"""Render the events dashboard as a single self-contained HTML file."""
from __future__ import annotations

import html
from datetime import datetime, date

import score as _score

TIER_COLORS = {
    "high":   "#2d4332",  # forest green — matches the cap
    "medium": "#c8542a",  # rust orange — matches beak/legs
    "low":    "#8a7a5d",  # muted brown — matches palm/distressed border
}

CATEGORY_LABELS = {
    "vintage_market":   "Vintage market",
    "maker_market":     "Maker market",
    "art_fair":         "Art / book fair",
    "art_event":        "Art exhibit",
    "film_screening":   "Film",
    "surf_event":       "Surf",
    "skate_event":      "Skate",
    "car_show":         "Car show",
    "farmers_market":   "Farmers market",
    "food_festival":    "Food",
    "brewery_event":    "Brewery",
    "outdoor_festival": "Festival",
    "outdoor_music":    "Outdoor music",
    "other_drinking":   "Drinking",
    "holiday_event":    "Holiday",
}

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fmt_date(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return f"{WEEKDAYS[v.weekday()]} {v.strftime('%b %-d')} · {v.strftime('%-I:%M %p').lower()}"
    if isinstance(v, date):
        return f"{WEEKDAYS[v.weekday()]} {v.strftime('%b %-d')}"
    return str(v)


def _fmt_price(p) -> str:
    if p is None:
        return "Price unknown"
    if p == 0:
        return "Free"
    if p == int(p):
        return f"${int(p)}"
    return f"${p:.0f}"


_STATE_TOKENS = {"sc", "s.c.", "south carolina"}


def _compact_venue(venue: str | None) -> str:
    """Short display name for the collapsed card.

    - "The Refinery, 1640 Meeting Street Rd., Charleston, SC" → "The Refinery"
    - "1630 Folly Road, Charleston, SC"                       → "1630 Folly Road, Charleston"
    - "Folly Beach Pier"                                       → "Folly Beach Pier"

    The rule: if the first comma-separated chunk looks like a named place
    (no leading digit), use it alone. If it's an address (digit-led), keep
    the street and the first city-like chunk, dropping the state.
    """
    if not venue:
        return ""
    parts = [p.strip() for p in venue.split(",") if p.strip()]
    if not parts:
        return ""
    first = parts[0]
    if first and first[0].isdigit():
        for p in parts[1:]:
            if p.lower().replace(".", "").strip() in _STATE_TOKENS:
                break
            return f"{first}, {p}"
        return first
    return first


def _venue_differs(full: str | None, short: str) -> bool:
    """True iff the full venue carries info beyond what `short` already showed."""
    if not full:
        return False
    return full.strip() != short.strip()


def _event_card(ev: dict, *, hero: bool = False) -> str:
    color = TIER_COLORS.get(ev.get("tier", "low"), "#718096")
    cat = CATEGORY_LABELS.get(ev.get("category") or "", ev.get("category") or "—")
    title = html.escape(ev.get("title") or "Untitled")
    full_venue_raw = ev.get("venue") or ""
    short_venue = _compact_venue(full_venue_raw)
    neighborhood = ev.get("neighborhood") or ""
    desc_full = html.escape(ev.get("description") or "")
    url = ev.get("url")
    when = _fmt_date(ev.get("start"))
    price = _fmt_price(ev.get("price"))
    source = html.escape(ev.get("source") or "")
    score = ev.get("score", 0)

    # Collapsed-card location line: compact venue + neighborhood, deduped
    # if the neighborhood is already embedded in the short venue.
    loc_parts = []
    if short_venue:
        loc_parts.append(html.escape(short_venue))
    if neighborhood and neighborhood.lower() not in short_venue.lower():
        loc_parts.append(html.escape(neighborhood))
    loc_bits = " · ".join(loc_parts)

    # Title link. Inline stopPropagation so clicking the title navigates
    # without also toggling the card.
    if url:
        title_html = (
            f'<a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener" onclick="event.stopPropagation()">{title}</a>'
        )
    else:
        title_html = title

    visit_link = ""
    if url:
        visit_link = (
            f'<a class="visit" href="{html.escape(url, quote=True)}" '
            f'target="_blank" rel="noopener">Visit event page →</a>'
        )

    # Full venue address shown inside the expansion only when it adds info
    # beyond what the collapsed line already displayed.
    full_venue_html = ""
    if _venue_differs(full_venue_raw, short_venue):
        full_venue_html = f'<div class="venue-full">{html.escape(full_venue_raw)}</div>'

    return f"""
    <details class="event {'hero' if hero else ''}" style="--tier:{color}">
      <summary>
        <div class="event-meta">
          <span class="tier-dot"></span>
          <span class="cat">{html.escape(cat)}</span>
          <span class="when">{html.escape(when)}</span>
          <span class="price">{html.escape(price)}</span>
          <span class="score" title="Match score (0–1)">{score:.2f}</span>
        </div>
        <div class="event-title-row">
          <span class="title">{title_html}</span>
          {f'<span class="loc">{loc_bits}</span>' if loc_bits else ''}
        </div>
      </summary>
      <div class="event-body">
        {full_venue_html}
        {f'<p class="desc">{desc_full}</p>' if desc_full else ''}
        <div class="event-foot">
          <span class="src">via {source}</span>
          {visit_link}
        </div>
      </div>
    </details>
    """


def _section(events: list[dict], *, label: str, empty_msg: str) -> str:
    if not events:
        return f'<div class="empty">{html.escape(empty_msg)}</div>'
    top = _score.pick_featured(events, n=3)
    top_ids = {id(e) for e in top}
    rest = [e for e in events if id(e) not in top_ids]
    rest_html = "".join(_event_card(e) for e in rest)
    top_html = "".join(_event_card(e, hero=True) for e in top)
    rest_block = f'<h2>More for {html.escape(label)}</h2>{rest_html}' if rest else ""
    return f"""
    <h2>Top picks — {html.escape(label)}</h2>
    <div class="hero-grid">{top_html}</div>
    {rest_block}
    """


def _recurring_section(events: list[dict], *, label: str) -> str:
    if not events:
        return ""
    top = events[:5]   # show the top 5 recurring picks
    cards = "".join(_event_card(e) for e in top)
    # Collapsed by default — routine markets etc. don't need to be in the
    # main scroll, but stay one click away.
    return f"""
    <details class="recurring-section">
      <summary><h2>Routine weekly / monthly — {html.escape(label)} ({len(top)})</h2></summary>
      <div class="recurring-list">{cards}</div>
    </details>
    """


def _overflow_section(events: list[dict], *, label: str) -> str:
    """Capped-out events that got filtered by the per-venue / per-category
    caps. Collapsed-by-default so the curated view stays clean but the
    full event pool is still one click away."""
    if not events:
        return ""
    cards = "".join(_event_card(e) for e in events)
    return f"""
    <details class="recurring-section">
      <summary><h2>More events at the same venues / categories — {html.escape(label)} ({len(events)})</h2></summary>
      <div class="recurring-list">{cards}</div>
    </details>
    """


def render(*, buckets: dict, fetched_at: str) -> str:
    this_w = buckets["this_weekend"]
    next_w = buckets["next_weekend"]
    last_w = buckets.get("last_weekend") or {}
    show_last = bool(buckets.get("show_last_weekend"))

    this_label = f"{_fmt_date(date.fromisoformat(this_w['range'][0]))} – {_fmt_date(date.fromisoformat(this_w['range'][1]))}"
    next_label = f"{_fmt_date(date.fromisoformat(next_w['range'][0]))} – {_fmt_date(date.fromisoformat(next_w['range'][1]))}"
    last_label = ""
    last_tab_html = ""
    last_panel_html = ""
    if show_last and last_w.get("range"):
        last_label = f"{_fmt_date(date.fromisoformat(last_w['range'][0]))} – {_fmt_date(date.fromisoformat(last_w['range'][1]))}"
        last_tab_html = f"""
      <button class="tab" data-target="last">
        Last weekend
        <span class="tab-range">{html.escape(last_label)}</span>
      </button>"""
        last_panel_html = f"""
    <section class="panel" id="last">
      {_section(last_w.get("events", []), label="last weekend", empty_msg="No events landed in last weekend's window.")}
      {_overflow_section(last_w.get("overflow", []), label="last weekend")}
      {_recurring_section(last_w.get("recurring", []), label="last weekend")}
    </section>"""

    this_weekday_html = ""
    next_weekday_html = ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="900">
<title>Charleston weekend events</title>
<style>
  :root {{
    --bg: #ffffff;            /* white page background */
    --panel: #faf3df;          /* very light cream — event cards */
    --panel-hero: #fcf6e7;     /* even lighter cream — hero cards */
    --panel-edge: #ece1c0;     /* soft cream-edge border */
    --text: #1a2228;           /* dark charcoal — bird body */
    --muted: #6b5f47;           /* warm brown — secondary text */
    --accent: #c8542a;          /* rust orange — beak/legs */
    --primary: #2d4332;         /* forest green — cap */
    --border: #a89274;          /* sandy brown — distressed border */
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 0;
    line-height: 1.45;
  }}
  .wrap {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 24px 20px 80px;
    display: flex;
    align-items: flex-start;
    gap: 36px;
  }}
  header {{
    flex: 0 0 220px;
    text-align: left;
    position: sticky;
    top: 24px;
  }}
  header img.logo {{
    display: block;
    margin: 0 0 8px;
    width: 220px;
    height: auto;
    max-width: 100%;
  }}
  header h1 {{
    position: absolute; width: 1px; height: 1px;
    overflow: hidden; clip: rect(0 0 0 0);
  }}
  header .sub {{ color: var(--muted); font-size: 12px; letter-spacing: 0.02em; }}
  .content {{ flex: 1; min-width: 0; }}
  @media (max-width: 720px) {{
    .wrap {{ flex-direction: column; gap: 16px; padding: 16px; }}
    header {{ flex: 0 0 auto; position: static; text-align: center; }}
    header img.logo {{ margin: 0 auto 6px; width: 160px; }}
  }}
  .tabs {{
    display: flex; gap: 4px;
    margin: 0 0 16px;
    border-bottom: 2px solid var(--border);
  }}
  .tab {{
    background: none;
    border: none;
    color: var(--muted);
    padding: 10px 16px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    border-bottom: 3px solid transparent;
    margin-bottom: -2px;
    font-family: inherit;
    letter-spacing: 0.01em;
  }}
  .tab.active {{ color: var(--primary); border-bottom-color: var(--accent); }}
  .tab-range {{ display: block; font-size: 11px; color: var(--muted);
                 margin-top: 3px; font-weight: 400; }}
  .panel {{ display: none; }}
  .panel.active {{ display: block; }}
  h2 {{
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--primary); margin: 32px 0 12px; font-weight: 700;
  }}
  .hero-grid {{ display: grid; gap: 8px; }}
  .event {{
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-left: 5px solid var(--tier);
    border-radius: 4px;
    margin: 0 0 8px;
    box-shadow: 0 1px 2px rgba(26, 34, 40, 0.06);
    overflow: hidden;
  }}
  .event.hero {{ background: var(--panel-hero); }}
  .event[open] {{ box-shadow: 0 2px 5px rgba(26, 34, 40, 0.10); }}
  /* Compact summary — two short rows of metadata + title, click to expand */
  .event > summary {{
    list-style: none;
    cursor: pointer;
    padding: 10px 14px;
    display: grid;
    grid-template-columns: 1fr 18px;
    gap: 4px 12px;
    align-items: center;
  }}
  .event > summary::-webkit-details-marker {{ display: none; }}
  .event > summary::after {{
    content: "▾";
    grid-column: 2;
    grid-row: 1 / span 2;
    color: var(--muted);
    font-size: 14px;
    transition: transform 0.15s ease;
    align-self: center;
    text-align: center;
  }}
  .event[open] > summary::after {{ transform: rotate(180deg); }}
  .event > summary:hover {{ background: rgba(45, 67, 50, 0.04); }}
  .event-meta {{
    grid-column: 1;
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    font-size: 12px; color: var(--muted);
  }}
  .event-title-row {{
    grid-column: 1;
    display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline;
  }}
  .tier-dot {{
    width: 9px; height: 9px; border-radius: 50%;
    background: var(--tier); flex-shrink: 0;
  }}
  .cat {{
    font-weight: 700; color: var(--primary); text-transform: uppercase;
    letter-spacing: 0.06em; font-size: 11px;
  }}
  .score {{
    margin-left: auto;
    font-variant-numeric: tabular-nums;
    color: var(--text); font-weight: 400; font-size: 11px;
    opacity: 0.55;
  }}
  .title {{ font-size: 15px; line-height: 1.3; font-weight: 700; color: var(--text); }}
  .event.hero .title {{ font-size: 16px; }}
  .title a {{ color: var(--text); text-decoration: none; }}
  .title a:hover {{ color: var(--accent); text-decoration: underline; }}
  .loc {{ color: var(--muted); font-size: 13px; }}
  .loc::before {{ content: "· "; }}
  .event-body {{
    padding: 12px 14px 14px;
    border-top: 1px solid rgba(168, 146, 116, 0.25);
    background: rgba(0, 0, 0, 0.015);
  }}
  .venue-full {{ font-size: 13px; color: var(--muted); margin: 0 0 8px; }}
  .desc {{ margin: 0 0 10px; font-size: 14px; color: #3a3528; line-height: 1.5; }}
  .event-foot {{
    display: flex; flex-wrap: wrap; justify-content: space-between;
    align-items: center; gap: 12px;
  }}
  .src {{ font-size: 11px; color: var(--muted); font-style: italic; }}
  .visit {{
    font-size: 13px; color: var(--accent); font-weight: 600;
    text-decoration: none;
  }}
  .visit:hover {{ text-decoration: underline; }}
  .empty {{ color: var(--muted); padding: 30px 0; text-align: center; font-style: italic; }}
  /* Routine-events drawer */
  .recurring-section {{ margin-top: 32px; }}
  .recurring-section > summary {{
    list-style: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .recurring-section > summary::-webkit-details-marker {{ display: none; }}
  .recurring-section > summary > h2 {{
    margin: 0; display: inline-flex; align-items: center; gap: 8px;
  }}
  .recurring-section > summary::after {{
    content: "▾";
    color: var(--muted);
    font-size: 12px;
    transition: transform 0.15s ease;
  }}
  .recurring-section[open] > summary::after {{ transform: rotate(180deg); }}
  .recurring-list {{ margin-top: 10px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <img class="logo" src="logo.png" alt="Charleston Weekend Events">
    <h1>Charleston weekend events</h1>
    <div class="sub">Updated {html.escape(fetched_at)} · refreshes every 3h</div>
  </header>

  <main class="content">
    <nav class="tabs" role="tablist">
      <button class="tab active" data-target="this">
        This weekend
        <span class="tab-range">{html.escape(this_label)}</span>
      </button>
      <button class="tab" data-target="next">
        Next weekend
        <span class="tab-range">{html.escape(next_label)}</span>
      </button>{last_tab_html}
    </nav>

    <section class="panel active" id="this">
      {_section(this_w["events"], label="this weekend", empty_msg="No events scored above the threshold yet — try widening keywords in config.py.")}
      {this_weekday_html}
      {_overflow_section(this_w.get("overflow", []), label="this weekend")}
      {_recurring_section(this_w.get("recurring", []), label="this weekend")}
    </section>

    <section class="panel" id="next">
      {_section(next_w["events"], label="next weekend", empty_msg="No events scored above the threshold yet for next weekend.")}
      {next_weekday_html}
      {_overflow_section(next_w.get("overflow", []), label="next weekend")}
      {_recurring_section(next_w.get("recurring", []), label="next weekend")}
    </section>{last_panel_html}
  </main>
</div>

<script>
  document.querySelectorAll('.tab').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.target).classList.add('active');
    }});
  }});
</script>
</body>
</html>
"""
