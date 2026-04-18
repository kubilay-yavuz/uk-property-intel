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
    print(listing.url, listing.price, listing.address)
```

## Architecture

Parsers are deliberately decoupled from crawlers. The caller (MCP, Apify actor, or agent)
owns the browser/proxy/retry logic. The parser's job is to turn a given `str` of HTML into
a list of `Listing` models with no IO.

Listings from all three sites are normalized to the same canonical `Listing` schema
(`uk_property_scrapers.schema.Listing`) so downstream consumers work with one type.

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
