"""Holy City Sinner.

Two angles:
  1. The recurring "Charleston Weekend Events" post, parsed for individual
     events (similar approach to CHStoday).
  2. The embedded Trumba calendar feed, if reachable as iCal.

We always try both — whichever produces events wins.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, date

from bs4 import BeautifulSoup

from . import _common

# Trumba's DESCRIPTION field is a smushed-together blob of HTML plus a few
# template lines like "Event Type:", "Holiday:", "Link:". This regex strips
# those and pulls out the embedded event URL when present.
_BOILERPLATE_PREFIXES = (
    "event type:",
    "holiday:",
    "link:",
    "cost:",
    "phone:",
    "email:",
    "contact:",
    "website:",
    "more info:",
    "tickets:",
)
_URL_RE = re.compile(r"https?://\S+")


def _clean_description(raw: str) -> tuple[str, str | None]:
    """Return (clean text, extracted URL or None)."""
    if not raw:
        return "", None
    # 1. Turn <br> variants into newlines so the structure survives stripping.
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", raw, flags=re.IGNORECASE)
    s = re.sub(r"</\s*p\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</\s*li\s*>", "\n", s, flags=re.IGNORECASE)
    # 2. Strip remaining HTML.
    try:
        s = BeautifulSoup(s, "html.parser").get_text("\n")
    except Exception:
        pass
    # 3. Walk lines: remove boilerplate, capture the first http URL.
    extracted_url: str | None = None
    kept: list[str] = []
    for ln in s.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        low = ln.lower()
        if any(low.startswith(p) for p in _BOILERPLATE_PREFIXES):
            if extracted_url is None:
                m = _URL_RE.search(ln)
                if m:
                    extracted_url = m.group(0).rstrip(".,)")
            continue
        kept.append(ln)
    text = " ".join(kept)
    # Collapse runs of whitespace.
    text = re.sub(r"\s+", " ", text).strip()
    # Truncate at a sentence boundary if we can.
    if len(text) > 280:
        cut = text.rfind(". ", 0, 280)
        text = (text[: cut + 1] if cut > 100 else text[:280]).rstrip() + "…"
    return text, extracted_url

log = logging.getLogger(__name__)
SOURCE = "Holy City Sinner"

# Verified-working landing URL on Holy City Sinner — used as the last-resort
# fallback for any iCal event that doesn't expose its own link. The
# `/calendar/` path is a 404; the weekend-events post is the real index.
LANDING_URL = "https://www.holycitysinner.com/charleston-weekend-events/"
WEEKEND_POST = "https://www.holycitysinner.com/lifestyle/charleston-weekend-events/"

# Trumba UIDs look like `http://uid.trumba.com/event/200836181`. We can build
# a deep-link to the embedded event modal on Holy City Sinner's page so each
# event gets its own real URL — not the generic landing page.
_TRUMBA_UID_RE = re.compile(r"trumba\.com/event/(\d+)")


def _trumba_event_url(uid: str | None) -> str | None:
    if not uid:
        return None
    m = _TRUMBA_UID_RE.search(uid)
    if not m:
        return None
    eid = m.group(1)
    return (
        "https://holycitysinner.com/lifestyle/charleston-weekend-events/"
        f"?trumbaEmbed=view%3Devent%26eventid%3D{eid}"
    )

# Trumba iCal feeds typically live at:
#   https://www.trumba.com/calendars/<slug>.ics
# We try a few known slugs.
TRUMBA_ICAL_GUESSES = [
    "https://www.trumba.com/calendars/holycitysinner.ics",
    "https://www.trumba.com/calendars/holy-city-sinner.ics",
    "https://mylonews.trumba.com/calendars/holycitysinner.ics",
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


def _parse_first_date(text: str) -> date | None:
    today = date.today()
    for m in _DATE_RE.finditer(text):
        month = _MONTHS[m.group("month").lower()[:3]]
        day = int(m.group("day"))
        for offset in (0, 1):
            try:
                d = date(today.year + offset, month, day)
            except ValueError:
                continue
            if d >= today:
                return d
    return None


def _parse_weekend_post(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        return []

    events: list[dict] = []
    headings = article.find_all(["h2", "h3"])
    for h in headings:
        name = h.get_text(" ", strip=True)
        if len(name) < 4 or len(name) > 140:
            continue
        chunk: list[str] = []
        for sib in h.find_next_siblings():
            if sib.name in {"h2", "h3"}:
                break
            chunk.append(sib.get_text(" ", strip=True))
        body = "\n".join(c for c in chunk if c).strip()
        d = _parse_first_date(body) or _parse_first_date(name)
        if not d:
            continue
        if _common.is_explicitly_excluded(name) or _common.is_explicitly_excluded(body):
            continue
        if not _common.is_in_area(body) and not _common.is_in_area(name):
            continue
        events.append(_common.event(
            title=name,
            start=d,
            description=body[:600] if body else None,
            url=WEEKEND_POST,
            price=_common.parse_price(body),
            source=SOURCE,
        ))
    return events


def _unfold(text: str) -> list[str]:
    """RFC 5545 line unfolding: continuation lines start with space or tab."""
    raw_lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for line in raw_lines:
        if line.startswith(" ") or line.startswith("\t"):
            if out:
                out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _ical_unescape(val: str) -> str:
    """Undo iCal text escaping and HTML entities."""
    import html as _html
    # iCal: \\ \, \; \n \N
    out = []
    i = 0
    while i < len(val):
        ch = val[i]
        if ch == "\\" and i + 1 < len(val):
            nxt = val[i + 1]
            if nxt in ("\\", ",", ";"):
                out.append(nxt)
                i += 2
                continue
            if nxt in ("n", "N"):
                out.append("\n")
                i += 2
                continue
        out.append(ch)
        i += 1
    return _html.unescape("".join(out))


def _parse_ical(text: str) -> list[dict]:
    """Minimal iCal parser — pulls SUMMARY/DTSTART/LOCATION/URL/DESCRIPTION."""
    events: list[dict] = []
    cur: dict | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, _, val = line.partition(":")
            key = key.split(";")[0]  # strip params like DTSTART;TZID=...
            cur[key] = _ical_unescape(val) if key in (
                "SUMMARY", "LOCATION", "DESCRIPTION", "URL", "CATEGORIES"
            ) else val
    out: list[dict] = []
    for raw in events:
        try:
            dt_raw = raw.get("DTSTART", "")
            # Formats: YYYYMMDD or YYYYMMDDTHHMMSSZ
            if "T" in dt_raw:
                start = datetime.strptime(dt_raw.replace("Z", "")[:15], "%Y%m%dT%H%M%S")
            else:
                start = datetime.strptime(dt_raw[:8], "%Y%m%d").date()
        except Exception:
            continue
        location = raw.get("LOCATION") or ""
        # For iCal events the LOCATION is authoritative — if it explicitly
        # names an excluded town, drop regardless of what the description says.
        if not _common.is_in_area(location):
            continue
        desc_text, embedded_url = _clean_description(raw.get("DESCRIPTION", ""))
        # URL preference:
        #   1. Description-embedded URL if it has a real path (event page).
        #   2. iCal URL if it has a real path.
        #   3. iCal URL even if it's a bare homepage — landing on the venue's
        #      site is more useful than a Trumba deep-link whose modal
        #      doesn't reliably auto-open.
        #   4. Embedded URL even if bare.
        #   5. Trumba deep-link.
        #   6. Landing URL fallback.
        from urllib.parse import urlsplit

        def _has_path(u: str | None) -> bool:
            return bool(u and urlsplit(u).path not in ("", "/"))

        ical_url = raw.get("URL")
        trumba_url = _trumba_event_url(raw.get("UID"))
        url = (
            (embedded_url if _has_path(embedded_url) else None)
            or (ical_url if _has_path(ical_url) else None)
            or ical_url
            or embedded_url
            or trumba_url
            or LANDING_URL
        )
        out.append(_common.event(
            title=raw.get("SUMMARY", ""),
            start=start,
            venue=location or None,
            url=url,
            description=desc_text or None,
            price=_common.parse_price(raw.get("DESCRIPTION", "")),
            source=SOURCE,
        ))
    return out


def fetch() -> list[dict]:
    out: list[dict] = []

    # Try the weekend post first.
    r = _common.http_get(WEEKEND_POST)
    if r:
        try:
            events = _parse_weekend_post(r.text)
            log.info("Holy City Sinner weekend post: %d events parsed", len(events))
            out.extend(e for e in events if _common.within_horizon(e["start"]))
        except Exception as e:
            log.warning("Holy City Sinner weekend parse failed: %s", e)

    # Try Trumba iCal endpoints.
    for ical_url in TRUMBA_ICAL_GUESSES:
        r = _common.http_get(ical_url, headers={"Accept": "text/calendar"})
        if r and "BEGIN:VCALENDAR" in r.text:
            try:
                events = _parse_ical(r.text)
                log.info("Trumba %s: %d events parsed", ical_url, len(events))
                out.extend(e for e in events if _common.within_horizon(e["start"]))
                break
            except Exception as e:
                log.warning("Trumba parse failed: %s", e)

    log.info("Holy City Sinner: %d events in horizon", len(out))
    return out
