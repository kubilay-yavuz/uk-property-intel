# uk-property-scrapers

Pure-Python parsers for UK property listing portals. **No browser. No network.** Takes HTML in, returns Pydantic models out.

This design makes parsers fast, testable against saved fixtures, and re-usable across:
- The `zoopla-mcp` and `rightmove-mcp` OSS MCP servers
- The `uk-property-apify` production Apify actors
- The `uk-property-agent` OSS natural-language agent

## Install

```bash
pip install uk-property-scrapers
```

## Usage

```python
from uk_property_scrapers.zoopla import parse_search_results

with open("page.html") as f:
    html = f.read()

for listing in parse_search_results(html):
    # Canonical `Listing` fields are side-agnostic.
    # `source_url` is the detail-page URL; price lives in either
    # `sale_price` or `rent_price` depending on `transaction_type`.
    price = listing.sale_price or listing.rent_price
    print(listing.source_url, price.raw if price else "POA", listing.address.raw)
```

If you need a detail page, use `parse_detail_page` — it returns a single
`Listing` with full photos, description, coordinates, and features:

```python
from uk_property_scrapers.zoopla import parse_detail_page

listing = parse_detail_page(html, source_url="https://www.zoopla.co.uk/for-sale/details/72228361/")
assert listing is not None
print(listing.coords, len(listing.image_urls))
```

## Architecture

Parsers are deliberately decoupled from crawlers. The caller (MCP, Apify actor, or agent)
owns the browser/proxy/retry logic. The parser's job is to turn a given `str` of HTML into
a list of `Listing` models with no IO.

Listings from all three sites are normalized to the same canonical `Listing` schema
(`uk_property_scrapers.schema.Listing`) so downstream consumers work with one type.
Key fields: `source` / `source_id` / `source_url`, `transaction_type`,
`sale_price` / `rent_price` (only one populated at a time), `address`,
`coords`, `bedrooms` / `bathrooms`, `image_urls` (with `caption="floorplan"`
for floorplan variants), `features`, `agent`.

## Supported sources

| Source        | Search results | Detail page | Status  |
|---------------|----------------|-------------|---------|
| Zoopla        | yes            | yes         | stable  |
| Rightmove     | yes            | yes         | stable  |
| OnTheMarket   | yes            | yes         | stable  |

All three are exercised by the shared production crawler
(`uk_property_scrapers.crawler`) which provides a two-tier HTTP +
Playwright fallback, domain-aware rate limiting, curl-cffi TLS
impersonation, Discord maintenance alerts, and anti-bot detection.

## Optional extras

```bash
pip install "uk-property-scrapers[crawler]"   # full production crawler
pip install "uk-property-scrapers[playwright]" # browser fallback only
```
