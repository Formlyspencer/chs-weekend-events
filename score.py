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
    # them every weekend. Two tiers: strong signals (annual/biennial/etc.)
    # floor at 1.0, softer signals (gala/fundraiser/debut) floor at 0.85.
    if getattr(config, "UNIQUE_KEYWORDS", None) and _matches_any(haystack, config.UNIQUE_KEYWORDS):
        base = max(base, 1.0)
    elif getattr(config, "UNIQUE_KEYWORDS_SOFT", None) and _matches_any(haystack, config.UNIQUE_KEYWORDS_SOFT):
        base = max(base, 0.85)

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


def _dedupe_by_venue_date(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for ev in events:
        merged = False
        for i, kept in enumerate(out):
            if _is_duplicate(ev, kept):
                winner = _prefer(ev, kept)
                # Use print so output is unbuffered and shows in CI logs
                # regardless of logging config.
                print(
                    f"DEDUP merge: '{(ev.get('title') or '')[:40]}' + "
                    f"'{(kept.get('title') or '')[:40]}' "
                    f"(venue={_venue_root(ev.get('venue'))}) "
                    f"-> kept '{(winner.get('title') or '')[:40]}'",
                    flush=True,
                )
                out[i] = winner
                merged = True
                break
        if not merged:
            out.append(ev)

    # Flag any same-(venue,date) survivors so we can spot regressions.
    seen: dict[tuple, list[tuple[str, object]]] = {}
    for ev in out:
        v = _venue_root(ev.get("venue"))
        d = _to_date(ev["start"])
        if not v or not d:
            continue
        seen.setdefault((v, d.isoformat()), []).append(
            ((ev.get("title") or "")[:40], ev.get("start"))
        )
    for key, entries in seen.items():
        if len(entries) > 1:
            print(
                f"DEDUP survivors at venue={key[0]} date={key[1]}: {entries}",
                flush=True,
            )
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
    """Within a single weekend bucket, collapse repeated entries for the same
    (title, venue) on different days — e.g. an art exhibition that has one
    iCal entry per day of its run. Keep the highest-scoring instance only.
    """
    seen: dict[tuple[str, str], dict] = {}
    out: list[dict] = []
    for ev in events:
        t = _norm_title(ev.get("title") or "")
        v = _venue_root(ev.get("venue")) or ""
        key = (t, v)
        if not t or not v:
            out.append(ev)
            continue
        if key in seen:
            continue
        seen[key] = ev
        out.append(ev)
    return out


def _norm_title(t: str) -> str:
    import re as _re
    t = (t or "").lower()
    t = _re.sub(r"[^a-z0-9]+", " ", t)
    return _re.sub(r"\s+", " ", t).strip()


def bucket(events: list[dict], today: date | None = None) -> dict:
    today = today or date.today()
    this_fri, this_sun = _weekend_range(today, 0)
    next_fri, next_sun = _weekend_range(today, 1)

    this_weekend: list[dict] = []
    next_weekend: list[dict] = []
    this_weekdays: list[dict] = []
    next_weekdays: list[dict] = []
    this_recurring: list[dict] = []
    next_recurring: list[dict] = []

    for ev in events:
        d = _to_date(ev["start"])
        if d is None:
            continue
        is_recur = bool(ev.get("recurring"))
        if this_fri <= d <= this_sun:
            (this_recurring if is_recur else this_weekend).append(ev)
        elif next_fri <= d <= next_sun:
            (next_recurring if is_recur else next_weekend).append(ev)
        elif today <= d < this_fri:
            this_weekdays.append(ev)
        elif this_sun < d < next_fri:
            next_weekdays.append(ev)

    # Collapse multi-day runs (same title + venue on different days) within
    # each bucket — an exhibition that runs Sat AND Sun shouldn't appear
    # twice in the weekend list.
    this_weekend  = _collapse_same_run(this_weekend)
    next_weekend  = _collapse_same_run(next_weekend)
    this_weekdays = _collapse_same_run(this_weekdays)
    next_weekdays = _collapse_same_run(next_weekdays)
    this_recurring = _collapse_same_run(this_recurring)
    next_recurring = _collapse_same_run(next_recurring)

    # Cap each list at MAX_PER_VENUE so one busy venue can't dominate.
    this_weekend  = _cap_per_venue(this_weekend)
    next_weekend  = _cap_per_venue(next_weekend)
    this_weekdays = _cap_per_venue(this_weekdays)
    next_weekdays = _cap_per_venue(next_weekdays)
    this_recurring = _cap_per_venue(this_recurring)
    next_recurring = _cap_per_venue(next_recurring)

    # Apply per-category caps (art events capped tight per user pref).
    this_weekend  = _cap_per_category(this_weekend)
    next_weekend  = _cap_per_category(next_weekend)
    this_weekdays = _cap_per_category(this_weekdays)
    next_weekdays = _cap_per_category(next_weekdays)
    this_recurring = _cap_per_category(this_recurring)
    next_recurring = _cap_per_category(next_recurring)

    return {
        "today": today.isoformat(),
        "this_weekend": {
            "range": (this_fri.isoformat(), this_sun.isoformat()),
            "events": this_weekend,
            "weekdays": this_weekdays,
            "recurring": this_recurring,
        },
        "next_weekend": {
            "range": (next_fri.isoformat(), next_sun.isoformat()),
            "events": next_weekend,
            "weekdays": next_weekdays,
            "recurring": next_recurring,
        },
    }
