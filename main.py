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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    raw = fetch.fetch_all()
    scored = score.score_all(raw)
    # Validate URLs *after* scoring/dedup so we only check the events that
    # will actually be displayed. Broken URLs get swapped for the source's
    # landing page; if no fallback is available the link is dropped.
    scored = validate_urls.validate(scored)
    buckets = score.bucket(scored)

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = render.render(buckets=buckets, fetched_at=fetched_at)

    out_dir = Path(__file__).parent / "docs"
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
        "buckets": _jsonable(buckets),
    }
    (out_dir / "events.json").write_text(
        json.dumps(debug_payload, indent=2, default=str), encoding="utf-8"
    )

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
