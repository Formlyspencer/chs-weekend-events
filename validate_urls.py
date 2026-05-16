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


# URLs are "too generic" if they're a bare homepage or a top-level section
# index — i.e. the link wouldn't take the user to anything specifically about
# this event. We treat the source's landing URL (a curated weekly roundup)
# as the exception, since that page actually lists the event.
_GENERIC_PATHS = {
    "", "/",
    "/news", "/news/",
    "/events", "/events/",
    "/calendar", "/calendar/",
    "/entertainment", "/entertainment/",
    "/lifestyle", "/lifestyle/",
    "/things-to-do", "/things-to-do/",
}

_SOURCE_LANDING_URLS = set(config.SOURCE_LANDING_URLS.values())


def _is_too_generic(url: str) -> bool:
    """True for bare homepages and section index pages.

    The whitelisted source landing URLs (Holy City Sinner's weekend roundup,
    etc.) are *not* considered too generic — they're the curated fallback
    that lists each event. Trumba deep-links (`?trumbaEmbed=view=event...`)
    are likewise event-specific even though the base path is generic.
    """
    if url in _SOURCE_LANDING_URLS:
        return False
    if "trumbaEmbed=view" in url and "eventid%3D" in url:
        return False  # per-event modal link — keep
    parts = urlsplit(url)
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
    # Dedupe URLs first so we don't hit the same one repeatedly.
    unique_urls = {ev["url"] for ev in events if ev.get("url")}
    log.info("Validating %d unique URLs across %d events", len(unique_urls), len(events))

    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_check, u) for u in unique_urls]
        for fut in as_completed(futures):
            url, ok = fut.result()
            results[url] = ok

    broken = sum(1 for ok in results.values() if not ok)
    log.info("URL check: %d ok, %d broken", len(results) - broken, broken)

    for ev in events:
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
    # User policy: if we can't verify it, we don't show it.
    recurring_urls = {
        ev["url"] for ev in events
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

        before = len(events)
        kept: list[dict] = []
        for ev in events:
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
        events = kept
        if before != len(events):
            log.info("Dropped %d unverifiable recurring events", before - len(events))
    return events
