"""CHStoday (6AM City Charleston).

Their main events page is a client-rendered SPA, but the weekly "weekender"
blog post (and individual event posts under /local/) tend to be server-
rendered. Strategy: pull the homepage, find recent post links, parse each
post for date/venue lines.

This is best-effort. When the layout changes, update the selectors. The
scraper logs but never raises.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import _common

log = logging.getLogger(__name__)
SOURCE = "CHStoday"
BASES = [
    "https://chstoday.6amcity.com/",
    "https://6amcity.com/sc/charleston",
]


def _candidate_post_urls(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()
    for a in soup.select("a[href]"):
        href = a["href"]
        # Heuristic: posts live under /local/ or /lifestyle/ or have a date
        # slug. Events page links are also fair game.
        if any(seg in href for seg in ("/local/", "/lifestyle/", "/things-to-do", "/weekender", "/events/")):
            urls.add(urljoin(base, href))
    return sorted(urls)


# Date phrases we'll try to parse out of post bodies:
#   "Saturday, May 17" / "Saturday May 17" / "5/17" / "May 17, 7pm"
_DATE_RE = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+"
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\s+(?P<day>\d{1,2})",
    re.IGNORECASE,
)

_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def _parse_dates(text: str) -> list[date]:
    today = date.today()
    out: list[date] = []
    for m in _DATE_RE.finditer(text):
        month = _MONTHS[m.group("month").lower()[:3]]
        day = int(m.group("day"))
        # Pick the soonest upcoming year for this month/day.
        for year_offset in (0, 1):
            try:
                d = date(today.year + year_offset, month, day)
            except ValueError:
                continue
            if d >= today:
                out.append(d)
                break
    return out


def _extract_events_from_post(url: str, html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    # Drop nav/script/style chrome.
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        return []

    text = article.get_text("\n", strip=True)
    if not text:
        return []

    title = (soup.find("h1") or soup.title)
    post_title = title.get_text(strip=True) if title else url

    # Heuristic: split the article on H2/H3 (event sub-headings).
    events: list[dict] = []
    headings = article.find_all(["h2", "h3"])
    if headings:
        for h in headings:
            name = h.get_text(" ", strip=True)
            if len(name) < 4 or len(name) > 120:
                continue
            # Skip the post's intro/outro headings.
            if name.lower() in {"this week", "the weekender", "events"}:
                continue
            # Gather the next siblings until the next h2/h3 for the description.
            chunk: list[str] = []
            for sib in h.find_next_siblings():
                if sib.name in {"h2", "h3"}:
                    break
                chunk.append(sib.get_text(" ", strip=True))
            body = "\n".join(c for c in chunk if c).strip()
            dates = _parse_dates(body) or _parse_dates(name)
            for d in dates[:1]:  # only the first date in the chunk
                if not _common.is_in_area(body) and not _common.is_in_area(name):
                    continue
                events.append(_common.event(
                    title=name,
                    start=d,
                    venue=None,
                    description=body[:600] if body else None,
                    url=url,
                    price=_common.parse_price(body),
                    source=SOURCE,
                ))
        if events:
            return events

    # Fallback: treat the whole post as one event keyed off the title and the
    # first date in the body.
    dates = _parse_dates(text)
    if not dates:
        return []
    return [_common.event(
        title=post_title,
        start=dates[0],
        description=text[:600],
        url=url,
        price=_common.parse_price(text),
        source=SOURCE,
    )]


def fetch() -> list[dict]:
    all_events: list[dict] = []
    seen_urls: set[str] = set()

    for base in BASES:
        r = _common.http_get(base)
        if not r:
            continue
        post_urls = _candidate_post_urls(r.text, base)
        log.info("CHStoday %s -> %d candidate posts", base, len(post_urls))
        for url in post_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            rp = _common.http_get(url)
            if not rp:
                continue
            try:
                events = _extract_events_from_post(url, rp.text)
            except Exception as e:
                log.warning("CHStoday parse %s failed: %s", url, e)
                continue
            for ev in events:
                if _common.within_horizon(ev["start"]):
                    all_events.append(ev)

    log.info("CHStoday: %d events in horizon", len(all_events))
    return all_events
