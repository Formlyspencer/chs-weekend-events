"""Manually-curated recurring events that aren't in the Trumba feed.

For each entry here, this scraper emits one occurrence per matching weekday
within the horizon — so a Saturday market appears for both "this weekend"
and "next weekend" automatically. Title language ("farmers market") makes
the recurring detector classify them into the routine section.

Edit the list below to add or remove items.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import config
from . import _common

SOURCE = "Manual"
LANDING_URL = "https://www.holycitysinner.com/charleston-weekend-events/"

# Each entry runs every week on `weekday` (0=Mon ... 6=Sun) at `time`.
MANUAL_RECURRING = [
    {
        "title": "Downtown Charleston Farmers Market",
        "weekday": 5,         # Saturday
        "time": (8, 0),
        "venue": "Marion Square, 329 Meeting St, Charleston, SC",
        "description": (
            "Charleston's flagship downtown farmers market at Marion Square. "
            "Local produce, prepared foods, makers, and live music. "
            "Saturdays April through November."
        ),
        "url": "https://www.charleston-sc.gov/Facilities/Facility/Details/Charleston-Farmers-Market-37",
        "price": 0.0,
    },
    {
        "title": "Pour House Farmers Market",
        "weekday": 6,         # Sunday
        "time": (11, 0),
        "venue": "Charleston Pour House, 1977 Maybank Hwy, James Island, SC",
        "description": (
            "Weekly Sunday farmers + makers market at the Pour House on "
            "James Island. Brunch service running alongside the market."
        ),
        "url": "https://charlestonpourhouse.com/",
        "price": 0.0,
    },
]


def _next_occurrences(weekday: int, hour: int, minute: int) -> list[datetime]:
    """Every occurrence of `weekday` between today and the horizon."""
    today = date.today()
    horizon = today + timedelta(days=config.HORIZON_DAYS)
    # Find the first matching weekday on or after today.
    offset = (weekday - today.weekday()) % 7
    cur = today + timedelta(days=offset)
    out = []
    while cur <= horizon:
        out.append(datetime(cur.year, cur.month, cur.day, hour, minute))
        cur += timedelta(days=7)
    return out


def fetch() -> list[dict]:
    out: list[dict] = []
    for entry in MANUAL_RECURRING:
        h, m = entry["time"]
        for start in _next_occurrences(entry["weekday"], h, m):
            out.append(_common.event(
                title=entry["title"],
                start=start,
                venue=entry["venue"],
                url=entry["url"],
                description=entry["description"],
                price=entry.get("price"),
                source=SOURCE,
            ))
    return out
