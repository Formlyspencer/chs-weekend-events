"""All the knobs: preferences, weights, area filter, source list."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Geography — events outside these neighborhoods/cities are dropped.
# Matching is fuzzy substring against the venue/address/neighborhood string,
# case-insensitive. Add aliases as you find them.
# ---------------------------------------------------------------------------
INCLUDED_AREAS = [
    "charleston",        # downtown / generic Charleston
    "west ashley",
    "north charleston",
    "mount pleasant",
    "mt pleasant",
    "mt. pleasant",
    "james island",
    "johns island",
    "kiawah",
    "wadmalaw",          # immediately adjacent to Johns Is / Kiawah
    "wadmalaw island",
    "folly beach",
    "folly",
    "daniel island",     # close enough to Mt. P
    "isle of palms",     # close enough to Mt. P
    "iop",
    "sullivan's island",
    "sullivans island",
]

# Explicit excludes — if any of these appear, drop the event even if it
# matched an included area (e.g. an event in "Summerville, near Charleston").
EXCLUDED_AREAS = [
    "summerville",
    "goose creek",
    "moncks corner",
    "hanahan",           # arguable; toggle if Spencer wants it back in
    "hilton head",
    "myrtle beach",
    "murrells inlet",
    "pawleys island",
    "beaufort",
    "savannah",
    "columbia",
    "edisto",
]

# ---------------------------------------------------------------------------
# Category preferences. Score is the base weight (0–1). The single highest
# matching category wins; the score is a multiplier on the final score.
# Add keywords to broaden a category. Keywords match against title + body.
# ---------------------------------------------------------------------------
CATEGORIES = {
    "vintage_market": {
        # True vintage/antique/flea — Pickers Hullabaloo type
        "weight": 1.0,
        "keywords": [
            "pickers hullabaloo", "vintage market", "flea market", "antique",
            "thrift", "vintage fest", "vintage pop-up", "vintage popup",
            "vintage fair", "estate sale",
        ],
    },
    "maker_market": {
        # Artisan / vendor markets — adjacent to vintage but different vibe
        "weight": 0.95,
        "keywords": [
            "makers market", "maker's market", "artisan market",
            "vendor village", "vendor market", "vendors market",
            "craft market", "handmade market", "craft fair",
        ],
    },
    "art_fair": {
        # Book fairs, art fairs, gallery walks — vendor-y but creative-focused
        "weight": 0.85,
        "keywords": [
            "book fair", "art fair", "art walk", "gallery walk",
            "art crawl", "studio tour", "author event", "author signing",
        ],
    },
    "car_show": {
        "weight": 0.70,
        "keywords": [
            "cars & coffee", "cars and coffee", "car show", "car meet",
            "classic car", "motorcycle show", "bike show", "auto show",
        ],
    },
    "farmers_market": {
        # Recurring weekly markets — score lower since these will land in
        # the routine section anyway, but worth keeping the category label.
        "weight": 0.75,
        "keywords": [
            "farmers market", "farmer's market", "farm market",
            "farm-to-table", "produce market",
        ],
    },
    "surf_event": {
        # Surf comps, surf film screenings, surf clinics, plus adjacent
        # outdoor / on-water competitions Spencer's into (fishing tourneys,
        # paddle races, etc.).
        "weight": 1.0,
        "keywords": [
            "surf comp", "surf competition", "surf contest", "surf premiere",
            "surf screening", "surf film", "surf movie",
            "wahine classic", "wahine", "gromfest", "grom fest",
            "longboard classic", "shortboard", "paddle out", "paddle race",
            "kayak race", "carolina cup", "follypalooza",
            "fishing tournament", "fishing tourney", "fishing classic",
            "tarpon tournament", "redfish tournament",
        ],
    },
    "skate_event": {
        "weight": 0.9,
        "keywords": [
            "skate comp", "skate competition", "skate contest",
            "skateboard", "skate jam", "skate session", "skate premiere",
            "skate film", "go skate",
        ],
    },
    "art_event": {
        # Generic gallery exhibitions / openings. Spencer rarely goes — these
        # rank near the bottom by default. The brand-keyword boost (Marsh
        # Wear sponsorship, etc.) can still lift one into the top tier when
        # appropriate, and festival-context art is caught by outdoor_festival.
        "weight": 0.40,
        "keywords": [
            "art opening", "gallery opening", "solo exhibition",
            "art show", "art reception", "exhibition opening",
            "art exhibition", "gallery show", "opening reception",
            "first friday art walk", "vernissage", "art talk", "artist talk",
            "fine art exhibition", "exhibition",
            # Charleston gallery / studio venues
            "meyer vogl gallery", "meyer vogl",
            "redux contemporary", "redux art",
            "halsey institute",
            "charleston crafts gallery", "charleston crafts",
            "lowcountry artists gallery", "lowcountry artists",
            "public works art center",
            "peter anthony fine art", "peter anthony",
            "duckworth gallery", "duckworth's", "duckworths",
            "city gallery", "robert lange studios", "robert lange",
            "michael mitchell gallery",
        ],
    },
    "film_screening": {
        # The "art" Spencer actually cares about: surf / waterman / ocean /
        # boating / skate films and indie screenings. Hed Hi Studio is the
        # classic venue. Higher weight than `art_event` so a real film show
        # ranks above a random gallery opening.
        "weight": 0.85,
        "keywords": [
            "surf film", "surf movie", "surf movie night", "surf screening",
            "surf premiere", "surf documentary",
            "waterman film", "waterman screening", "waterman documentary",
            "ocean film", "ocean film festival", "ocean documentary",
            "boating film", "sailing film",
            "skate film", "skate premiere", "skate screening",
            "film screening", "film premiere", "movie night",
            "hed hi studio", "hed hi",
        ],
    },
    "food_festival": {
        "weight": 1.0,
        "keywords": [
            "food truck", "food festival", "food fest", "bbq fest", "wine + food",
            "wine and food", "oyster roast", "crawfish boil", "shrimp festival",
            "lowcountry boil", "rib fest", "taco fest", "dumpling", "food crawl",
            "food + wine", "restaurant week", "food & wine",
        ],
    },
    "brewery_event": {
        "weight": 0.95,
        "keywords": [
            "brewery", "brewing co", "tap room", "taproom", "beer release",
            "beer fest", "cidery", "cider release", "distillery", "tasting room",
            "trivia at", "yappy hour", "run club", "yoga at",
            "edmunds oast", "revelry", "low tide", "holy city brewing",
            "munkle", "tradesman", "westbrook", "freehouse", "frothy beard",
            "fatty's beer works", "snafu brewing", "ghost monkey",
            "two blokes", "lo-fi brewing", "lo fi brewing",
        ],
    },
    "outdoor_festival": {
        "weight": 0.9,
        "keywords": [
            "festival", "fest", "block party", "street fair", "fair on king",
            "second sunday", "moja", "spoleto", "piccolo spoleto",
            "lowcountry jazz", "high water", "charleston wine + food",
        ],
    },
    "outdoor_music": {
        # Content-only signals — NOT venue names. Venues live in MUSIC_KEYWORDS
        # and bump category weight separately. Putting venue names here would
        # mis-categorize non-music events at music venues (e.g. a book fair
        # whose description happens to mention the venue).
        "weight": 0.75,
        "keywords": [
            "concert in the park", "outdoor concert", "music on the green",
            "live music at", "sunset music", "lawn concert", "concert series",
            "music festival", "free concert", "summer concert",
            "tribute band", "headliner",
        ],
    },
    "other_drinking": {
        # Non-brewery drinking events (wine tastings, cocktail classes, etc).
        # Lower weight than brewery_event, never matches bar crawls.
        "weight": 0.70,
        "keywords": [
            "wine tasting", "cocktail class", "wine dinner", "champagne",
            "rum tasting", "bourbon tasting", "whiskey tasting",
            # Winery / vineyard / wine-themed events
            "winery", "vineyard", "wine-down", "wine down", "wine flight",
            "wine and dine", "wine pairing", "sparkling wine",
        ],
    },
}

# Artists/genres Spencer specifically wants to see — when matched (in title,
# description, OR venue), outdoor_music gets bumped to weight 1.0.
MUSIC_KEYWORDS = [
    # Artists
    "noah kahan",
    "caamp",                  # the indie folk band — note double-a
    "little stranger",
    "dave matthews",
    "dmb",
    "spacey jane",
    # Genres / vibes
    "indie",
    "indie rock",
    "indie folk",
    "folk rock",
    "americana",
    "bluegrass",
    "alt rock",
    "alternative rock",
    "singer-songwriter",
    "singer songwriter",
    # Preferred music venues — events at these places get the 1.0 bump.
    # Limit to venues Spencer has explicitly called out as favorites.
    "the refinery",
    "refinery",
    "firefly distillery",
    "the windjammer",
    "windjammer",
    "music hall",             # Charleston Music Hall
    "charleston music hall",
    "music farm",
]


# Other music venues — used ONLY to categorize events as music in the
# venue-fallback pass. They do NOT trigger the weight bump. A random band
# at Pour House should still be classified as outdoor_music, but shouldn't
# rank above a real festival or food event just because of the venue.
MUSIC_VENUE_HINTS = [
    "pour house",
    "charleston pour house",
    "credit one stadium",     # Daniel Island — concerts/tennis
    "riverfront park",
    "north charleston coliseum",
    "north charleston performing arts",
]

# Frequency-based "uniqueness" boost. Events that happen rarely deserve
# to surface higher than routine programming. Two tiers:
#
#   UNIQUE_KEYWORDS       — strong rarity signal, floors base weight at 1.0
#   UNIQUE_KEYWORDS_SOFT  — weaker signal, floors base weight at 0.85
#
# "premiere" was removed because every touring band claims to premiere
# something. "gala/fundraiser/debut" stayed but were moved to the soft tier
# because they sometimes apply to non-unique events (regular charity nights,
# debut performances by random bands, etc.).
UNIQUE_KEYWORDS = [
    "annual",            # "18th annual Chef's Potluck"
    "annually",
    "biennial",
    "biennially",
    "inaugural",
    "first annual",
    "first-ever",
    "first ever",
    "anniversary",
    "kickoff",
    "kick-off",
    "one night only",
    "one-night-only",
    "limited engagement",
    "benefit concert",
]

UNIQUE_KEYWORDS_SOFT = [
    "gala",
    "fundraiser",
    "debut",
]


# Kid-friendliness signals. Detected once in Python, surfaced as flags in
# events.json so the browser can filter without re-doing regex work. Age
# buckets approximate; users can choose which apply.
KID_FRIENDLY_KEYWORDS = [
    "family-friendly", "family friendly",
    "kid-friendly", "kid friendly",
    "kids", "for kids", "kids'",
    "children", "childrens", "children's",
    "family fun", "family day",
    "all ages", "ages welcome",
]

# Maps a phrase to one or more age buckets:
#   "toddler"     → 0-3
#   "preschool"   → 3-5
#   "elementary"  → 6-10
#   "tween"       → 11-13
#   "teen"        → 14-17
KID_AGE_KEYWORDS = {
    "toddler":       ["toddler", "toddlers", "babies", "infants"],
    "preschool":     ["preschool", "preschoolers", "pre-k", "pre k", "ages 3-5"],
    "elementary":    ["elementary", "ages 5-10", "ages 6-10", "school-age", "school age"],
    "tween":         ["tween", "tweens", "ages 9-12", "ages 11-13", "pre-teen", "preteen"],
    "teen":          ["teen", "teens", "teenager", "high school", "ages 13+", "ages 14+"],
}

# Hard adult-only markers — kid filters should hide these when active.
ADULT_ONLY_KEYWORDS = [
    "21+", "21 and over", "21 and up",
    "18+", "adults only", "adult only",
    "must be 21",
]

# Outdoor / indoor signals — for the v2 outdoor/indoor toggle. Detected once
# in Python, surfaced as flags so the browser can filter cleanly.
OUTDOOR_KEYWORDS = [
    "outdoor", "outdoors", "open air", "open-air",
    "rooftop", "patio", "lawn", "garden",
    "park", "pier", "beach", "waterfront",
    "marina", "courtyard", "plaza",
    "street fair", "block party",
    "tailgate", "outside stage",
]
INDOOR_KEYWORDS = [
    "indoor", "indoors", "inside stage",
    "theater", "theatre", "music hall",
    "gallery", "museum", "library",
    "auditorium", "ballroom", "cinema",
    "warehouse",
]

# Alcohol-centric / drinking-event signals — for the v2 toggle. Categories
# brewery_event / other_drinking already imply drinking, but we surface
# keyword hits too so the browser can hide them even when the category
# vote went elsewhere.
DRINKING_KEYWORDS = [
    "beer release", "beer fest", "beer tasting", "tap takeover",
    "wine tasting", "wine dinner", "wine pairing",
    "cocktail class", "cocktail dinner", "cocktail competition",
    "happy hour",
    "rum tasting", "bourbon tasting", "whiskey tasting", "champagne tasting",
    "byob", "open bar",
    "boozy brunch", "cocktail crawl",
]


# Brands Spencer actively follows — events that mention them (in title,
# description, or venue) get an automatic bump to weight 1.0 regardless of
# category. Use lowercase. Treat any mention as a positive signal.
BRAND_KEYWORDS = [
    # Surf/skate/lifestyle shops
    "mckevlin's", "mckevlins", "mckevlin",
    "dead low", "deadlow",
    "marsh wear",
    # Local food brands Spencer likes
    "hometeam bbq", "home team bbq", "hometeam", "home team",
    "lewis bbq", "lewis barbecue",
    # Art studios
    "hed hi studio", "hed hi", "hedhi",
]


# ---------------------------------------------------------------------------
# Hard excludes — these drop the event entirely (no scoring).
# ---------------------------------------------------------------------------
EXCLUDED_KEYWORDS = [
    "bar crawl",
    "pub crawl",
    "bachelorette",
    "speed dating",
    "singles mixer",
    # Cars & Coffee monthly meet — not Spencer's thing
    "cars & coffee",
    "cars and coffee",
    # Spencer doesn't go to rap shows
    "rap show",
    "rap concert",
    "rap",                # word-boundary protected — won't match "wrap"/"trap"
    "rapper",
    "hip hop",
    "hip-hop",
    # Historical reenactments / war-history exhibits — not Spencer's thing
    "reenactment",
    "re-enactment",
    "civil war",
    "revolutionary war",
    "colonial era",
    "in revolt",          # caught "Charleston in Revolt, 1775" type titles
    "ringleaders of rebellion",
    # Touring musicals / Broadway shows — not Spencer's thing
    "the musical",
    "broadway",
    "national tour of",
]

# Events already attended — dampen but don't drop. Substring match in title.
ATTENDED_BEFORE = [
    "greek festival",
]
ATTENDED_BEFORE_DAMPEN = 0.7  # multiplier applied to repeat festivals

# ---------------------------------------------------------------------------
# Price scoring. price is in USD; None means unknown.
# Multipliers below are applied to the score.
# ---------------------------------------------------------------------------
def price_multiplier(price: float | None) -> float:
    if price is None:
        return 0.85          # unknown — slight haircut, we'd rather know
    if price == 0:
        return 0.95          # free is great, not quite an auto-win
    if price <= 20:
        return 1.00          # sweet spot
    if price <= 40:
        return 0.90
    if price <= 75:
        return 0.75
    if price <= 100:
        return 0.60
    return 0.40              # > $100/ticket significantly lowers priority


# ---------------------------------------------------------------------------
# Day-of-week scoring. 0 = Monday, 6 = Sunday.
# Saturday/Sunday and Friday-night events are best.
# ---------------------------------------------------------------------------
def day_multiplier(weekday: int, hour: int | None) -> float:
    if weekday == 5 or weekday == 6:   # Sat or Sun
        return 1.00
    if weekday == 4:                    # Friday
        # Friday night gets full credit; Friday day a slight haircut.
        if hour is None or hour >= 17:
            return 1.00
        return 0.85
    if weekday == 3:                    # Thursday
        return 0.75
    # Mon–Wed: deprioritized but allowed
    return 0.55


# ---------------------------------------------------------------------------
# Tier thresholds. Score = category_weight * price_mult * day_mult
# (with optional dampen for repeat festivals).
# ---------------------------------------------------------------------------
TIER_HIGH = 0.80
TIER_MEDIUM = 0.55
# anything below MEDIUM is "low" — still shown but at the bottom


# ---------------------------------------------------------------------------
# Distance multiplier. Spencer lives in Folly Beach — events near home rank
# higher; further-out events get a small haircut so a great Mt. P / IOP event
# can still surface but a tied Folly/James Island event wins. Matches against
# the event's detected neighborhood (case-insensitive substring).
# ---------------------------------------------------------------------------
_LOCATION_MULTIPLIERS = [
    # (substring, multiplier) — first match wins, so order by specificity
    ("folly",                 1.00),
    ("james island",          0.96),
    ("johns island",          0.96),   # same tier as James Island per user pref
    ("kiawah",                0.90),
    ("wadmalaw",              0.90),
    ("charleston",            0.92),   # downtown
    ("west ashley",           0.88),
    ("north charleston",      0.82),
    ("mount pleasant",        0.78),
    ("mt pleasant",           0.78),
    ("mt. pleasant",          0.78),
    ("daniel island",         0.75),
    ("isle of palms",         0.72),
    ("sullivan's island",     0.72),
    ("sullivans island",      0.72),
    ("iop",                   0.72),
]


def location_multiplier(neighborhood: str | None) -> float:
    if not neighborhood:
        return 0.85  # unknown — slight haircut
    low = neighborhood.lower()
    for needle, mult in _LOCATION_MULTIPLIERS:
        if needle in low:
            return mult
    return 0.85


# ---------------------------------------------------------------------------
# Per-source fallback URLs — used by the URL validator when an event's link
# is broken (4xx/5xx/timeout). Each value must be a verified-working page on
# the source site.
# ---------------------------------------------------------------------------
SOURCE_LANDING_URLS = {
    "Holy City Sinner":        "https://www.holycitysinner.com/charleston-weekend-events/",
    "CHStoday":                "https://chstoday.6amcity.com/",
    "Charleston City Paper":   "https://www.charlestoncitypaper.com/",
    "Eventbrite":              "https://www.eventbrite.com/d/sc--charleston/all-events/",
    "Explore Charleston":      "https://www.charlestoncvb.com/events/",
    "Hed Hi Studio":           "https://www.hedhistudio.com/shows",
}


# ---------------------------------------------------------------------------
# Sources. Each entry is a module name in sources/ that exposes fetch().
# ---------------------------------------------------------------------------
SOURCES = [
    "sources.manual",                # hand-curated weekly events
    "sources.chstoday",
    "sources.chstoday_newsletter",   # Gmail IMAP
    "sources.holy_city_sinner",
    "sources.charleston_city_paper",
    "sources.eventbrite",
    "sources.explore_charleston",
    "sources.hedhi_studio",
]

# Two-week forecast horizon, starting today.
HORIZON_DAYS = 16

# Request settings for all scrapers.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
