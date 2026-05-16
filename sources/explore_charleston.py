"""Explore Charleston / CharlestonCVB.

This site 403s most bot traffic — the scraper is a stub that tries once and
logs the outcome. Kept as a hook so the source list is complete and so it's
easy to revisit if we get a working approach (sitemap, mobile site, etc.).
"""
from __future__ import annotations

import logging

from . import _common

log = logging.getLogger(__name__)
SOURCE = "Explore Charleston"

URLS = [
    "https://www.charlestoncvb.com/events/",
    "https://www.explorecharleston.com/events/",
]


def fetch() -> list[dict]:
    for url in URLS:
        r = _common.http_get(url)
        if r:
            log.info("Explore Charleston %s reachable (status=%s) but no parser yet",
                     url, r.status_code)
            break
        else:
            log.info("Explore Charleston %s unreachable", url)
    return []
