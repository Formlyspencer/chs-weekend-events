"""CHStoday newsletter — pulled from Spencer's personal Gmail via IMAP.

CHStoday's website is a JavaScript SPA we can't scrape, but the daily
email newsletter has the same content as plain HTML. This source connects
to Gmail's IMAP server with an App Password (no OAuth, no expiring tokens)
and extracts event mentions from each newsletter body.

Why IMAP instead of the Gmail API:
  Google's Gmail API expires refresh tokens after 7 days for apps in
  "Testing" status with sensitive scopes — so OAuth would require weekly
  re-auth. App Passwords don't expire, are revocable independently, and
  IMAP is a 30-year-old protocol that just works.

If the env vars aren't set, this scraper silently returns [] so the
pipeline keeps working before setup.

Required env vars (or GitHub Secrets):
    IMAP_USERNAME — full personal Gmail address
    IMAP_PASSWORD — 16-char App Password generated at
                    https://myaccount.google.com/apppasswords
                    (requires 2FA enabled on the Google account)
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import os
import re
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from bs4 import BeautifulSoup

from . import _common

log = logging.getLogger(__name__)
SOURCE = "CHStoday (newsletter)"

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Gmail-specific extended search — same syntax as the Gmail web UI.
# Gives us OR support and `newer_than:`.
GMAIL_QUERY = "from:chstoday OR from:6amcity newer_than:14d"

# Standard IMAP fallback (used if X-GM-RAW isn't honored for some reason).
FALLBACK_SINCE_DAYS = 14

# Cap on emails we'll pull per run.
MAX_EMAILS = 10


_DATE_RE = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+"
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\s+(?P<day>\d{1,2})",
    re.IGNORECASE,
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
)}


def _connect() -> imaplib.IMAP4_SSL | None:
    """Return an authenticated IMAP connection, or None if env vars missing."""
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
    """List recent CHStoday message UIDs. Tries Gmail's extended search
    first; falls back to standard IMAP if that's not supported.
    """
    conn.select("INBOX", readonly=True)
    # Gmail X-GM-RAW: supports Gmail's web-search syntax (OR, newer_than:, etc.)
    try:
        typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{GMAIL_QUERY}"')
        if typ == "OK" and data and data[0]:
            return data[0].split()
    except imaplib.IMAP4.error:
        pass

    # Fallback: basic IMAP search (single FROM, SINCE only).
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
        raw = data[0][1]
        return email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as e:
        log.warning("CHStoday newsletter: fetch uid=%s failed: %s", uid, e)
        return None


def _email_body(msg: EmailMessage) -> str:
    """Get the best text representation of an email's body."""
    # Prefer HTML (newsletters always send HTML, and BeautifulSoup gives a
    # cleaner extraction than the auto-generated text/plain part).
    html_part = msg.get_body(preferencelist=("html",))
    if html_part is not None:
        try:
            html = html_part.get_content()
        except Exception:
            html = ""
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            return soup.get_text("\n", strip=True)
    text_part = msg.get_body(preferencelist=("plain",))
    if text_part is not None:
        try:
            return text_part.get_content()
        except Exception:
            return ""
    return ""


def _parse_events_from_body(body: str, source_url: str) -> list[dict]:
    """Extract events from a newsletter body. Heuristic — looks for chunks
    with a date phrase and pulls the first heading-ish line as the title.

    Will likely need tuning once we see real CHStoday newsletter samples.
    """
    if not body:
        return []
    out: list[dict] = []
    today = date.today()
    chunks = re.split(r"\n\s*\n", body)
    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) < 30 or len(chunk) > 1200:
            continue
        dm = _DATE_RE.search(chunk)
        if not dm:
            continue
        month = _MONTHS[dm.group("month").lower()[:3]]
        day = int(dm.group("day"))
        ev_date: date | None = None
        for year_offset in (0, 1):
            try:
                cand = date(today.year + year_offset, month, day)
            except ValueError:
                continue
            if cand >= today:
                ev_date = cand
                break
        if ev_date is None or not _common.within_horizon(ev_date):
            continue
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        title = lines[0] if lines else ""
        if len(title) < 4 or len(title) > 140:
            continue
        if title.lower() in {"events", "this weekend", "things to do", "what's happening"}:
            continue
        body_text = " ".join(lines[1:])[:400]
        if not _common.is_in_area(body_text) and not _common.is_in_area(title):
            continue
        out.append(_common.event(
            title=title,
            start=ev_date,
            description=body_text or None,
            url=source_url,
            price=_common.parse_price(body_text),
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
        for uid in uids[-MAX_EMAILS:]:   # most recent N
            msg = _fetch_message(conn, uid)
            if msg is None:
                continue
            body = _email_body(msg)
            try:
                events = _parse_events_from_body(
                    body,
                    source_url="https://chstoday.6amcity.com/",
                )
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
