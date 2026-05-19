"""Run the full pipeline: fetch → score → bucket → render → write docs/."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import fetch
import score
import render
import validate_urls
import v2_emit

try:
    # zoneinfo is stdlib on Python 3.9+; the CI runner has it.
    from zoneinfo import ZoneInfo
    _CHS_TZ = ZoneInfo("America/New_York")
except Exception:
    _CHS_TZ = None  # fallback below falls back to UTC

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _load_archive(path: Path, max_age_days: int = 21) -> list[dict]:
    """Reload events from the previous run's events.json.

    Trumba's iCal feed (our biggest source) only ships events from the
    current date forward — past events disappear from the source the
    moment they happen. To keep the 'Last weekend' tab populated through
    the week, we re-read the events we wrote on the previous run and
    merge them with the new fetch.

    Drops anything older than `max_age_days`. Reconstructs Python
    datetime/date objects from the ISO strings so downstream code keeps
    working unchanged.
    """
    from datetime import datetime, date, timedelta
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    cutoff = date.today() - timedelta(days=max_age_days)
    out: list[dict] = []
    for ev in data.get("events", []):
        start_str = ev.get("start") or ""
        if not start_str:
            continue
        try:
            if "T" in start_str:
                start_dt = datetime.fromisoformat(start_str)
                ev_date = start_dt.date()
            else:
                ev_date = date.fromisoformat(start_str[:10])
                start_dt = ev_date
        except Exception:
            continue
        if ev_date < cutoff:
            continue
        out.append({
            "title":        ev.get("title"),
            "start":        start_dt,
            "end":          ev.get("end"),
            "venue":        ev.get("venue"),
            "neighborhood": ev.get("neighborhood"),
            "url":          ev.get("url"),
            "description":  ev.get("description"),
            "price":        ev.get("price"),
            "source":       ev.get("source"),
        })
    return out


def _merge_with_archive(fresh: list[dict], archived: list[dict]) -> list[dict]:
    """Combine the newly-fetched events with the archived (past) ones,
    deduping by stable id so re-fetches don't create duplicates."""
    seen: set[str] = set()
    out: list[dict] = []
    for ev in fresh:
        sid = v2_emit._stable_id(ev)
        seen.add(sid)
        out.append(ev)
    for ev in archived:
        sid = v2_emit._stable_id(ev)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(ev)
    return out


def main() -> None:
    out_dir = Path(__file__).parent / "docs"

    # Load archived events from the previous run, then fetch new ones,
    # then merge. This keeps the 'Last weekend' tab populated even after
    # the source iCal stops shipping those dates.
    archived = _load_archive(out_dir / "v2" / "events.json")
    if archived:
        print(f"Loaded {len(archived)} archived events from previous run", flush=True)

    raw = fetch.fetch_all()
    raw = _merge_with_archive(raw, archived)
    # Run URL validation against the FULL deduped list. This way every event
    # — including ones that v1's hard-excludes would drop — has clean URLs
    # by the time v2 gets it. Modifies events in place; both raw and the
    # subset that score_all returns share the same dicts.
    raw = validate_urls.validate(raw)
    # Venue+date+content dedup BEFORE scoring, so v2's full event pool
    # also benefits from it. (score_all also runs this pass internally,
    # but on the already-deduped list it becomes a no-op.) Front Paige
    # Media vs. Firefly Vendor Village & Book Fair would otherwise both
    # land in v2/events.json because v2 ships the unfiltered list.
    raw = score._dedupe_by_venue_date(raw)
    scored = score.score_all(raw)
    buckets = score.bucket(scored)

    # Display the dashboard's "updated" timestamp in Charleston local time
    # (EDT/EST auto-handled by America/New_York). The cron itself still
    # runs on UTC — only the user-facing label is local.
    if _CHS_TZ is not None:
        local = datetime.now(timezone.utc).astimezone(_CHS_TZ)
        fetched_at = local.strftime("%Y-%m-%d %I:%M %p %Z")
    else:
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = render.render(buckets=buckets, fetched_at=fetched_at)

    out_dir.mkdir(exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    # Side-car JSON for debugging / future client-side use.
    debug_payload = {
        "fetched_at": fetched_at,
        "counts": {
            "raw": len(raw),
            "scored": len(scored),
            "this_weekend": len(buckets["this_weekend"]["events"]),
            "next_weekend": len(buckets["next_weekend"]["events"]),
        },
        "dedup_log": list(score.DEDUP_DEBUG),
        "buckets": _jsonable(buckets),
    }
    (out_dir / "events.json").write_text(
        json.dumps(debug_payload, indent=2, default=str), encoding="utf-8"
    )

    # v2 payload — ships the FULL deduped + URL-validated event list, not
    # just the post-exclusion subset. v2's browser-side filters can show or
    # hide rap shows / musicals / uncategorized events on demand. Defaults
    # in the JS still hide the same things v1 hides.
    v2_payload = v2_emit.build_payload(raw, fetched_at)
    v2_emit.write_payload(v2_payload, out_dir / "v2" / "events.json")

    print(
        f"Wrote docs/index.html — raw={len(raw)} scored={len(scored)} "
        f"this={len(buckets['this_weekend']['events'])} "
        f"next={len(buckets['next_weekend']['events'])}"
    )


def _jsonable(obj):
    from datetime import datetime, date
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, tuple):
        return [_jsonable(v) for v in obj]
    return obj


if __name__ == "__main__":
    main()
