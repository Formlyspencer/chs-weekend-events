"""Score, categorize, and bucket events into this/next weekend + weekdays."""
from __future__ import annotations

import re
from datetime import datetime, date, timedelta

import config


def _tokens(text: str) -> set[str]:
    """Lowercase word tokens, ignoring very short / stopword-ish tokens."""
    stop = {
        "the", "a", "an", "and", "or", "of", "in", "at", "to", "for", "on",
        "with", "by", "from", "is", "are", "be", "this", "that", "your",
        "you", "we", "our", "us", "it", "as", "will", "all",
    }
    out: set[str] = set()
    for raw in text.lower().split():
        word = "".join(c for c in raw if c.isalnum())
        if len(word) >= 4 and word not in stop:
            out.add(word)
    return out


# ---------------------------------------------------------------------------
# Recurring event detection. Trumba expands recurring events into individual
# VEVENT entries (no RRULE), so we rely on title/description language. Things
# that run "every Saturday" or "the third Sunday of the month" shouldn't fight
# one-off events for the featured slots.
# ---------------------------------------------------------------------------
_DAY_RE = r"(mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?)"

_RECURRING_PATTERNS = [
    re.compile(rf"\bevery\s+{_DAY_RE}\b", re.IGNORECASE),
    re.compile(rf"\beach\s+{_DAY_RE}\b", re.IGNORECASE),
    # "Saturdays from 9am..." / "Sundays at 11" — require the PLURAL day name
    # so we don't false-positive on "Saturday at Firefly Distillery" (where
    # "at" is just a preposition for location, not a recurring schedule).
    re.compile(r"\b(mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays)\s+(from|at|noon|night|morning|afternoon|evening|@|beginning|through)\b", re.IGNORECASE),
    re.compile(rf"\b(first|second|third|fourth|last)\s+{_DAY_RE}\s+of\b", re.IGNORECASE),
    re.compile(r"\b(weekly|bi-?weekly|monthly|bi-?monthly|quarterly)\b", re.IGNORECASE),
    re.compile(r"\bevery\s+other\b", re.IGNORECASE),
    re.compile(r"\ball\s+year\s+long\b", re.IGNORECASE),
    re.compile(r"\byear[-\s]round\b", re.IGNORECASE),
    # Farmers markets are reliably weekly — saves having to find the schedule
    # text in the description.
    re.compile(r"\bfarmer'?s\s+market\b", re.IGNORECASE),
    # Brewery/bar standing events
    re.compile(r"\btrivia\s+night\b", re.IGNORECASE),
    re.compile(r"\brun\s+club\b", re.IGNORECASE),
    re.compile(r"\byappy\s+hour\b", re.IGNORECASE),
    re.compile(r"\bopen\s+mic\b", re.IGNORECASE),
    re.compile(r"\bline\s+dancing\b", re.IGNORECASE),
    re.compile(r"\bkaraoke\s+night\b", re.IGNORECASE),
]


def is_recurring(ev: dict) -> bool:
    """True if the title/description signals a standing weekly/monthly event."""
    haystack = " ".join(filter(None, [ev.get("title"), ev.get("description")]))
    return any(p.search(haystack) for p in _RECURRING_PATTERNS)


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _overlap(a: str, b: str) -> float:
    """Overlap coefficient: |A ∩ B| / min(|A|, |B|). Robust when one side is short."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _to_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v


def _to_hour(v) -> int | None:
    if isinstance(v, datetime):
        return v.hour
    return None


import re as _re


def _word_boundary(needle: str) -> _re.Pattern:
    # Multi-word phrases use a relaxed boundary (just \b); single short words
    # require a word boundary on both sides to avoid "fest" matching "manifest".
    return _re.compile(r"\b" + _re.escape(needle) + r"\b", _re.IGNORECASE)


_NEEDLE_CACHE: dict[str, _re.Pattern] = {}


def _matches_any(text: str, needles) -> bool:
    for n in needles:
        pat = _NEEDLE_CACHE.get(n)
        if pat is None:
            pat = _word_boundary(n)
            _NEEDLE_CACHE[n] = pat
        if pat.search(text):
            return True
    return False


def _longest_match(text: str, keywords) -> int:
    """Return the length (in chars) of the longest matching keyword, or 0."""
    best = 0
    for n in keywords:
        pat = _NEEDLE_CACHE.get(n)
        if pat is None:
            pat = _word_boundary(n)
            _NEEDLE_CACHE[n] = pat
        if pat.search(text):
            if len(n) > best:
                best = len(n)
    return best


def categorize(ev: dict) -> tuple[str | None, float]:
    """Return (category_key, weight).

    Categorization rule: the category whose LONGEST matching keyword is
    longest wins (more specific phrases beat generic single words). Weight
    is then taken from that category's `weight` for scoring.

    Title + description determine the category. Venue is only a fallback
    when nothing in the title/description matched any category.

    For outdoor_music specifically, a preferred-venue match in MUSIC_KEYWORDS
    bumps weight to 1.0 when the event already classified as outdoor_music.
    """
    title_desc = " ".join(filter(None, [
        ev.get("title"), ev.get("description"),
    ])).lower()
    venue = (ev.get("venue") or "").lower()

    # Pass 1: title + description — pick the category whose longest matching
    # keyword is longest. Tie-break by weight.
    best: tuple[str | None, float, int] = (None, 0.0, 0)
    for key, spec in config.CATEGORIES.items():
        match_len = _longest_match(title_desc, spec["keywords"])
        if match_len == 0:
            continue
        w = spec["weight"]
        if key == "outdoor_music" and config.MUSIC_KEYWORDS and (
            _matches_any(title_desc, config.MUSIC_KEYWORDS)
            or _matches_any(venue, config.MUSIC_KEYWORDS)
        ):
            w = 1.0
        if (match_len, w) > (best[2], best[1]):
            best = (key, w, match_len)
    if best[0] is not None:
        return (best[0], best[1])

    # Pass 2: venue fallback — same longest-match rule, 0.7x haircut.
    # For outdoor_music we use TWO venue lists:
    #   MUSIC_KEYWORDS      — preferred venues, full 1.0 bump (haircut applied)
    #   MUSIC_VENUE_HINTS   — other music venues, category-only, no bump
    music_venue_hints = getattr(config, "MUSIC_VENUE_HINTS", []) or []
    for key, spec in config.CATEGORIES.items():
        match_len = _longest_match(venue, spec["keywords"])
        if key == "outdoor_music":
            if config.MUSIC_KEYWORDS:
                m = _longest_match(venue, config.MUSIC_KEYWORDS)
                if m > match_len:
                    match_len = m
            if music_venue_hints:
                m = _longest_match(venue, music_venue_hints)
                if m > match_len:
                    match_len = m
        if match_len == 0:
            continue
        w = spec["weight"] * 0.7
        if key == "outdoor_music" and config.MUSIC_KEYWORDS and _matches_any(
            venue, config.MUSIC_KEYWORDS
        ):
            w = 1.0 * 0.7  # preferred-venue 1.0 bump, with venue-only haircut
        # Note: MUSIC_VENUE_HINTS does NOT trigger the bump — it only routes
        # the event to outdoor_music at the category's base weight.
        if (match_len, w) > (best[2], best[1]):
            best = (key, w, match_len)
    return (best[0], best[1])


def is_excluded(ev: dict) -> bool:
    text = " ".join(filter(None, [
        ev.get("title"), ev.get("description"),
    ])).lower()
    return _matches_any(text, config.EXCLUDED_KEYWORDS)


def score(ev: dict) -> dict:
    """Mutate-and-return ev with category/score/tier filled in."""
    cat, weight = categorize(ev)
    d = _to_date(ev["start"])
    h = _to_hour(ev["start"])

    base = weight  # 0 if no category matched

    # Day-of-week multiplier
    if d is not None:
        day_mult = config.day_multiplier(d.weekday(), h)
    else:
        day_mult = 0.5

    # Price multiplier
    price_mult = config.price_multiplier(ev.get("price"))

    # Distance multiplier — Spencer is in Folly Beach
    distance_mult = config.location_multiplier(ev.get("neighborhood"))

    # Attended-before dampen
    title_low = (ev.get("title") or "").lower()
    if any(att in title_low for att in config.ATTENDED_BEFORE):
        repeat_mult = config.ATTENDED_BEFORE_DAMPEN
    else:
        repeat_mult = 1.0

    # Brand boost — events that mention a brand Spencer follows get
    # category weight floored at 1.0 (so a Hed Hi screening or McKevlin's
    # surf comp lands in the top tier even if its category weight is lower).
    haystack = " ".join(filter(None, [
        ev.get("title"), ev.get("description"), ev.get("venue"),
    ])).lower()
    if getattr(config, "BRAND_KEYWORDS", None) and _matches_any(haystack, config.BRAND_KEYWORDS):
        base = max(base, 1.0)

    # Uniqueness boost — rare events deserve a bump because you don't see
    # them every weekend. Two tiers:
    #   strong (annual/biennial/inaugural/anniversary/...): floors base at 1.0
    #     AND bypasses the price multiplier. Rare expensive events are often
    #     that way because they're charity/special-occasion, and penalizing
    #     them on price defeats the rarity boost. Day and distance still apply.
    #   soft (gala/fundraiser/debut): floors base at 0.85, normal multipliers.
    strong_unique = bool(
        getattr(config, "UNIQUE_KEYWORDS", None)
        and _matches_any(haystack, config.UNIQUE_KEYWORDS)
    )
    if strong_unique:
        base = max(base, 1.0)
    elif getattr(config, "UNIQUE_KEYWORDS_SOFT", None) and _matches_any(haystack, config.UNIQUE_KEYWORDS_SOFT):
        base = max(base, 0.85)

    if strong_unique:
        s = base * day_mult * distance_mult * repeat_mult
    else:
        s = base * day_mult * price_mult * distance_mult * repeat_mult
    if s >= config.TIER_HIGH:
        tier = "high"
    elif s >= config.TIER_MEDIUM:
        tier = "medium"
    else:
        tier = "low"

    ev["category"] = cat
    ev["score"] = round(s, 3)
    ev["tier"] = tier
    return ev


def _venue_root(venue: str | None) -> str | None:
    if not venue:
        return None
    # Strip street address; keep just the venue name.
    return venue.split(",")[0].strip().lower() or None


def _is_duplicate(a: dict, b: dict) -> bool:
    """Two events are the same if they're at the same venue at the same
    time, full stop. Same venue + close-enough start time = duplicate,
    regardless of title or description content.

    Why the time-window dance: Trumba and CHStoday newsletters give us the
    same event with slightly different titles ("Front Paige Media" vs.
    "Firefly Vendor Village & Book Fair") and identical start times. The
    only thing that legitimately distinguishes two events at the same venue
    on the same day is the time slot (12 pm trivia vs. 8 pm concert).
    """
    va, vb = _venue_root(a.get("venue")), _venue_root(b.get("venue"))
    if not va or va != vb:
        return False

    sa, sb = a.get("start"), b.get("start")
    da, db = _to_date(sa), _to_date(sb)
    if da is None or da != db:
        return False

    # If both have a full datetime, use the time-of-day distance.
    if isinstance(sa, datetime) and isinstance(sb, datetime):
        diff_sec = abs((sa - sb).total_seconds())
        if diff_sec <= 60:                  # same minute → same event, always
            return True
        if diff_sec >= 2 * 3600:            # ≥2h apart → clearly distinct sessions
            return False
        # 1-120 min apart: ambiguous — fall through to title/desc check.

    # Title / description match (used when at least one side is date-only,
    # or when datetimes are within a 2-hour window).
    ta = (a.get("title") or "").lower()
    tb = (b.get("title") or "").lower()
    desca = (a.get("description") or "").lower()
    descb = (b.get("description") or "").lower()

    if ta and tb and (ta in tb or tb in ta):
        return True
    if ta and ta in descb:
        return True
    if tb and tb in desca:
        return True
    if desca and descb:
        if _jaccard(desca, descb) >= 0.30 or _overlap(desca, descb) >= 0.50:
            return True
    return False


def _prefer(a: dict, b: dict) -> dict:
    """Which of two duplicate events to keep — higher score, then more
    descriptive title (longer + not equal to venue name), then more populated.
    """
    # Higher score wins.
    if (a.get("score") or 0) != (b.get("score") or 0):
        return a if (a.get("score") or 0) > (b.get("score") or 0) else b
    # Penalize titles that look like the host (= venue name).
    va = _venue_root(a.get("venue")) or ""
    vb = _venue_root(b.get("venue")) or ""
    ta = (a.get("title") or "").lower()
    tb = (b.get("title") or "").lower()
    if (ta in va) != (tb in vb):
        return b if ta in va else a
    # Longer title wins.
    if len(ta) != len(tb):
        return a if len(ta) > len(tb) else b
    # Tie-break: more populated fields.
    fa = sum(int(bool(a.get(f))) for f in ("price", "url", "neighborhood", "description"))
    fb = sum(int(bool(b.get(f))) for f in ("price", "url", "neighborhood", "description"))
    return a if fa >= fb else b


def pick_featured(events: list[dict], n: int = 3) -> list[dict]:
    """Pick the top N events with diversity constraints:

      - At most one outdoor_music event in the featured row.
      - At most one event per venue (so we don't double-feature the same place).

    Events are passed in score order; the rest are returned in
    `pick_featured_rest` (whatever fell through, still in score order).
    """
    picked: list[dict] = []
    used_venues: set[str] = set()
    used_music = False
    for ev in events:
        if len(picked) >= n:
            break
        venue = _venue_root(ev.get("venue"))
        is_music = ev.get("category") == "outdoor_music"
        if venue and venue in used_venues:
            continue
        if is_music and used_music:
            continue
        picked.append(ev)
        if venue:
            used_venues.add(venue)
        if is_music:
            used_music = True
    return picked


# Diagnostic record accumulated during the most recent dedup pass.
# Read by main.py and written to docs/events.json so we can post-mortem
# CI behavior without needing access to workflow logs.
DEDUP_DEBUG: list[str] = []


def _dedupe_by_venue_date(events: list[dict]) -> list[dict]:
    DEDUP_DEBUG.clear()
    DEDUP_DEBUG.append(f"input_count={len(events)}")

    # Group every event by (venue, date) so we can see the raw clustering
    # before dedup logic runs.
    pre_groups: dict[tuple, list[str]] = {}
    for ev in events:
        v = _venue_root(ev.get("venue"))
        d = _to_date(ev["start"])
        if v and d:
            pre_groups.setdefault((v, d.isoformat()), []).append(
                f"{(ev.get('title') or '')[:35]}@{ev.get('start')}|src={ev.get('source')}"
            )
    for key, members in pre_groups.items():
        if len(members) > 1:
            DEDUP_DEBUG.append(f"pre_dup_group venue={key[0]} date={key[1]}: {members}")

    out: list[dict] = []
    for ev in events:
        merged = False
        for i, kept in enumerate(out):
            try:
                dup = _is_duplicate(ev, kept)
            except Exception as e:
                DEDUP_DEBUG.append(f"_is_duplicate raised: {type(e).__name__}: {e}")
                dup = False
            if dup:
                winner = _prefer(ev, kept)
                msg = (
                    f"merge '{(ev.get('title') or '')[:35]}' + "
                    f"'{(kept.get('title') or '')[:35]}' "
                    f"@ {_venue_root(ev.get('venue'))} "
                    f"-> kept '{(winner.get('title') or '')[:35]}'"
                )
                DEDUP_DEBUG.append(msg)
                print("DEDUP " + msg, flush=True)
                out[i] = winner
                merged = True
                break
        if not merged:
            out.append(ev)

    # Consolidation pass: a single-pass dedup misses chains. Example:
    #   out starts empty. We add N (newsletter "Front Paige Media Book Fair").
    #   Then FVV ("Firefly Vendor Village & Book Fair", Trumba) arrives —
    #   different title, no description overlap → appended, out=[N, FVV].
    #   Then FPM (Trumba "Front Paige Media") arrives — matches N via
    #   title-substring → merged with N (replaces slot 0), then we BREAK.
    #   FPM and FVV both stay in out, never compared against each other.
    # Re-running dedup over the survivors catches the FPM-vs-FVV match.
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(out):
            j = i + 1
            while j < len(out):
                try:
                    dup = _is_duplicate(out[i], out[j])
                except Exception:
                    dup = False
                if dup:
                    winner = _prefer(out[i], out[j])
                    msg = (
                        f"consolidate '{(out[i].get('title') or '')[:35]}' + "
                        f"'{(out[j].get('title') or '')[:35]}' "
                        f"@ {_venue_root(out[i].get('venue'))} "
                        f"-> kept '{(winner.get('title') or '')[:35]}'"
                    )
                    DEDUP_DEBUG.append(msg)
                    print("DEDUP " + msg, flush=True)
                    out[i] = winner
                    del out[j]
                    changed = True
                else:
                    j += 1
            i += 1

    # Final survivor groups by (venue, date).
    post_groups: dict[tuple, list[str]] = {}
    for ev in out:
        v = _venue_root(ev.get("venue"))
        d = _to_date(ev["start"])
        if v and d:
            post_groups.setdefault((v, d.isoformat()), []).append(
                f"{(ev.get('title') or '')[:35]}@{ev.get('start')}"
            )
    for key, members in post_groups.items():
        if len(members) > 1:
            msg = f"survivor venue={key[0]} date={key[1]}: {members}"
            DEDUP_DEBUG.append(msg)
            print("DEDUP " + msg, flush=True)
    DEDUP_DEBUG.append(f"output_count={len(out)}")
    return out


def score_all(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for ev in events:
        if is_excluded(ev):
            continue
        ev = score(ev)
        # Drop uncategorized events — they're noise unless we know what they
        # are. (Keep an open eye on this — if too many things drop, expand
        # the keyword lists rather than relax the filter.)
        if ev["category"] is None:
            continue
        ev["recurring"] = is_recurring(ev)
        out.append(ev)
    out = _dedupe_by_venue_date(out)
    # Stable descending sort by score, then by date ascending.
    out.sort(key=lambda e: (-e["score"], _to_date(e["start"]) or date.max))
    return out


# ---------------------------------------------------------------------------
# Weekend bucketing
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo
    _CHS_TZ = ZoneInfo("America/New_York")
except Exception:
    _CHS_TZ = None


def _effective_today_charleston() -> tuple[date, bool]:
    """Return (effective_today, show_last_weekend).

    Rollover rule: until Monday 9 AM Charleston time, the "This weekend"
    tab still points at the just-finished weekend. At 9 AM Monday the
    default rolls forward to the upcoming weekend and a "Last weekend"
    tab appears on the right.
    """
    if _CHS_TZ is not None:
        now_local = datetime.now(_CHS_TZ)
    else:
        now_local = datetime.now()
    today_local = now_local.date()
    if today_local.weekday() == 0 and now_local.hour < 9:   # Mon before 9 AM
        return today_local - timedelta(days=1), False
    return today_local, True


def _weekend_range(reference: date, offset_weeks: int = 0) -> tuple[date, date]:
    """Return (fri, sun) for the weekend N weeks out from `reference`.

    The reference's own weekend counts as offset 0:
      - Mon–Thu → next Fri–Sun
      - Fri/Sat/Sun → current weekend (so the dashboard stays useful on Sat)
    """
    wd = reference.weekday()  # Mon=0 ... Sun=6
    if wd <= 3:                # Mon–Thu
        days_to_fri = 4 - wd
    elif wd == 4:              # Fri
        days_to_fri = 0
    else:                       # Sat (5) or Sun (6)
        days_to_fri = -(wd - 4)
    fri = reference + timedelta(days=days_to_fri + 7 * offset_weeks)
    sun = fri + timedelta(days=2)
    return fri, sun


MAX_PER_VENUE = 2  # cap any one venue at this many events per list

# Cap specific categories that tend to over-fill the list. Anything not in
# this dict is uncapped. Driven by user preference, not category weight —
# e.g. art exhibits get a tight cap because Spencer only goes to ones with
# unique programming or brand sponsorship (and the brand boost handles
# ranking inside the cap).
MAX_PER_CATEGORY = {
    "art_event": 2,
}


def _cap_per_venue(events: list[dict], cap: int = MAX_PER_VENUE) -> list[dict]:
    """Keep at most `cap` events per venue (highest-scoring win). Stops
    Windjammer/Pour House etc. from monopolizing a weekend's listing."""
    counts: dict[str, int] = {}
    out: list[dict] = []
    for ev in events:  # input must be score-sorted
        v = _venue_root(ev.get("venue"))
        if not v:
            out.append(ev)
            continue
        if counts.get(v, 0) >= cap:
            continue
        counts[v] = counts.get(v, 0) + 1
        out.append(ev)
    return out


def _cap_per_category(events: list[dict]) -> list[dict]:
    """Apply MAX_PER_CATEGORY limits. Events must be score-sorted."""
    counts: dict[str, int] = {}
    out: list[dict] = []
    for ev in events:
        cat = ev.get("category") or ""
        cap = MAX_PER_CATEGORY.get(cat)
        if cap is not None and counts.get(cat, 0) >= cap:
            continue
        counts[cat] = counts.get(cat, 0) + 1
        out.append(ev)
    return out


def _collapse_same_run(events: list[dict]) -> list[dict]:
    """Collapse SAME-DAY duplicates only: events sharing a venue, a close
    title (substring match), AND the same calendar date.

    Multi-day events (Memorial Day Weekend running Sat–Mon, a band booked
    both Sat and Sun, etc.) intentionally get a separate entry per day so
    the Sunday tab isn't empty just because the score-sort tiebreaker
    happened to give Saturday's instance the slot.

    Catches:
      • Two performance times of the same act on the same day at the same
        venue (often > 2h apart, so the venue+date+content dedup treats
        them as 'different sessions' and lets both through).
      • 'Nico Moon' + 'Nico Moon Band' at the same venue on the same day.
    """
    seen: list[tuple[str, str, str, dict]] = []  # (title, venue, date, ev)
    out: list[dict] = []
    for ev in events:
        t = _norm_title(ev.get("title") or "")
        v = _venue_root(ev.get("venue")) or ""
        d = _to_date(ev.get("start"))
        d_key = d.isoformat() if d else ""
        if not t or not v:
            out.append(ev)
            continue
        match = False
        for (st, sv, sd, _) in seen:
            if sv != v or sd != d_key:
                continue
            if t == st or t in st or st in t:
                match = True
                break
        if match:
            continue
        seen.append((t, v, d_key, ev))
        out.append(ev)
    return out


def _norm_title(t: str) -> str:
    import re as _re
    t = (t or "").lower()
    t = _re.sub(r"[^a-z0-9]+", " ", t)
    return _re.sub(r"\s+", " ", t).strip()


def bucket(events: list[dict], today: date | None = None) -> dict:
    show_last = True
    if today is None:
        today, show_last = _effective_today_charleston()
    this_fri, this_sun = _weekend_range(today, 0)
    next_fri, next_sun = _weekend_range(today, 1)
    last_fri, last_sun = _weekend_range(today, -1)

    this_weekend: list[dict] = []
    next_weekend: list[dict] = []
    last_weekend: list[dict] = []
    this_weekdays: list[dict] = []
    next_weekdays: list[dict] = []
    this_recurring: list[dict] = []
    next_recurring: list[dict] = []
    last_recurring: list[dict] = []

    for ev in events:
        d = _to_date(ev["start"])
        if d is None:
            continue
        is_recur = bool(ev.get("recurring"))
        if this_fri <= d <= this_sun:
            (this_recurring if is_recur else this_weekend).append(ev)
        elif next_fri <= d <= next_sun:
            (next_recurring if is_recur else next_weekend).append(ev)
        elif last_fri <= d <= last_sun:
            (last_recurring if is_recur else last_weekend).append(ev)
        elif today <= d < this_fri:
            this_weekdays.append(ev)
        elif this_sun < d < next_fri:
            next_weekdays.append(ev)

    def _polish_with_overflow(lst):
        """Returns (kept, overflow): events that survived the venue+category
        caps and those that got dropped, so the renderer can surface the
        full pool in a collapsed drawer instead of throwing it away."""
        collapsed = _collapse_same_run(lst)
        kept = _cap_per_category(_cap_per_venue(collapsed))
        kept_ids = {id(e) for e in kept}
        overflow = [e for e in collapsed if id(e) not in kept_ids]
        return kept, overflow

    tw, tw_overflow = _polish_with_overflow(this_weekend)
    nw, nw_overflow = _polish_with_overflow(next_weekend)
    lw, lw_overflow = _polish_with_overflow(last_weekend)
    twr, twr_overflow = _polish_with_overflow(this_recurring)
    nwr, nwr_overflow = _polish_with_overflow(next_recurring)
    lwr, lwr_overflow = _polish_with_overflow(last_recurring)

    return {
        "today": today.isoformat(),
        "show_last_weekend": show_last,
        "this_weekend": {
            "range": (this_fri.isoformat(), this_sun.isoformat()),
            "events": tw,
            "overflow": tw_overflow + twr_overflow,
            "weekdays": _cap_per_category(_cap_per_venue(_collapse_same_run(this_weekdays))),
            "recurring": twr,
        },
        "next_weekend": {
            "range": (next_fri.isoformat(), next_sun.isoformat()),
            "events": nw,
            "overflow": nw_overflow + nwr_overflow,
            "weekdays": _cap_per_category(_cap_per_venue(_collapse_same_run(next_weekdays))),
            "recurring": nwr,
        },
        "last_weekend": {
            "range": (last_fri.isoformat(), last_sun.isoformat()),
            "events": lw,
            "overflow": lw_overflow + lwr_overflow,
            "recurring": lwr,
        },
    }
