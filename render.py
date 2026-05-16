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


def _event_card(ev: dict, *, hero: bool = False) -> str:
    color = TIER_COLORS.get(ev.get("tier", "low"), "#718096")
    cat = CATEGORY_LABELS.get(ev.get("category") or "", ev.get("category") or "—")
    title = html.escape(ev.get("title") or "Untitled")
    venue = html.escape(ev.get("venue") or "") if ev.get("venue") else ""
    neighborhood = html.escape(ev.get("neighborhood") or "") if ev.get("neighborhood") else ""
    desc = html.escape((ev.get("description") or "")[:240])
    url = ev.get("url")
    when = _fmt_date(ev.get("start"))
    price = _fmt_price(ev.get("price"))
    source = html.escape(ev.get("source") or "")
    score = ev.get("score", 0)

    loc_bits = " · ".join(b for b in [venue, neighborhood] if b)

    return f"""
    <article class="event {'hero' if hero else ''}" style="--tier:{color}">
      <div class="event-head">
        <span class="tier-dot"></span>
        <span class="cat">{html.escape(cat)}</span>
        <span class="when">{html.escape(when)}</span>
        <span class="price">{html.escape(price)}</span>
        <span class="score">{score:.2f}</span>
      </div>
      <h3 class="title">{f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{title}</a>' if url else title}</h3>
      {f'<div class="loc">{loc_bits}</div>' if loc_bits else ''}
      {f'<p class="desc">{desc}</p>' if desc else ''}
      <div class="src">via {source}</div>
    </article>
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
    return f'<h2>Routine weekly / monthly — {html.escape(label)}</h2>{cards}'


def render(*, buckets: dict, fetched_at: str) -> str:
    this_w = buckets["this_weekend"]
    next_w = buckets["next_weekend"]

    this_label = f"{_fmt_date(date.fromisoformat(this_w['range'][0]))} – {_fmt_date(date.fromisoformat(this_w['range'][1]))}"
    next_label = f"{_fmt_date(date.fromisoformat(next_w['range'][0]))} – {_fmt_date(date.fromisoformat(next_w['range'][1]))}"

    this_weekday_html = ""
    if this_w["weekdays"]:
        this_weekday_html = (
            "<h2>Heads-up — weekdays this week</h2>"
            + "".join(_event_card(e) for e in this_w["weekdays"][:8])
        )
    next_weekday_html = ""
    if next_w["weekdays"]:
        next_weekday_html = (
            "<h2>Heads-up — weekdays next week</h2>"
            + "".join(_event_card(e) for e in next_w["weekdays"][:8])
        )

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
  .hero-grid {{ display: grid; gap: 12px; }}
  .event {{
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-left: 5px solid var(--tier);
    border-radius: 4px;
    padding: 14px 16px;
    margin: 0 0 10px;
    box-shadow: 0 1px 2px rgba(26, 34, 40, 0.06);
  }}
  .event.hero {{ padding: 18px 20px; background: var(--panel-hero); }}
  .event-head {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
                 font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
  .tier-dot {{ width: 9px; height: 9px; border-radius: 50%; background: var(--tier); }}
  .cat {{ font-weight: 700; color: var(--primary); text-transform: uppercase;
          letter-spacing: 0.06em; font-size: 11px; }}
  .when {{ }}
  .price {{ }}
  .score {{ margin-left: auto; font-variant-numeric: tabular-nums;
            color: var(--accent); font-weight: 700; font-size: 13px; }}
  .title {{ margin: 4px 0 6px; font-size: 17px; line-height: 1.3; font-weight: 700; }}
  .event.hero .title {{ font-size: 20px; }}
  .title a {{ color: var(--text); text-decoration: none; }}
  .title a:hover {{ color: var(--accent); text-decoration: underline; }}
  .loc {{ color: var(--muted); font-size: 13px; margin-bottom: 6px; }}
  .desc {{ margin: 6px 0 4px; font-size: 14px; color: #3a3528; }}
  .src {{ font-size: 11px; color: var(--muted); margin-top: 8px; font-style: italic; }}
  .stale-badge {{
    font-size: 10px; color: var(--accent);
    border: 1px solid var(--accent);
    padding: 2px 6px; border-radius: 10px;
    text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;
  }}
  .empty {{ color: var(--muted); padding: 30px 0; text-align: center; font-style: italic; }}
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
      </button>
    </nav>

    <section class="panel active" id="this">
      {_section(this_w["events"], label="this weekend", empty_msg="No events scored above the threshold yet — try widening keywords in config.py.")}
      {this_weekday_html}
      {_recurring_section(this_w.get("recurring", []), label="this weekend")}
    </section>

    <section class="panel" id="next">
      {_section(next_w["events"], label="next weekend", empty_msg="No events scored above the threshold yet for next weekend.")}
      {next_weekday_html}
      {_recurring_section(next_w.get("recurring", []), label="next weekend")}
    </section>
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
