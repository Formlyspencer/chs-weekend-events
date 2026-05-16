"""Validate event URLs before publishing.

For each event:
  - If the URL returns 2xx/3xx, keep it.
  - If it 4xx/5xx/times out/errors, swap it for the source's landing URL
    (a verified-working page on the same site) so the link still goes
    somewhere real.

Uses HEAD requests with a short timeout, run in parallel across a thread pool.
Some servers don't support HEAD (return 405), so we fall back to GET and read
nothing.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

import requests

import config

log = logging.getLogger(__name__)


def _freshness_check(url: str) -> bool | None:
    """For a recurring event, GET the page and check for evidence the listing
    is still current — current year or recent date mentioned in the body.

    Returns:
      True  → looks fresh (current year found in body)
      False → looks stale (no current/recent year mentioned)
      None  → couldn't tell (network error, JS-only site, etc.)
    """
    # Instagram and similar JS-heavy sites won't render the post list in the
    # HTML we get back — we can't verify them either way, so default to
    # "stale" (flag visually) and let the user decide.
    if "instagram.com" in url or "facebook.com" in url:
        return False
    try:
        r = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT},
            timeout=10,
            allow_redirects=True,
        )
        if r.status_code >= 400:
            return None
        body = r.text.lower()
    except requests.RequestException:
        return None
    now = date.today().year
    # Accept current year or last year (some sites are slow to update copyright).
    for y in (now, now - 1):
        if str(y) in body:
            return True
    return False


# Generic top-level paths — bare homepages and broad section indexes.
_GENERIC_PATHS = {
    "", "/",
    "/news", "/news/",
    "/events", "/events/",
    "/calendar", "/calendar/",
    "/entertainment", "/entertainment/",
    "/lifestyle", "/lifestyle/",
    "/things-to-do", "/things-to-do/",
}

# News/blog/aggregator sites where a bare or section-level URL is genuinely
# unhelpful — the user lands on a news front page, not an event. Anywhere
# else (venue sites, brand sites, etc.) a bare homepage is fine since the
# user can navigate to find the event.
_NEWS_DOMAINS = {
    "holycitysinner.com", "www.holycitysinner.com",
    "postandcourier.com", "www.postandcourier.com",
    "6amcity.com", "www.6amcity.com", "chstoday.6amcity.com",
    "charlestoncitypaper.com", "www.charlestoncitypaper.com",
    "explorecharleston.com", "www.charlestoncvb.com",
}

_SOURCE_LANDING_URLS = set(config.SOURCE_LANDING_URLS.values())


def _is_too_generic(url: str) -> bool:
    """True for bare/section-level URLs on news/blog sites only.

    Venue/brand homepages aren't flagged — landing on a venue site is
    useful (user can navigate to its calendar). Trumba deep-links and
    whitelisted source landing pages also pass through.
    """
    if url in _SOURCE_LANDING_URLS:
        return False
    if "trumbaEmbed=view" in url and "eventid%3D" in url:
        return False  # per-event modal link — keep
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host not in _NEWS_DOMAINS:
        return False  # venue/brand site — bare path is acceptable
    if parts.path in _GENERIC_PATHS:
        return True
    return False


def _check(url: str) -> tuple[str, bool]:
    """Return (url, ok). Tries HEAD first; falls back to GET on 405."""
    headers = {"User-Agent": config.USER_AGENT, "Accept": "*/*"}
    try:
        r = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        if r.status_code == 405 or r.status_code >= 400:
            # Some sites reject HEAD outright. Try GET with no body read.
            r = requests.get(url, headers=headers, timeout=10, allow_redirects=True, stream=True)
            r.close()
        return url, 200 <= r.status_code < 400
    except requests.RequestException:
        return url, False


def validate(events: list[dict]) -> list[dict]:
    """Mutate-and-return: events with broken URLs get their URL swapped to the
    source's landing page. Logs each repair so we can see what's flaky.
    """
    # Manual-source events are user-curated and trusted — they bypass URL
    # validation entirely (their URLs may be intentionally homepage-ish or
    # gov-site stable links that fail our heuristics).
    trusted = [ev for ev in events if ev.get("source") == "Manual"]
    others = [ev for ev in events if ev.get("source") != "Manual"]
    if trusted:
        log.info("Skipping URL validation for %d manual events", len(trusted))

    # Dedupe URLs first so we don't hit the same one repeatedly.
    unique_urls = {ev["url"] for ev in others if ev.get("url")}
    log.info("Validating %d unique URLs across %d non-manual events", len(unique_urls), len(others))

    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_check, u) for u in unique_urls]
        for fut in as_completed(futures):
            url, ok = fut.result()
            results[url] = ok

    broken = sum(1 for ok in results.values() if not ok)
    log.info("URL check: %d ok, %d broken", len(results) - broken, broken)

    for ev in others:
        url = ev.get("url")
        if not url:
            continue
        broken = results.get(url) is False
        too_generic = _is_too_generic(url)
        if broken or too_generic:
            reason = "broken" if broken else "too generic"
            fallback = config.SOURCE_LANDING_URLS.get(ev.get("source", ""))
            if fallback and fallback != url:
                log.info("Swapping %s URL: %s  ->  %s  (event: %s)",
                         reason, url, fallback, ev.get("title", "")[:50])
                ev["url"] = fallback
            else:
                log.info("Dropping %s URL with no fallback: %s (event: %s)",
                         reason, url, ev.get("title", "")[:50])
                ev["url"] = None

    # For recurring events specifically, do a freshness check on the linked
    # page. Any event we can't confirm is current gets DROPPED — not flagged.
    # User policy: if we can't verify it, we don't show it. Manual events
    # are exempt (already filtered out above).
    recurring_urls = {
        ev["url"] for ev in others
        if ev.get("recurring") and ev.get("url")
        and ev["url"] not in _SOURCE_LANDING_URLS
    }
    if recurring_urls:
        log.info("Freshness-checking %d recurring-event URLs", len(recurring_urls))
        fresh: dict[str, bool | None] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_freshness_check, u): u for u in recurring_urls}
            for fut in as_completed(futs):
                fresh[futs[fut]] = fut.result()

        before = len(others)
        kept: list[dict] = []
        for ev in others:
            if not ev.get("recurring"):
                kept.append(ev)
                continue
            url = ev.get("url")
            verdict = fresh.get(url) if url else None
            # verdict True  → fresh, keep
            # verdict None  → couldn't tell (network error, etc.), keep
            # verdict False → looks stale or JS-only, DROP
            if verdict is False:
                log.info("Dropping unverifiable recurring event: %s  (url: %s)",
                         ev.get("title", "")[:50], url)
                continue
            kept.append(ev)
        others = kept
        if before != len(others):
            log.info("Dropped %d unverifiable recurring events", before - len(others))

    # Recombine: trusted manual events + validated others, preserving the
    # input order (trusted first since that's the order we collected).
    return trusted + others
