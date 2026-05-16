"""One scraper per file. Each exposes:

    def fetch() -> list[dict]

Returned dicts use the schema in event_schema.md (top-level: title, start,
end, venue, neighborhood, url, description, price, source). Missing fields
should be None — the scoring layer handles unknowns.

Scrapers should NOT raise on network or parsing errors. Log and return [].
"""
