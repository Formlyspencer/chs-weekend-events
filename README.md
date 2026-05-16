# Charleston weekend events

A static dashboard of upcoming events in the Charleston SC area (downtown, West Ashley, North Charleston, Mt. Pleasant, James Island, Johns Island, Kiawah, Folly Beach), scored by personal preferences. Tabs for this weekend and next weekend, top 3 events surfaced first, weekday picks shown at the bottom of each tab.

## How it works

1. **Scrapers** in `sources/` pull events from CHStoday, Holy City Sinner, Charleston City Paper, Eventbrite, and Explore Charleston. Each scraper returns a list of normalized event dicts. Failures are logged and don't break the run.
2. **`score.py`** categorizes each event (food, brewery, vintage market, festival, outdoor music, drinking) and computes:
   ```
   score = category_weight · day_of_week_mult · price_mult · attended_before_dampen
   ```
   Events that hit an excluded keyword (e.g. "bar crawl") are dropped. Uncategorized events are dropped — expand the keyword lists in `config.py` rather than relax the filter.
3. **`render.py`** writes a single self-contained `docs/index.html` with two tabs and a top-3 hero row per weekend.
4. **GitHub Actions** runs the pipeline every 3 hours and commits the regenerated HTML.

## Tuning

Almost everything lives in `config.py`:

- `CATEGORIES` — keyword → weight. Bump weight to surface more of a category, add keywords to broaden it.
- `MUSIC_KEYWORDS` — currently empty; add artist names / genres you actively want to see. Matching events get bumped to weight 1.0.
- `EXCLUDED_KEYWORDS` — events that drop entirely.
- `ATTENDED_BEFORE` — names of recurring events you've already done; weight is multiplied by `ATTENDED_BEFORE_DAMPEN`.
- `price_multiplier()` — the price → score curve.
- `day_multiplier()` — Sat/Sun/Fri-night = 1.0, Thu = 0.75, Mon–Wed = 0.55.
- `TIER_HIGH` / `TIER_MEDIUM` — bucket cutoffs for the color dot.
- `INCLUDED_AREAS` / `EXCLUDED_AREAS` — geography gating.

After editing, run `python3 main.py` to regenerate locally.

## Local use

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py
open docs/index.html
```

The pipeline also writes `docs/events.json` — useful for poking at what each scraper returned and what got categorized as what.

## Deploying to GitHub Pages

1. **Create a new GitHub repo** (e.g. `chs-weekend-events`). Public is fine.
2. **Push this directory:**
   ```bash
   git init
   git add .
   git commit -m "initial"
   git branch -M main
   git remote add origin git@github.com:<you>/chs-weekend-events.git
   git push -u origin main
   ```
3. **Enable Pages.** Settings → Pages → Source: Deploy from a branch → main → `/docs` → Save.
4. **First run.** Actions → "Update events" → Run workflow. After ~30s, the page is published at `https://<you>.github.io/chs-weekend-events/`.

The page has a 15-min HTML meta-refresh and the Actions workflow regenerates the data every 3 hours.

## Gmail setup (CHStoday newsletter scraper)

CHStoday's website is a JavaScript SPA we can't scrape, but the daily email
newsletter has the same content as plain HTML. This source connects to
Gmail over IMAP with an App Password (no OAuth, no expiring tokens) and
pulls recent newsletters for parsing.

**This is isolated from any Claude / agent connector** on your work
account. The IMAP connection authenticates against your personal Gmail
only, using a password scoped to this app that you can revoke independently
at any time. The pipeline only reads emails — never sends, never modifies.

### One-time setup (~3 minutes)

You need **2-Step Verification enabled** on your Google account first. If
it's not, turn it on at https://myaccount.google.com/security → "2-Step
Verification".

1. **Generate an App Password.** Go to https://myaccount.google.com/apppasswords.
   - App name: `chs-weekend-events` (or anything memorable).
   - Click **Create**.
   - Google shows a 16-character password (with spaces — they don't matter,
     paste the whole thing). **Copy it now** — you can't see it again.

2. **Add two secrets to the GitHub repo.**
   Repo → **Settings** → **Secrets and variables** → **Actions** → **New
   repository secret**. Add:

   | Name            | Value                                          |
   |-----------------|------------------------------------------------|
   | `IMAP_USERNAME` | your full personal Gmail address               |
   | `IMAP_PASSWORD` | the 16-character app password from step 1      |

3. **Trigger a run** to verify. Actions → "Update events" → Run workflow.
   The logs should show `CHStoday newsletter: N emails matched query`
   instead of "skipping". If you see an IMAP login failure in the logs,
   double-check the password (paste exactly what Google showed you, spaces
   are fine).

Until the secrets are set, the scraper is a no-op and silently returns no
events (the rest of the pipeline keeps working).

### Revoking access

Two ways:
- App-Passwords page (https://myaccount.google.com/apppasswords) → find
  the `chs-weekend-events` entry → **Remove**. Instant.
- Delete the `IMAP_PASSWORD` secret from the repo. The scraper goes back
  to being a no-op.

## Iterating on scrapers

Scrapers are best-effort. Two failure modes are normal:

- **A site changes layout.** Look at `docs/events.json` to see what each source returned. Adjust the parser in `sources/<site>.py`. The framework is heading-based — most weekly-roundup blog posts use H2/H3 per event, which is what `_extract_events_from_post` keys on.
- **A new source you'd like to add.** Drop a new file into `sources/` exposing `fetch() -> list[dict]`, then list it in `config.SOURCES`. Use `sources._common.event(...)` to build the dict so all fields are normalized.

## Files

- `config.py` — all knobs (categories, weights, areas, sources, tier cutoffs).
- `sources/` — one scraper per site, plus `_common.py` for shared helpers.
- `fetch.py` — runs all sources, dedupes by (title, date).
- `score.py` — categorize, score, bucket by weekend.
- `render.py` — HTML template.
- `main.py` — orchestrates fetch → score → render → write `docs/`.
- `.github/workflows/update.yml` — cron + push to refresh.
- `docs/` — published page + events.json side-car.
