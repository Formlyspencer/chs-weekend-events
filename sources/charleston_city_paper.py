"""Charleston City Paper.

Their calendar (/calendar/) is a dynamic widget, but the weekly "What to do"
article is a normal blog post — same heading-based approach as CHStoday.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import _common

log = logging.getLogger(__name__)
SOURCE = "Charleston City Paper"

BASE = "https://www.charlestoncitypaper.com/"
SECTION_PATHS = [
    "what-to-do-this-week/",
    "things-to-do/",
    "events/",
]

_DATE_RE = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+"
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\s+(?P<day>\d{1,2})",
    re.IGNORECASE,
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}


def _first_date(text: str) -> date | None:
    today = date.today()
    for m in _DATE_RE.finditer(text):
        month = _MONTHS[m.group("month").lower()[:3]]
        day = int(m.group("day"))
        for off in (0, 1):
            try:
                d = date(today.year + off, month, day)
            except ValueError:
                continue
            if d >= today:
                return d
    return None


def _discover_posts(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()
    for a in soup.select("a[href]"):
        href = a["href"]
        if any(k in href for k in ("what-to-do", "things-to-do", "weekend", "/event/")):
            urls.add(urljoin(base, href))
    return sorted(urls)


def _parse_post(url: str, html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        return []

    out: list[dict] = []
    for h in article.find_all(["h2", "h3"]):
        name = h.get_text(" ", strip=True)
        if len(name) < 4 or len(name) > 140:
            continue
        chunk: list[str] = []
        for sib in h.find_next_siblings():
            if sib.name in {"h2", "h3"}:
                break
            chunk.append(sib.get_text(" ", strip=True))
        body = "\n".join(c for c in chunk if c).strip()
        d = _first_date(body) or _first_date(name)
        if not d:
            continue
        if not _common.is_in_area(body) and not _common.is_in_area(name):
            continue
        out.append(_common.event(
            title=name,
            start=d,
            description=body[:600] if body else None,
            url=url,
            price=_common.parse_price(body),
            source=SOURCE,
        ))
    return out


def fetch() -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for path in SECTION_PATHS:
        r = _common.http_get(urljoin(BASE, path))
        if not r:
            continue
        for url in _discover_posts(r.text, BASE):
            if url in seen:
                continue
            seen.add(url)
            rp = _common.http_get(url)
            if not rp:
                continue
            try:
                for ev in _parse_post(url, rp.text):
                    if _common.within_horizon(ev["start"]):
                        out.append(ev)
            except Exception as e:
                log.warning("CCP parse %s: %s", url, e)
    log.info("Charleston City Paper: %d events in horizon", len(out))
    return out
