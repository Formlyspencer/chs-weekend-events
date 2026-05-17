/* Charleston weekend events — browser-side scoring and customization.
 *
 * Data flow:
 *   1. Fetch events.json (built by main.py / v2_emit.py). Contains
 *      pre-computed keyword-match flags per event + project defaults.
 *   2. Load user prefs from localStorage (or seed from defaults).
 *   3. Score every event in the browser using the user's prefs.
 *   4. Dedupe by venue (already done in Python), filter, bucket, render.
 *   5. On any setting change → re-score → re-render → save prefs.
 */
(function () {
  "use strict";

  const STORAGE = {
    PREFS:  "chs_events_prefs_v1",
    HIDDEN: "chs_events_hidden_v1",
    SORT:   "chs_events_sort_v1",
  };

  // ---------------------------------------------------------------------
  // Storage helpers
  // ---------------------------------------------------------------------
  function loadJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }
  function saveJson(key, obj) {
    try { localStorage.setItem(key, JSON.stringify(obj)); } catch (_) {}
  }

  // ---------------------------------------------------------------------
  // Prefs: shape + merging with defaults
  // ---------------------------------------------------------------------
  function buildPrefsFromDefaults(defaults) {
    return {
      home: defaults.default_home,
      category_weights: Object.assign({}, defaults.category_weights),
      brand_boost: true,
      unique_strong_boost: true,
      unique_soft_boost: true,
      unique_skips_price: true,
      price_cap: 50,
      hide_over_cap: false,
      free_only: false,
      indoor_outdoor: "all",   // "all" | "outdoor" | "indoor"
      hide_drinking: false,
      show_kid_friendly: true,
      kid_only: false,
      hide_adult_only: false,
      kid_ages: [],            // ["toddler", "preschool", ...]
      max_per_venue: defaults.max_per_venue,
      max_per_category: Object.assign({}, defaults.max_per_category),
      tier_high: defaults.tier_high,
      tier_medium: defaults.tier_medium,
    };
  }

  // ---------------------------------------------------------------------
  // Scoring — direct port of score.py
  // ---------------------------------------------------------------------
  function locMult(neighborhood, prefs, defaults) {
    if (!neighborhood) return defaults.default_unknown_location_multiplier;
    const low = neighborhood.toLowerCase();
    const home = prefs.home || defaults.default_home;
    // First find the multiplier for the EVENT's neighborhood using the
    // standard table, then if user has a different home, recenter so that
    // home = 1.0 and everything else scales proportionally. Simple model.
    let raw = defaults.default_unknown_location_multiplier;
    for (const phrase of defaults.priority_areas) {
      if (low.indexOf(phrase) !== -1) {
        raw = defaults.location_multipliers[phrase] || raw;
        break;
      }
    }
    // If the user picked a non-Folly home, rescale: their home is 1.0, and
    // every other multiplier is `raw * (home_mult / their_home_mult)`.
    // Falling back to identity if numbers are weird.
    const homeMult = defaults.location_multipliers[home];
    if (!homeMult || homeMult === 1.0) return raw;
    // Folly was 1.0 baseline; rescale by 1/homeMult so user's home becomes 1.0
    return Math.min(1.0, raw / homeMult);
  }

  function priceMult(price, defaults) {
    if (price === null || price === undefined) {
      return defaults.price_curve[0][1];
    }
    for (let i = 1; i < defaults.price_curve.length; i++) {
      const [cap, mult] = defaults.price_curve[i];
      if (price <= cap) return mult;
    }
    return defaults.price_above_cap_multiplier;
  }

  function dayMult(start, defaults) {
    if (!start) return 0.5;
    const d = new Date(start);
    let weekday = d.getDay();           // 0 = Sun in JS
    weekday = (weekday + 6) % 7;        // shift to 0 = Mon
    const hour = d.getHours();
    const base = defaults.day_multipliers[String(weekday)] ?? 0.55;
    if (weekday === 4 && hour < 17) {   // Friday daytime
      return defaults.friday_daytime_multiplier;
    }
    return base;
  }

  function venueRoot(venue) {
    if (!venue) return "";
    return (venue.split(",")[0] || "").trim().toLowerCase();
  }

  function categorize(ev, prefs, defaults) {
    // Pick the category with the longest matching keyword (matches Python).
    let best = { key: null, weight: 0, len: 0 };
    const m = ev.matches;

    for (const [cat, kw] of (m.category_keywords || [])) {
      const w = (prefs.category_weights[cat] ?? defaults.category_weights[cat] ?? 0);
      if (!w) continue;
      if (kw.length > best.len || (kw.length === best.len && w > best.weight)) {
        best = { key: cat, weight: w, len: kw.length };
      }
    }
    if (best.key) {
      // Music keyword bump if event already classified as outdoor_music
      // and a preferred-music keyword matched.
      if (best.key === "outdoor_music" && (m.music_keywords || []).length) {
        best.weight = 1.0;
      }
      return best;
    }

    // Venue fallback (0.7x haircut).
    for (const [cat, kw] of (m.venue_categories || [])) {
      // outdoor_music_pref_venue and outdoor_music_hint_venue are virtual
      // entries — both indicate the venue is a music venue.
      const actualCat = (cat === "outdoor_music_pref_venue" || cat === "outdoor_music_hint_venue")
        ? "outdoor_music" : cat;
      const baseW = (prefs.category_weights[actualCat] ?? defaults.category_weights[actualCat] ?? 0);
      if (!baseW) continue;
      let w = baseW * 0.7;
      if (cat === "outdoor_music_pref_venue") w = 1.0 * 0.7;
      if (kw.length > best.len || (kw.length === best.len && w > best.weight)) {
        best = { key: actualCat, weight: w, len: kw.length };
      }
    }
    return best;
  }

  function isExcluded(ev, prefs, hiddenIds) {
    const m = ev.matches;
    if (hiddenIds && hiddenIds.has(ev.id)) return true;
    if (prefs.hide_adult_only && m.adult_only) return true;
    if (prefs.kid_only && (m.kid_friendly || []).length === 0) return true;
    if (!prefs.show_kid_friendly) {
      // Hide events whose ONLY appeal is the kid-friendly tag.
      if ((m.kid_friendly || []).length && m.unique_strong.length === 0
          && m.brand_keywords.length === 0) {
        return true;
      }
    }
    if (prefs.hide_over_cap && ev.price !== null && ev.price > prefs.price_cap) {
      return true;
    }
    if (prefs.free_only && ev.price !== 0) return true;
    if (prefs.hide_drinking) {
      const cat = ev._score && ev._score.category;
      if (cat === "brewery_event" || cat === "other_drinking") return true;
      if ((m.drinking_signals || []).length) return true;
    }
    if (prefs.indoor_outdoor !== "all") {
      const out = (m.outdoor_signals || []).length;
      const ind = (m.indoor_signals || []).length;
      if (prefs.indoor_outdoor === "outdoor") {
        // Drop events that clearly read as indoor and have no outdoor signal.
        if (ind && !out) return true;
      } else if (prefs.indoor_outdoor === "indoor") {
        if (out && !ind) return true;
      }
    }
    return false;
  }

  function scoreEvent(ev, prefs, defaults) {
    const cat = categorize(ev, prefs, defaults);
    if (!cat.key) return { score: 0, category: null, tier: "low" };

    let base = cat.weight;
    const m = ev.matches;

    // Brand boost (user-toggleable).
    if (prefs.brand_boost && (m.brand_keywords || []).length) {
      base = Math.max(base, 1.0);
    }

    // Uniqueness boosts.
    let strongUnique = false;
    if (prefs.unique_strong_boost && (m.unique_strong || []).length) {
      base = Math.max(base, 1.0);
      strongUnique = true;
    } else if (prefs.unique_soft_boost && (m.unique_soft || []).length) {
      base = Math.max(base, 0.85);
    }

    // Multipliers
    const day = dayMult(ev.start, defaults);
    const dist = locMult(ev.neighborhood, prefs, defaults);
    let repeat = 1.0;
    if ((m.attended_before || []).length) repeat = defaults.attended_before_dampen;

    let s;
    if (strongUnique && prefs.unique_skips_price) {
      s = base * day * dist * repeat;
    } else {
      s = base * day * priceMult(ev.price, defaults) * dist * repeat;
    }

    let tier = "low";
    if (s >= prefs.tier_high) tier = "high";
    else if (s >= prefs.tier_medium) tier = "medium";

    return { score: Math.round(s * 1000) / 1000, category: cat.key, tier: tier };
  }

  // ---------------------------------------------------------------------
  // Bucketing + dedup helpers
  // ---------------------------------------------------------------------
  function weekendRange(today, offset) {
    const wd = (today.getDay() + 6) % 7; // 0=Mon
    let daysToFri;
    if (wd <= 3)      daysToFri = 4 - wd;
    else if (wd === 4) daysToFri = 0;
    else               daysToFri = -(wd - 4);
    const fri = new Date(today);
    fri.setDate(today.getDate() + daysToFri + 7 * offset);
    const sun = new Date(fri);
    sun.setDate(fri.getDate() + 2);
    return [fri, sun];
  }

  function dateOnly(d) {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x;
  }

  function bucketize(events) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const [thisFri, thisSun] = weekendRange(today, 0);
    const [nextFri, nextSun] = weekendRange(today, 1);
    const out = {
      today: today.toISOString().slice(0, 10),
      this: { range: [thisFri, thisSun], events: [], recurring: [] },
      next: { range: [nextFri, nextSun], events: [], recurring: [] },
    };
    for (const ev of events) {
      if (!ev.start) continue;
      const d = dateOnly(ev.start);
      const isRecur = !!ev.matches.recurring;
      if (d >= thisFri && d <= thisSun) {
        (isRecur ? out.this.recurring : out.this.events).push(ev);
      } else if (d >= nextFri && d <= nextSun) {
        (isRecur ? out.next.recurring : out.next.events).push(ev);
      }
    }
    return out;
  }

  function collapseSameRun(events) {
    const seen = new Set();
    const out = [];
    for (const ev of events) {
      const t = (ev.title || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
      const v = venueRoot(ev.venue);
      if (!t || !v) { out.push(ev); continue; }
      const key = t + "|" + v;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(ev);
    }
    return out;
  }

  function capPerVenue(events, cap) {
    const counts = {};
    const out = [];
    for (const ev of events) {
      const v = venueRoot(ev.venue);
      if (!v) { out.push(ev); continue; }
      if ((counts[v] || 0) >= cap) continue;
      counts[v] = (counts[v] || 0) + 1;
      out.push(ev);
    }
    return out;
  }

  function capPerCategory(events, capMap) {
    const counts = {};
    const out = [];
    for (const ev of events) {
      const c = ev._score.category;
      if (capMap[c] !== undefined && (counts[c] || 0) >= capMap[c]) continue;
      counts[c] = (counts[c] || 0) + 1;
      out.push(ev);
    }
    return out;
  }

  function pickFeatured(events, n) {
    const picked = [];
    const venues = new Set();
    let usedMusic = false;
    for (const ev of events) {
      if (picked.length >= n) break;
      const v = venueRoot(ev.venue);
      const isMusic = ev._score.category === "outdoor_music";
      if (v && venues.has(v)) continue;
      if (isMusic && usedMusic) continue;
      picked.push(ev);
      if (v) venues.add(v);
      if (isMusic) usedMusic = true;
    }
    return picked;
  }

  // ---------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------
  const CAT_LABELS = {
    vintage_market:   "Vintage market",
    maker_market:     "Maker market",
    art_fair:         "Art / book fair",
    art_event:        "Art exhibit",
    film_screening:   "Film",
    surf_event:       "Surf",
    skate_event:      "Skate",
    car_show:         "Car show",
    farmers_market:   "Farmers market",
    food_festival:    "Food",
    brewery_event:    "Brewery",
    outdoor_festival: "Festival",
    outdoor_music:    "Outdoor music",
    other_drinking:   "Drinking",
  };
  const TIER_COLORS = { high: "#2d4332", medium: "#c8542a", low: "#8a7a5d" };
  const DAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, c => ({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    }[c]));
  }
  function escapeAttr(s) { return escapeHtml(s).replace(/`/g, "&#96;"); }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const wd = (d.getDay() + 6) % 7;
    const month = d.toLocaleString("en-US", { month: "short" });
    const dayNum = d.getDate();
    if (iso.indexOf("T") === -1) return `${DAYS[wd]} ${month} ${dayNum}`;
    let h = d.getHours();
    const m = d.getMinutes();
    const ampm = h >= 12 ? "pm" : "am";
    h = h % 12; if (h === 0) h = 12;
    const mins = String(m).padStart(2, "0");
    return `${DAYS[wd]} ${month} ${dayNum} · ${h}:${mins} ${ampm}`;
  }

  function formatPrice(p) {
    if (p === null || p === undefined) return "Price unknown";
    if (p === 0) return "Free";
    return "$" + Math.round(p);
  }

  function compactVenue(venue) {
    if (!venue) return "";
    const parts = venue.split(",").map(s => s.trim()).filter(Boolean);
    if (!parts.length) return "";
    const first = parts[0];
    if (/^\d/.test(first)) {
      for (let i = 1; i < parts.length; i++) {
        const p = parts[i].toLowerCase().replace(/\./g, "").trim();
        if (p === "sc" || p === "south carolina") break;
        return first + ", " + parts[i];
      }
      return first;
    }
    return first;
  }

  function eventCardHTML(ev, isHero) {
    const sc = ev._score;
    const color = TIER_COLORS[sc.tier] || "#8a7a5d";
    const cat = CAT_LABELS[sc.category] || sc.category || "—";
    const venueShort = compactVenue(ev.venue);
    const neighborhood = ev.neighborhood || "";
    const loc = [
      venueShort && escapeHtml(venueShort),
      neighborhood && neighborhood.toLowerCase() !== (venueShort || "").toLowerCase()
        ? escapeHtml(neighborhood) : null,
    ].filter(Boolean).join('</span><span class="loc">');

    // Calendar icon — clicking downloads an .ics file (Apple Calendar /
    // Outlook / any iCal-compatible). Google Calendar option is still
    // available inside the expanded body.
    const calIconHtml = `<button class="cal-icon-btn" data-cal="ics" data-id="${escapeAttr(ev.id)}" title="Add to calendar (.ics)">📅</button>`;

    const titleLink = ev.url
      ? `<a href="${escapeAttr(ev.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(ev.title)}</a>`
      : escapeHtml(ev.title);

    const venueFullHtml = (ev.venue && ev.venue.trim() !== venueShort)
      ? `<div class="venue-full">${escapeHtml(ev.venue)}</div>` : "";

    return `
      <details class="event ${isHero ? "hero" : ""}" style="--tier:${color}" data-id="${escapeAttr(ev.id)}">
        <summary>
          <div class="event-meta">
            <span class="tier-dot"></span>
            <span class="cat">${escapeHtml(cat)}</span>
            <span>${escapeHtml(formatDate(ev.start))}</span>
            <span>${escapeHtml(formatPrice(ev.price))}</span>
            <span class="score" title="Match score (0–1)">${sc.score.toFixed(2)}</span>
          </div>
          <div class="event-title-row">
            <span class="title">${titleLink}</span>
            ${loc ? `<span class="loc">${loc}</span>` : ""}
          </div>
          ${calIconHtml}
        </summary>
        <div class="event-body">
          ${venueFullHtml}
          ${ev.description ? `<p class="desc">${escapeHtml(ev.description)}</p>` : ""}
          <div class="event-foot">
            <span class="src">via ${escapeHtml(ev.source || "")}</span>
            <div class="cal-buttons">
              <button class="cal-btn" data-cal="ics" data-id="${escapeAttr(ev.id)}">+ Calendar (.ics)</button>
              <button class="cal-btn" data-cal="gcal" data-id="${escapeAttr(ev.id)}">+ Google Calendar</button>
              ${ev.url ? `<a class="cal-btn" href="${escapeAttr(ev.url)}" target="_blank" rel="noopener">Visit event page →</a>` : ""}
              <button class="cal-btn" data-hide="${escapeAttr(ev.id)}" style="color:var(--muted)">Don't show again</button>
            </div>
          </div>
        </div>
      </details>`;
  }

  function renderTab(panel, bucket, label, maxPerVenue, maxPerCategory) {
    let evs = collapseSameRun(bucket.events);
    evs = capPerVenue(evs, maxPerVenue);
    evs = capPerCategory(evs, maxPerCategory);

    let recur = collapseSameRun(bucket.recurring);
    recur = capPerVenue(recur, maxPerVenue);
    recur = capPerCategory(recur, maxPerCategory);

    if (!evs.length && !recur.length) {
      panel.innerHTML = `<div class="empty">No events match your filters for ${label}.</div>`;
      return;
    }

    const top = pickFeatured(evs, 3);
    const topIds = new Set(top.map(e => e.id));
    const rest = evs.filter(e => !topIds.has(e.id));

    let html = "";
    if (top.length) {
      html += `<h2>Top picks — ${label}</h2>`;
      html += top.map(e => eventCardHTML(e, true)).join("");
    }
    if (rest.length) {
      html += `<h2>More for ${label}</h2>`;
      html += rest.map(e => eventCardHTML(e, false)).join("");
    }
    if (recur.length) {
      html += `
        <details class="recurring-section">
          <summary><h2>Routine weekly / monthly — ${label} (${recur.length})</h2></summary>
          <div class="recurring-list">${recur.map(e => eventCardHTML(e, false)).join("")}</div>
        </details>`;
    }
    panel.innerHTML = html;
  }

  // ---------------------------------------------------------------------
  // Calendar export
  // ---------------------------------------------------------------------
  function icsEscape(s) {
    return (s || "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n")
      .replace(/,/g, "\\,").replace(/;/g, "\\;");
  }
  function asIcsDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const pad = (n) => String(n).padStart(2, "0");
    if (iso.indexOf("T") === -1) {
      return `${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}`;
    }
    return `${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}T` +
           `${pad(d.getHours())}${pad(d.getMinutes())}00`;
  }
  function eventToIcs(ev) {
    const dt = asIcsDate(ev.start);
    const dtEnd = ev.end ? asIcsDate(ev.end) :
      (ev.start && ev.start.indexOf("T") !== -1
        ? asIcsDate(new Date(new Date(ev.start).getTime() + 2*3600*1000).toISOString())
        : dt);
    const lines = [
      "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//chs-weekend-events//EN",
      "CALSCALE:GREGORIAN",
      "BEGIN:VEVENT",
      `UID:${ev.id}@chs-weekend-events`,
      `DTSTAMP:${asIcsDate(new Date().toISOString())}`,
      ev.start.indexOf("T") === -1
        ? `DTSTART;VALUE=DATE:${dt}`
        : `DTSTART:${dt}`,
      ev.end || ev.start.indexOf("T") !== -1
        ? (ev.start.indexOf("T") === -1 ? `DTEND;VALUE=DATE:${dtEnd}` : `DTEND:${dtEnd}`)
        : "",
      `SUMMARY:${icsEscape(ev.title)}`,
      ev.venue ? `LOCATION:${icsEscape(ev.venue)}` : "",
      ev.description ? `DESCRIPTION:${icsEscape(ev.description)}` : "",
      ev.url ? `URL:${icsEscape(ev.url)}` : "",
      "END:VEVENT", "END:VCALENDAR",
    ];
    return lines.filter(Boolean).join("\r\n");
  }
  function downloadIcs(ev) {
    const blob = new Blob([eventToIcs(ev)], { type: "text/calendar" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const safeName = (ev.title || "event").replace(/[^a-z0-9]+/gi, "-").toLowerCase();
    a.href = url; a.download = `${safeName}.ics`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
  function gcalUrl(ev) {
    const dt = asIcsDate(ev.start);
    const dtEnd = ev.end ? asIcsDate(ev.end) :
      (ev.start && ev.start.indexOf("T") !== -1
        ? asIcsDate(new Date(new Date(ev.start).getTime() + 2*3600*1000).toISOString())
        : dt);
    const params = new URLSearchParams({
      action: "TEMPLATE",
      text: ev.title || "",
      dates: `${dt}/${dtEnd}`,
      details: (ev.description || "") + (ev.url ? `\n\n${ev.url}` : ""),
      location: ev.venue || "",
    });
    return "https://www.google.com/calendar/render?" + params.toString();
  }

  // ---------------------------------------------------------------------
  // Settings drawer
  // ---------------------------------------------------------------------
  function populateHomes(selectEl, defaults) {
    const items = defaults.priority_areas;
    selectEl.innerHTML = items.map(a =>
      `<option value="${escapeAttr(a)}">${escapeHtml(a.replace(/\b\w/g, c => c.toUpperCase()))}</option>`
    ).join("");
  }

  function populateCategorySliders(container, prefs, defaults) {
    container.innerHTML = Object.keys(defaults.category_weights).map(cat => {
      const w = prefs.category_weights[cat] ?? defaults.category_weights[cat];
      return `
        <div class="settings-row">
          <label>${escapeHtml(CAT_LABELS[cat] || cat)}</label>
          <input type="range" data-cat-w="${cat}" min="0" max="1" step="0.05" value="${w}">
          <span class="val" id="catw-${cat}-val">${w.toFixed(2)}</span>
        </div>`;
    }).join("");
  }

  // ---------------------------------------------------------------------
  // Bootstrapping
  // ---------------------------------------------------------------------
  let RAW = null;          // events.json contents
  let PREFS = null;        // user prefs object
  let HIDDEN = new Set();  // set of hidden event ids ("not interested")
  let ACTIVE_TAB = "this";
  let SORT_MODE = "score"; // "score" | "date"

  function applyPrefsToScore() {
    if (!RAW) return [];
    const out = [];
    for (const ev of RAW.events) {
      const sc = scoreEvent(ev, PREFS, RAW.defaults);
      if (sc.category === null) continue;     // uncategorized → drop
      const copy = Object.assign({}, ev, { _score: sc });
      // isExcluded reads _score (for drinking category filter), so attach
      // before checking.
      if (isExcluded(copy, PREFS, HIDDEN)) continue;
      out.push(copy);
    }
    if (SORT_MODE === "date") {
      out.sort((a, b) => (a.start || "").localeCompare(b.start || ""));
    } else {
      out.sort((a, b) => {
        if (b._score.score !== a._score.score) return b._score.score - a._score.score;
        return (a.start || "").localeCompare(b.start || "");
      });
    }
    return out;
  }

  function render() {
    if (!RAW) return;
    const scored = applyPrefsToScore();
    const buckets = bucketize(scored);

    // Tabs
    const tabsEl = document.getElementById("tabs");
    const fmt = (d) => DAYS[(d.getDay()+6)%7] + " " + d.toLocaleString("en-US",{month:"short", day:"numeric"});
    tabsEl.innerHTML = [
      ["this", "This weekend", buckets.this.range],
      ["next", "Next weekend", buckets.next.range],
    ].map(([key, label, [start, end]]) => `
      <button class="tab ${ACTIVE_TAB===key?"active":""}" data-tab="${key}">
        ${label}<span class="tab-range">${fmt(start)} – ${fmt(end)}</span>
      </button>
    `).join("");

    const maxArt = PREFS.max_per_category.art_event ?? 2;
    const capMap = Object.assign({}, PREFS.max_per_category, { art_event: maxArt });

    renderTab(document.getElementById("panel-this"), buckets.this,
              "this weekend", PREFS.max_per_venue, capMap);
    renderTab(document.getElementById("panel-next"), buckets.next,
              "next weekend", PREFS.max_per_venue, capMap);

    // Reflect active tab visibility
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    document.getElementById(`panel-${ACTIVE_TAB}`).classList.add("active");
  }

  // ---------------------------------------------------------------------
  // Wire up UI
  // ---------------------------------------------------------------------
  function wireSettings(defaults) {
    const drawer = document.getElementById("settings-drawer");
    const overlay = document.getElementById("settings-overlay");
    function open()  { drawer.classList.add("open"); overlay.classList.add("open"); }
    function close() { drawer.classList.remove("open"); overlay.classList.remove("open"); }
    document.getElementById("settings-btn").addEventListener("click", open);
    document.getElementById("settings-close").addEventListener("click", close);
    overlay.addEventListener("click", close);

    populateHomes(document.getElementById("pref-home"), defaults);
    populateCategorySliders(document.getElementById("pref-categories"), PREFS, defaults);

    function syncFromPrefs() {
      document.getElementById("pref-home").value = PREFS.home;
      document.getElementById("pref-free-only").checked = PREFS.free_only;
      document.getElementById("pref-indoor-outdoor").value = PREFS.indoor_outdoor;
      document.getElementById("pref-hide-drinking").checked = PREFS.hide_drinking;
      document.getElementById("pref-brand-boost").checked = PREFS.brand_boost;
      document.getElementById("pref-unique-strong-boost").checked = PREFS.unique_strong_boost;
      document.getElementById("pref-unique-soft-boost").checked = PREFS.unique_soft_boost;
      document.getElementById("pref-unique-skips-price").checked = PREFS.unique_skips_price;
      document.getElementById("pref-show-kid-friendly").checked = PREFS.show_kid_friendly;
      document.getElementById("pref-hide-adult-only").checked = PREFS.hide_adult_only;
      document.getElementById("pref-kid-only").checked = PREFS.kid_only;
      document.querySelectorAll("[data-age]").forEach(el => {
        el.checked = PREFS.kid_ages.indexOf(el.dataset.age) !== -1;
      });
      document.getElementById("pref-price-cap").value = PREFS.price_cap;
      document.getElementById("pref-price-cap-val").textContent = "$" + PREFS.price_cap;
      document.getElementById("pref-hide-over-cap").checked = PREFS.hide_over_cap;
      document.getElementById("pref-max-venue").value = PREFS.max_per_venue;
      document.getElementById("pref-max-venue-val").textContent = PREFS.max_per_venue;
      const maxArt = PREFS.max_per_category.art_event ?? 2;
      document.getElementById("pref-max-art").value = maxArt;
      document.getElementById("pref-max-art-val").textContent = maxArt;
      document.getElementById("pref-tier-high").value = PREFS.tier_high;
      document.getElementById("pref-tier-high-val").textContent = PREFS.tier_high.toFixed(2);
      document.querySelectorAll("[data-cat-w]").forEach(el => {
        const cat = el.dataset.catW;
        const w = PREFS.category_weights[cat] ?? defaults.category_weights[cat];
        el.value = w;
        const v = document.getElementById("catw-"+cat+"-val");
        if (v) v.textContent = w.toFixed(2);
      });
    }
    syncFromPrefs();

    function commit() {
      saveJson(STORAGE.PREFS, PREFS);
      render();
    }

    drawer.addEventListener("input", (e) => {
      const t = e.target;
      if (t.id === "pref-home") PREFS.home = t.value;
      else if (t.id === "pref-free-only") PREFS.free_only = t.checked;
      else if (t.id === "pref-indoor-outdoor") PREFS.indoor_outdoor = t.value;
      else if (t.id === "pref-hide-drinking") PREFS.hide_drinking = t.checked;
      else if (t.id === "pref-brand-boost") PREFS.brand_boost = t.checked;
      else if (t.id === "pref-unique-strong-boost") PREFS.unique_strong_boost = t.checked;
      else if (t.id === "pref-unique-soft-boost") PREFS.unique_soft_boost = t.checked;
      else if (t.id === "pref-unique-skips-price") PREFS.unique_skips_price = t.checked;
      else if (t.id === "pref-show-kid-friendly") PREFS.show_kid_friendly = t.checked;
      else if (t.id === "pref-hide-adult-only") PREFS.hide_adult_only = t.checked;
      else if (t.id === "pref-kid-only") PREFS.kid_only = t.checked;
      else if (t.dataset.age !== undefined) {
        const a = t.dataset.age;
        if (t.checked && PREFS.kid_ages.indexOf(a) === -1) PREFS.kid_ages.push(a);
        else if (!t.checked) PREFS.kid_ages = PREFS.kid_ages.filter(x => x !== a);
      }
      else if (t.id === "pref-price-cap") {
        PREFS.price_cap = parseInt(t.value, 10);
        document.getElementById("pref-price-cap-val").textContent = "$" + PREFS.price_cap;
      }
      else if (t.id === "pref-hide-over-cap") PREFS.hide_over_cap = t.checked;
      else if (t.id === "pref-max-venue") {
        PREFS.max_per_venue = parseInt(t.value, 10);
        document.getElementById("pref-max-venue-val").textContent = PREFS.max_per_venue;
      }
      else if (t.id === "pref-max-art") {
        PREFS.max_per_category.art_event = parseInt(t.value, 10);
        document.getElementById("pref-max-art-val").textContent = t.value;
      }
      else if (t.id === "pref-tier-high") {
        PREFS.tier_high = parseFloat(t.value);
        document.getElementById("pref-tier-high-val").textContent = PREFS.tier_high.toFixed(2);
      }
      else if (t.dataset.catW !== undefined) {
        const cat = t.dataset.catW;
        PREFS.category_weights[cat] = parseFloat(t.value);
        document.getElementById("catw-"+cat+"-val").textContent = t.value;
      }
      else return;
      commit();
    });

    document.getElementById("pref-reset").addEventListener("click", () => {
      PREFS = buildPrefsFromDefaults(defaults);
      saveJson(STORAGE.PREFS, PREFS);
      syncFromPrefs();
      render();
    });

    document.getElementById("pref-unhide-all").addEventListener("click", () => {
      HIDDEN.clear();
      saveJson(STORAGE.HIDDEN, []);
      refreshHiddenListUI();
      render();
    });
  }

  // Update the hidden-events list panel in the settings drawer.
  function refreshHiddenListUI() {
    const wrap = document.getElementById("hidden-list");
    const unhideAll = document.getElementById("pref-unhide-all");
    if (!RAW || !HIDDEN.size) {
      wrap.textContent = "None hidden.";
      unhideAll.style.display = "none";
      return;
    }
    const items = [];
    for (const id of HIDDEN) {
      const ev = RAW.events.find(x => x.id === id);
      const title = ev ? ev.title : "(unknown)";
      items.push(
        `<div style="display:flex;justify-content:space-between;gap:8px;align-items:center;margin:4px 0">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(title.slice(0,40))}</span>
          <button class="cal-btn" data-unhide="${escapeAttr(id)}" style="color:var(--accent)">unhide</button>
        </div>`
      );
    }
    wrap.innerHTML = items.join("");
    unhideAll.style.display = "inline-block";
  }

  function wireOtherUI() {
    const tabsEl = document.getElementById("tabs");
    tabsEl.addEventListener("click", (e) => {
      const btn = e.target.closest(".tab");
      if (!btn) return;
      ACTIVE_TAB = btn.dataset.tab;
      render();
    });

    // Capture-phase click delegation: runs BEFORE inline stopPropagation
    // so the calendar/hide/unhide buttons inside <summary> still fire
    // without prematurely opening the card.
    document.addEventListener("click", (e) => {
      const cal = e.target.closest("[data-cal]");
      if (cal) {
        e.stopPropagation();   // don't toggle the parent <details>
        const id = cal.dataset.id;
        const kind = cal.dataset.cal;
        const ev = RAW.events.find(x => x.id === id);
        if (!ev) return;
        if (kind === "ics") {
          downloadIcs(ev);
          cal.classList.add("added");
          setTimeout(() => cal.classList.remove("added"), 1500);
        } else if (kind === "gcal") {
          window.open(gcalUrl(ev), "_blank", "noopener");
        }
        return;
      }
      const hideBtn = e.target.closest("[data-hide]");
      if (hideBtn) {
        e.stopPropagation();
        HIDDEN.add(hideBtn.dataset.hide);
        saveJson(STORAGE.HIDDEN, Array.from(HIDDEN));
        refreshHiddenListUI();
        render();
        return;
      }
      const unhideBtn = e.target.closest("[data-unhide]");
      if (unhideBtn) {
        e.stopPropagation();
        HIDDEN.delete(unhideBtn.dataset.unhide);
        saveJson(STORAGE.HIDDEN, Array.from(HIDDEN));
        refreshHiddenListUI();
        render();
        return;
      }
    }, true);

    // Sort dropdown
    const sortSelect = document.getElementById("sort-mode");
    sortSelect.value = SORT_MODE;
    sortSelect.addEventListener("change", (e) => {
      SORT_MODE = e.target.value;
      try { localStorage.setItem(STORAGE.SORT, SORT_MODE); } catch (_) {}
      render();
    });

  }

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------
  async function boot() {
    try {
      const r = await fetch("events.json", { cache: "no-store" });
      RAW = await r.json();
    } catch (err) {
      document.getElementById("panel-this").innerHTML =
        `<div class="empty">Couldn't load events.json: ${escapeHtml(String(err))}</div>`;
      return;
    }
    document.getElementById("updated-line").textContent =
      `Updated ${RAW.generated_at} · refreshes every 3h`;

    const storedPrefs = loadJson(STORAGE.PREFS, null);
    PREFS = storedPrefs || buildPrefsFromDefaults(RAW.defaults);
    // Merge in any new keys defaults may have added since the last save.
    const fresh = buildPrefsFromDefaults(RAW.defaults);
    for (const k of Object.keys(fresh)) {
      if (PREFS[k] === undefined) PREFS[k] = fresh[k];
    }

    HIDDEN  = new Set(loadJson(STORAGE.HIDDEN,  []) || []);
    try { SORT_MODE = localStorage.getItem(STORAGE.SORT) || "score"; } catch (_) {}

    wireSettings(RAW.defaults);
    wireOtherUI();
    refreshHiddenListUI();
    render();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
