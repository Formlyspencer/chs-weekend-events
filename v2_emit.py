"""Emit a rich events.json for the v2 browser-side-filtering UI.

Strategy:
  - Reuse the existing pipeline for the network-heavy + deterministic parts
    (scraping, dedup, URL validation, hard-exclude filtering).
  - For each surviving event, report ALL keyword matches as flags
    (categories matched, brand matches, music keyword matches, etc.).
  - Do NOT compute a score here — the browser applies the user's preferred
    weights to these flags and computes the score itself.
  - Bundle the project's default weights/settings into the same JSON so
    first-time visitors get Spencer's setup, then can tune in-browser.

Output schema is documented at the bottom of this module.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, date
from pathlib import Path

import config
from sources._common import _PRIORITY_AREAS  # priority-ordered list


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _stable_id(ev: dict) -> str:
    """Deterministic id for an event so the browser can star / track them."""
    key = "|".join([
        ev.get("title") or "",
        str(ev.get("start") or ""),
        ev.get("venue") or "",
        ev.get("source") or "",
    ])
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def _isoformat(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


_NEEDLE_CACHE: dict[str, re.Pattern] = {}

def _wb(needle: str) -> re.Pattern:
    pat = _NEEDLE_CACHE.get(needle)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(needle) + r"\b", re.IGNORECASE)
        _NEEDLE_CACHE[needle] = pat
    return pat


def _hits(text: str, keywords) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for n in keywords:
        if _wb(n).search(text):
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Match extraction — per-event analysis
# ---------------------------------------------------------------------------
def _detect_kid_age_signals(text: str) -> list[str]:
    """Return the list of age buckets that matched."""
    if not text:
        return []
    out: list[str] = []
    for bucket, kws in config.KID_AGE_KEYWORDS.items():
        if any(_wb(kw).search(text) for kw in kws):
            out.append(bucket)
    return out


def _category_keyword_hits(text: str) -> list[list]:
    """Returns [[category_key, matched_keyword], ...] for the title/desc text."""
    out: list[list] = []
    for key, spec in config.CATEGORIES.items():
        for kw in spec["keywords"]:
            if _wb(kw).search(text):
                out.append([key, kw])
                # Only need one hit per category per text — but report it.
                break
    return out


def _venue_category_hits(venue: str) -> list[list]:
    """Categories whose keywords (or for music, MUSIC_KEYWORDS or HINTS) match
    the venue string. The JS uses these for the venue-fallback pass.
    """
    if not venue:
        return []
    out: list[list] = []
    for key, spec in config.CATEGORIES.items():
        for kw in spec["keywords"]:
            if _wb(kw).search(venue):
                out.append([key, kw])
                break
    # Music gets extra venue lists.
    for kw in config.MUSIC_KEYWORDS:
        if _wb(kw).search(venue):
            out.append(["outdoor_music_pref_venue", kw])
            break
    for kw in getattr(config, "MUSIC_VENUE_HINTS", []) or []:
        if _wb(kw).search(venue):
            out.append(["outdoor_music_hint_venue", kw])
            break
    return out


def _analyze_event(ev: dict) -> dict:
    """Build the `matches` blob for one event."""
    title_desc = " ".join(filter(None, [ev.get("title"), ev.get("description")]))
    venue = ev.get("venue") or ""
    haystack = (title_desc + " " + venue)

    return {
        "category_keywords":  _category_keyword_hits(title_desc),
        "venue_categories":   _venue_category_hits(venue),
        "brand_keywords":     _hits(haystack, config.BRAND_KEYWORDS),
        "music_keywords":     _hits(venue + " " + title_desc, config.MUSIC_KEYWORDS),
        "music_venue_hints":  _hits(venue, getattr(config, "MUSIC_VENUE_HINTS", []) or []),
        "unique_strong":      _hits(haystack, config.UNIQUE_KEYWORDS),
        "unique_soft":        _hits(haystack, config.UNIQUE_KEYWORDS_SOFT),
        "kid_friendly":       _hits(haystack, config.KID_FRIENDLY_KEYWORDS),
        "kid_age_buckets":    _detect_kid_age_signals(haystack),
        "adult_only":         bool(_hits(haystack, config.ADULT_ONLY_KEYWORDS)),
        "attended_before":    _hits(haystack, config.ATTENDED_BEFORE),
        "recurring":          bool(ev.get("recurring")),
        "outdoor_signals":    _hits(haystack, getattr(config, "OUTDOOR_KEYWORDS", []) or []),
        "indoor_signals":     _hits(haystack, getattr(config, "INDOOR_KEYWORDS", []) or []),
        "drinking_signals":   _hits(haystack, getattr(config, "DRINKING_KEYWORDS", []) or []),
    }


# ---------------------------------------------------------------------------
# Bundle defaults so first-time visitors see Spencer's setup, then can tune.
# ---------------------------------------------------------------------------
def _defaults() -> dict:
    cats = {k: spec["weight"] for k, spec in config.CATEGORIES.items()}
    loc = {phrase: mult for phrase, mult in config._LOCATION_MULTIPLIERS}
    return {
        "category_weights": cats,
        "priority_areas":   _PRIORITY_AREAS,
        "location_multipliers": loc,
        "default_home":     "folly",
        "default_unknown_location_multiplier": 0.85,
        "music_keywords":   list(config.MUSIC_KEYWORDS),
        "music_venue_hints": list(getattr(config, "MUSIC_VENUE_HINTS", []) or []),
        "brand_keywords":   list(config.BRAND_KEYWORDS),
        "unique_strong":    list(config.UNIQUE_KEYWORDS),
        "unique_soft":      list(config.UNIQUE_KEYWORDS_SOFT),
        "attended_before":  list(config.ATTENDED_BEFORE),
        "attended_before_dampen": config.ATTENDED_BEFORE_DAMPEN,
        "tier_high":        config.TIER_HIGH,
        "tier_medium":      config.TIER_MEDIUM,
        "max_per_venue":    2,
        "max_per_category": {"art_event": 2},
        "horizon_days":     config.HORIZON_DAYS,
        "price_curve": [
            # [max_inclusive_price_or_-1_for_unknown, multiplier]
            [-1,   0.85],   # unknown
            [0,    0.95],   # free
            [20,   1.00],
            [40,   0.90],
            [75,   0.75],
            [100,  0.60],
            # everything above $100 → 0.40
        ],
        "price_above_cap_multiplier": 0.40,
        "day_multipliers": {
            # 0=Mon ... 6=Sun
            "0": 0.55, "1": 0.55, "2": 0.55, "3": 0.75,
            "4": 1.00, "5": 1.00, "6": 1.00,
            # Friday before 5pm gets a small haircut — encoded as a special-case
            # in the JS scorer, since dict can't represent it cleanly.
        },
        "friday_daytime_multiplier": 0.85,
    }


# ---------------------------------------------------------------------------
# Top-level: build the JSON payload from raw events.
# ---------------------------------------------------------------------------
def build_payload(events: list[dict], fetched_at: str) -> dict:
    enriched: list[dict] = []
    for ev in events:
        enriched.append({
            "id":           _stable_id(ev),
            "title":        ev.get("title"),
            "start":        _isoformat(ev.get("start")),
            "end":          _isoformat(ev.get("end")),
            "venue":        ev.get("venue"),
            "neighborhood": ev.get("neighborhood"),
            "url":          ev.get("url"),
            "description":  ev.get("description"),
            "price":        ev.get("price"),
            "source":       ev.get("source"),
            "matches":      _analyze_event(ev),
        })
    return {
        "generated_at": fetched_at,
        "defaults":     _defaults(),
        "events":       enriched,
    }


def write_payload(payload: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=None, separators=(",", ":")),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Schema (for reference only — not enforced):
#
#   {
#     generated_at: str,                      # human-readable timestamp
#     defaults: {                             # baseline config for first visit
#       category_weights: {key: weight, ...},
#       priority_areas: [...],
#       location_multipliers: {area: mult},
#       default_home: str,
#       default_unknown_location_multiplier: float,
#       music_keywords: [...],
#       music_venue_hints: [...],
#       brand_keywords: [...],
#       unique_strong: [...],
#       unique_soft: [...],
#       attended_before: [...],
#       attended_before_dampen: float,
#       tier_high: float,
#       tier_medium: float,
#       max_per_venue: int,
#       max_per_category: {key: int},
#       horizon_days: int,
#       price_curve: [[max_price, mult], ...],
#       price_above_cap_multiplier: float,
#       day_multipliers: {dow_str: mult},
#       friday_daytime_multiplier: float,
#     },
#     events: [{
#       id, title, start, end, venue, neighborhood, url, description,
#       price, source,
#       matches: {
#         category_keywords:  [[category_key, matched_keyword], ...],
#         venue_categories:   [[category_key, matched_keyword], ...],
#         brand_keywords:     [str, ...],
#         music_keywords:     [str, ...],
#         music_venue_hints:  [str, ...],
#         unique_strong:      [str, ...],
#         unique_soft:        [str, ...],
#         kid_friendly:       [str, ...],
#         kid_age_buckets:    [bucket, ...],   # toddler/preschool/...
#         adult_only:         bool,
#         attended_before:    [str, ...],
#         recurring:          bool,
#       }
#     }, ...]
#   }
# ---------------------------------------------------------------------------
