"""Eventbrite Charleston.

Eventbrite's discover page returns server-rendered HTML with a __NEXT_DATA__
JSON blob and JSON-LD scripts. We try JSON-LD first (structured) and fall
back to the listing cards.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from . import _common

log = logging.getLogger(__name__)
SOURCE = "Eventbrite"

# Discover endpoint. Supports start_date / end_date in YYYY-MM-DD.
BASE = "https://www.eventbrite.com/d/sc--charleston/all-events/"


def _build_url(page: int) -> str:
    today = date.today()
    qs = urlencode({
        "start_date": today.isoformat(),
        "page": page,
    })
    return f"{BASE}?{qs}"


def _parse_jsonld(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            if t != "Event" and not (isinstance(t, list) and "Event" in t):
                continue
            start_raw = item.get("startDate")
            if not start_raw:
                continue
            try:
                # ISO 8601 with timezone; strip tz for naive datetime.
                start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                start = start.replace(tzinfo=None)
            except Exception:
                continue
            loc = item.get("location") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = loc.get("address") or {}
            if isinstance(addr, str):
                addr_str = addr
            else:
                addr_str = " ".join(filter(None, [
                    addr.get("addressLocality"),
                    addr.get("addressRegion"),
                    addr.get("streetAddress"),
                ]))
            venue = loc.get("name") if isinstance(loc, dict) else None
            offers = item.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = None
            if isinstance(offers, dict):
                p = offers.get("price")
                if p is not None:
                    try:
                        price = float(p)
                    except (TypeError, ValueError):
                        price = None
            search_text = " ".join(filter(None, [
                venue, addr_str, item.get("name", ""), item.get("description", "") or "",
            ]))
            if not _common.is_in_area(search_text):
                continue
            out.append(_common.event(
                title=item.get("name", ""),
                start=start,
                venue=venue,
                url=item.get("url"),
                description=(item.get("description") or "")[:600],
                price=price,
                source=SOURCE,
            ))
    return out


def fetch() -> list[dict]:
    out: list[dict] = []
    for page in (1, 2, 3):
        r = _common.http_get(_build_url(page))
        if not r:
            continue
        try:
            events = _parse_jsonld(r.text)
        except Exception as e:
            log.warning("Eventbrite page %d parse: %s", page, e)
            continue
        log.info("Eventbrite page %d: %d events", page, len(events))
        for ev in events:
            if _common.within_horizon(ev["start"]):
                out.append(ev)
        if not events:
            break  # likely end of useful data
    log.info("Eventbrite: %d events in horizon", len(out))
    return out
