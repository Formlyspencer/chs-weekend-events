"""Hed Hi Studio — Charleston gallery / event space at 654 King St.

Squarespace site with a `/shows` events collection. Each event page has
schema.org Event JSON-LD with name, startDate, endDate, location, image,
description. Much cleaner than chstoday's SPA.

Strategy:
  1. GET /shows  → find all `/shows/<slug>` links in the upcoming list
  2. For each event slug, GET the page and parse the Event JSON-LD
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import _common

log = logging.getLogger(__name__)
SOURCE = "Hed Hi Studio"
BASE = "https://www.hedhistudio.com"
INDEX = f"{BASE}/shows"
LANDING_URL = INDEX


def _discover_upcoming(html: str) -> list[str]:
    """Pull /shows/<slug> URLs from the events list.

    We *don't* trust Squarespace's `--past` class — their /shows view often
    shows the most-recent past events by default, with future ones tagged
    `--past` until the curator updates the page. We collect every slug and
    let `within_horizon` filter by the actual startDate from JSON-LD.
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()
    for a in soup.select('a[href^="/shows/"]'):
        href = a["href"].split("?")[0].split("#")[0]
        if href in ("/shows", "/shows/"):
            continue
        urls.add(urljoin(BASE, href))
    return sorted(urls)


def _parse_event_page(url: str, html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
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
                start = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                continue
            end = None
            end_raw = item.get("endDate")
            if end_raw:
                try:
                    end = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    pass
            loc = item.get("location") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            venue_name = loc.get("name") if isinstance(loc, dict) else None
            venue_addr = ""
            if isinstance(loc, dict):
                addr = loc.get("address") or ""
                venue_addr = addr if isinstance(addr, str) else ""
            venue_full = ", ".join(filter(None, [venue_name, venue_addr.replace("\n", ", ")]))
            # Strip the "— Hed Hi Studio" suffix that's in every JSON-LD name.
            name = re.sub(r"\s*[—\-]\s*Hed Hi Studio\s*$", "", item.get("name", ""))
            # Description is usually in og:description, not in JSON-LD here.
            og = soup.find("meta", attrs={"property": "og:description"})
            desc = og["content"] if og and og.get("content") else None
            return _common.event(
                title=name,
                start=start,
                end=end,
                venue=venue_full or None,
                neighborhood="Charleston",  # Hed Hi is downtown
                url=url,
                description=desc,
                price=_common.parse_price(desc),
                source=SOURCE,
            )
    return None


def fetch() -> list[dict]:
    r = _common.http_get(INDEX)
    if not r:
        log.warning("Hed Hi Studio /shows unreachable")
        return []
    urls = _discover_upcoming(r.text)
    log.info("Hed Hi Studio: %d upcoming event URLs", len(urls))
    out: list[dict] = []
    for url in urls:
        rp = _common.http_get(url)
        if not rp:
            continue
        try:
            ev = _parse_event_page(url, rp.text)
        except Exception as e:
            log.warning("Hed Hi parse %s failed: %s", url, e)
            continue
        if ev and _common.within_horizon(ev["start"]):
            out.append(ev)
    log.info("Hed Hi Studio: %d events in horizon", len(out))
    return out
