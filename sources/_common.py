"""Shared helpers for scrapers."""
from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta, date
from typing import Iterable

import requests

import config

log = logging.getLogger(__name__)


def http_get(url: str, **kwargs) -> requests.Response | None:
    """GET with the project User-Agent and a timeout. Returns None on failure."""
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", config.USER_AGENT)
    headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml")
    try:
        r = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT, **kwargs)
        if r.status_code >= 400:
            log.warning("GET %s -> %s", url, r.status_code)
            return None
        return r
    except requests.RequestException as e:
        log.warning("GET %s failed: %s", url, e)
        return None


# Common money patterns: "$15", "$15-$20", "$15 – $20", "Free", "Free entry", "$100+"
_PRICE_RE = re.compile(
    r"(?:(?P<free>\bfree\b)|"
    r"\$\s*(?P<lo>\d+(?:\.\d+)?)\s*(?:[-–to]+\s*\$?\s*(?P<hi>\d+(?:\.\d+)?))?\s*(?P<plus>\+)?)",
    re.IGNORECASE,
)


def parse_price(text: str | None) -> float | None:
    """Best-effort extract a representative price. Returns None if unknown."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    if m.group("free"):
        return 0.0
    lo = float(m.group("lo"))
    hi = float(m.group("hi")) if m.group("hi") else lo
    if m.group("plus"):
        # "$100+" — assume the floor for scoring purposes
        return lo
    return (lo + hi) / 2


# Priority-ordered area matching: specific neighborhoods first, generic
# "charleston" last. Otherwise "Folly Road, Charleston, SC" matches
# "charleston" before "folly" (length-sorted) and we lose the real signal.
_PRIORITY_AREAS = [
    "folly beach", "folly",
    "james island",
    "johns island",
    "kiawah", "wadmalaw island", "wadmalaw",
    "mount pleasant", "mt. pleasant", "mt pleasant",
    "isle of palms", "iop",
    "sullivan's island", "sullivans island",
    "daniel island",
    "west ashley",
    "north charleston",
    "charleston",   # generic fallback — must come last
]

_EXCLUDE_PHRASES = sorted(config.EXCLUDED_AREAS, key=len, reverse=True)
_AREA_PHRASES = config.INCLUDED_AREAS  # used by is_in_area; order doesn't matter there


def detect_neighborhood(text: str | None) -> str | None:
    """Find the best Charleston-area neighborhood phrase in `text`.

    Uses a hand-ordered priority list so specific neighborhoods beat the
    generic "Charleston" fallback when an address contains both.
    """
    if not text:
        return None
    low = text.lower()
    for phrase in _PRIORITY_AREAS:
        if phrase in low:
            return phrase.title()
    return None


def is_in_area(text: str | None) -> bool:
    """True if text mentions an included area and not an excluded one.

    None text returns True (we'd rather show an unlocated event than drop it
    — the user can decide). If both an included and excluded phrase appear
    (common in roundup posts that list events across the region), trust the
    included match. If only an excluded phrase appears, drop.
    """
    if not text:
        return True
    low = text.lower()
    has_excluded = any(p in low for p in _EXCLUDE_PHRASES)
    has_included = any(p in low for p in _AREA_PHRASES)
    if has_excluded and not has_included:
        return False
    return True  # included or ambiguous → keep


def is_explicitly_excluded(text: str | None) -> bool:
    """True if `text` names an excluded area (Summerville, Goose Creek, etc.)

    Use this for venue-specific checks where the field is authoritative —
    even if a description elsewhere mentions Charleston, a venue in
    Summerville is in Summerville.
    """
    if not text:
        return False
    low = text.lower()
    return any(p in low for p in _EXCLUDE_PHRASES)


def within_horizon(dt: datetime | date | None) -> bool:
    if dt is None:
        return False
    if isinstance(dt, datetime):
        d = dt.date()
    else:
        d = dt
    today = date.today()
    return today <= d <= today + timedelta(days=config.HORIZON_DAYS)


def event(
    *,
    title: str,
    start: datetime | date | None,
    end: datetime | date | None = None,
    venue: str | None = None,
    neighborhood: str | None = None,
    url: str | None,
    description: str | None = None,
    price: float | None = None,
    source: str,
) -> dict:
    """Build a normalized event dict. Use this in every scraper for consistency.

    `url` must be a real, working URL — either the event-specific page or the
    scraper's source landing page. Don't pass None; pass the source's landing
    URL as the last-resort fallback.
    """
    if neighborhood is None:
        neighborhood = detect_neighborhood(" ".join(
            filter(None, [venue, description])
        ))
    return {
        "title": (title or "").strip(),
        "start": start,
        "end": end,
        "venue": (venue or "").strip() or None,
        "neighborhood": neighborhood,
        "url": url,
        "description": (description or "").strip() or None,
        "price": price,
        "source": source,
    }
