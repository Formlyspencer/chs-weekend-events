"""Iterate all configured sources, collect events, dedupe."""
from __future__ import annotations

import importlib
import logging
import re
from datetime import datetime, date

import config

log = logging.getLogger(__name__)


def _to_date(v):
    if isinstance(v, datetime):
        return v.date()
    return v


def _norm_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def fetch_all() -> list[dict]:
    bucket: list[dict] = []
    for modname in config.SOURCES:
        try:
            mod = importlib.import_module(modname)
            events = mod.fetch() or []
            log.info("[%s] %d events", modname, len(events))
            bucket.extend(events)
        except Exception as e:
            log.warning("[%s] crashed: %s", modname, e)

    # Dedupe by (normalized title, date). Prefer the entry with the most
    # populated fields (price / venue / url).
    by_key: dict[tuple[str, str], dict] = {}
    for ev in bucket:
        d = _to_date(ev["start"])
        if not d:
            continue
        key = (_norm_title(ev["title"]), d.isoformat())
        if key not in by_key:
            by_key[key] = ev
            continue
        old = by_key[key]
        # Score completeness for the winner.
        score_new = sum(int(bool(ev.get(f))) for f in ("price", "venue", "url", "neighborhood"))
        score_old = sum(int(bool(old.get(f))) for f in ("price", "venue", "url", "neighborhood"))
        if score_new > score_old:
            by_key[key] = ev

    out = list(by_key.values())
    log.info("After dedupe: %d events", len(out))
    return out
