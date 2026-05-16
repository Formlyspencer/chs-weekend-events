"""CHStoday newsletter — pulled from Spencer's personal Gmail via IMAP.

CHStoday's website is a JavaScript SPA we can't scrape, but the daily
email newsletter has the same content as plain HTML and (better still)
a tidy structured "Events" section.

Format (verified against a real newsletter):
    Events
    Friday, May 15
    Tiny Lawn Music Series | 6-8 p.m. | Darby Building, Mount Pleasant | Free

    Charleston Riverdogs vs. Kannapolis | 7:05 p.m. | Joe Riley Stadium | $25+

    Saturday, May 16
    Farmers Market | 9:30 a.m.-1 p.m. | Ravenel Depot | Price of purchase
    ...
    AtomaCon | Saturday, May 16-Sunday, May 17 | Trident Tech, N Chas | $20+
    ...
    Sunday, May 17
    Sunday Bazaar | 10:30 a.m.-2:30 p.m. | American Gardens | Price of purchase
    ...

Each event is a single line, pipe-delimited: title | time | venue | price.
A date header line (`Friday, May 15`) precedes a day's group; multi-day
events embed their own date range in the time column.

Auth: IMAP with an App Password, env vars IMAP_USERNAME / IMAP_PASSWORD.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import os
import re
from datetime import date, timedelta
from email.message import EmailMessage

from bs4 import BeautifulSoup

from . import _common

log = logging.getLogger(__name__)
SOURCE = "CHStoday (newsletter)"

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

GMAIL_QUERY = "from:chstoday OR from:6amcity newer_than:14d"
FALLBACK_SINCE_DAYS = 14
MAX_EMAILS = 10

# Fallback URL when we can't extract a per-event link from the email HTML.
EVENTS_CALENDAR_URL = "https://6amcity.com/sc/charleston/events"

# A day header looks like "Friday, May 15" (no year). Optional comma.
_DAY_HEADER_RE = re.compile(
    r"^(?P<dow>mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+"
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\s+(?P<day>\d{1,2})\s*$",
    re.IGNORECASE,
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}
# Multi-day events embed dates in the time column:
#   "Saturday, May 16-Sunday, May 17"  or  "May 16 - May 17"
_INLINE_DATE_RE = re.compile(
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\s+(?P<day>\d{1,2})",
    re.IGNORECASE,
)


def _connect() -> imaplib.IMAP4_SSL | None:
    user = os.environ.get("IMAP_USERNAME")
    pwd = os.environ.get("IMAP_PASSWORD")
    if not (user and pwd):
        log.info("CHStoday newsletter: IMAP env vars not set, skipping")
        return None
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        conn.login(user, pwd)
        return conn
    except imaplib.IMAP4.error as e:
        log.warning("CHStoday newsletter: IMAP login failed: %s", e)
        return None
    except Exception as e:
        log.warning("CHStoday newsletter: IMAP connect failed: %s", e)
        return None


def _list_message_ids(conn: imaplib.IMAP4_SSL) -> list[bytes]:
    conn.select("INBOX", readonly=True)
    try:
        typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{GMAIL_QUERY}"')
        if typ == "OK" and data and data[0]:
            return data[0].split()
    except imaplib.IMAP4.error:
        pass
    since = (date.today() - timedelta(days=FALLBACK_SINCE_DAYS)).strftime("%d-%b-%Y")
    try:
        typ, data = conn.uid("SEARCH", None, "FROM", "chstoday", "SINCE", since)
        if typ == "OK" and data and data[0]:
            return data[0].split()
    except imaplib.IMAP4.error as e:
        log.warning("CHStoday newsletter: IMAP search failed: %s", e)
    return []


def _fetch_message(conn: imaplib.IMAP4_SSL, uid: bytes) -> EmailMessage | None:
    try:
        typ, data = conn.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not data or not data[0]:
            return None
        return email.message_from_bytes(data[0][1], policy=email.policy.default)
    except Exception as e:
        log.warning("CHStoday newsletter: fetch uid=%s failed: %s", uid, e)
        return None


def _email_html(msg: EmailMessage) -> str:
    """Return the HTML body of the message (preferred) or empty string."""
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        try:
            return part.get_content()
        except Exception:
            return ""
    return ""


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _build_url_map(soup: BeautifulSoup) -> dict[str, str]:
    """{normalized-link-text: href} for every <a> in the email — used to
    recover per-event URLs since the newsletter wraps each event title in
    a link.
    """
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        text = _norm_text(a.get_text(" ", strip=True))
        if text and text not in out:
            out[text] = a["href"]
    return out


def _date_from_inline(text: str) -> date | None:
    """Pick the soonest upcoming month/day mentioned in `text`.

    For a date range like "Thursday, May 14-Saturday, May 16" run on May 16,
    we want May 16 (today, still happening) — not the first mention May 14
    bounced to next year.
    """
    today = date.today()
    candidates: list[date] = []
    for m in _INLINE_DATE_RE.finditer(text):
        month = _MONTHS[m.group("month").lower()[:3]]
        day = int(m.group("day"))
        for offset in (0, 1):
            try:
                cand = date(today.year + offset, month, day)
            except ValueError:
                continue
            if cand >= today:
                candidates.append(cand)
                break
    return min(candidates) if candidates else None


def _parse_events_section(body_text: str, url_map: dict[str, str]) -> list[dict]:
    """Walk the `Events` section of the newsletter and yield event dicts."""
    # Find the Events section — bracketed by "Events" header and the next
    # major heading ("News Notes", "The Wrap", "The Buy") or the "See our
    # full events calendar" footer line.
    start_match = re.search(r"(?m)^\s*Events\s*$", body_text)
    if not start_match:
        return []
    section = body_text[start_match.end():]
    for end_marker in (
        r"(?m)^\s*See our full events calendar",
        r"(?m)^\s*News Notes\s*$",
        r"(?m)^\s*The Wrap\s*$",
        r"(?m)^\s*The Buy\s*$",
        r"(?m)^\s*News\s*$",
    ):
        m = re.search(end_marker, section)
        if m:
            section = section[: m.start()]
            break

    today = date.today()
    out: list[dict] = []
    current_date: date | None = None

    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip "Feature your event here" promo lines etc.
        if line.startswith("Feature your event"):
            continue

        # Day header?
        m = _DAY_HEADER_RE.match(line)
        if m:
            month = _MONTHS[m.group("month").lower()[:3]]
            day = int(m.group("day"))
            new_date: date | None = None
            for offset in (0, 1):
                try:
                    cand = date(today.year + offset, month, day)
                except ValueError:
                    continue
                if cand >= today - timedelta(days=2):  # allow today even after midnight
                    new_date = cand
                    break
            if new_date:
                current_date = new_date
            continue

        # Event line — pipe-delimited. Two shapes observed in real
        # newsletters:
        #   (A) single-day:  title | time | venue | price            (4 parts)
        #   (B) multi-day:   title | date_range | venue | price      (4 parts)
        #                    title | date_range | time | venue | price (5 parts)
        # Detect (B) by checking whether slot 1 contains a month/day phrase.
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        title = parts[0]
        if len(title) < 4 or len(title) > 140:
            continue
        if any(skip in title.lower() for skip in ("subscribe", "advertise", "support us")):
            continue

        slot1_has_date = bool(_INLINE_DATE_RE.search(parts[1]))
        if slot1_has_date:
            date_text = parts[1]
            if len(parts) >= 5:
                time_col, venue_col, price_col = parts[2], parts[3], parts[4]
            else:
                time_col = ""
                venue_col = parts[2] if len(parts) > 2 else ""
                price_col = parts[3] if len(parts) > 3 else ""
        else:
            date_text = ""
            time_col = parts[1] if len(parts) > 1 else ""
            venue_col = parts[2] if len(parts) > 2 else ""
            price_col = parts[3] if len(parts) > 3 else ""

        # Inline date wins over the section's day-header (multi-day events).
        ev_date = _date_from_inline(date_text) or current_date
        if ev_date is None or not _common.within_horizon(ev_date):
            continue

        # Venue can include the neighborhood in parens or after a comma:
        #   "West Marine (West Ashley)"
        #   "Holy City Brewing (The Porter Room), North Charleston"
        if not _common.is_in_area(venue_col) and not _common.is_in_area(title):
            continue

        price = _common.parse_price(price_col)
        url = url_map.get(_norm_text(title)) or EVENTS_CALENDAR_URL

        description = " · ".join(p for p in (time_col, venue_col, price_col) if p)
        out.append(_common.event(
            title=title,
            start=ev_date,
            venue=venue_col or None,
            url=url,
            description=description,
            price=price,
            source=SOURCE,
        ))
    return out


def fetch() -> list[dict]:
    conn = _connect()
    if conn is None:
        return []
    try:
        uids = _list_message_ids(conn)
        log.info("CHStoday newsletter: %d emails matched query", len(uids))
        out: list[dict] = []
        for uid in uids[-MAX_EMAILS:]:
            msg = _fetch_message(conn, uid)
            if msg is None:
                continue
            html = _email_html(msg)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            url_map = _build_url_map(soup)
            body_text = soup.get_text("\n", strip=True)
            try:
                events = _parse_events_section(body_text, url_map)
            except Exception as e:
                log.warning("CHStoday newsletter: parse uid=%s failed: %s", uid, e)
                continue
            out.extend(events)
        log.info("CHStoday newsletter: %d events extracted", len(out))
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass
