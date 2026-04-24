# uk-property-intel

> A Python monorepo for UK property intelligence — scrapers, government-API clients, geospatial tools, an AVM, and a natural-language agent that ties them all together.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/packaging-uv-261230.svg?logo=astral)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/badge/lint-ruff-000000.svg?logo=ruff)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/badge/tests-1326%20passing-brightgreen.svg)](#testing)

Eight independently publishable packages, one `uv` workspace, one MIT LICENSE, one Python toolchain (`ruff` + `mypy` + `pytest`).

Downstream consumers — the MCP servers ([`zoopla-mcp`](https://github.com/kubi-yavuz/zoopla-mcp), [`rightmove-mcp`](https://github.com/kubi-yavuz/rightmove-mcp), [`onthemarket-mcp`](https://github.com/kubi-yavuz/onthemarket-mcp)) and the Apify actor fleet — live in sibling repositories and depend on these packages.

---

## Packages

| Directory | PyPI name | What it does |
|-----------|-----------|--------------|
| [`packages/scrapers`](packages/scrapers)           | `uk-property-scrapers`     | Pure-Python parsers for Zoopla / Rightmove / OnTheMarket (search, detail, agent-branch, branch-stock) and the four major UK auction houses. Browser-agnostic: HTML in, Pydantic models out. Owns the canonical `Listing`, `AuctionLot`, `AgentProfile`, inquiry/viewing/valuation and change-snapshot schemas. |
| [`packages/apis`](packages/apis)                   | `uk-property-apis`         | Typed async clients for 20+ UK government and public APIs: EPC Open Data, HMLR Price Paid, Postcodes.io, data.police.uk, planning.data.gov.uk (plus IDOX / ArcGIS planning), Environment Agency Flood, Companies House, ONS + ONS Nomis, VOA council tax, DEFRA AURN air quality, BGS + radon, Natural England, elevation, coastal erosion, Contracts Finder + Find a Tender, and auction-house catalogues. Retries, pagination, and Pydantic models throughout. |
| [`packages/listings`](packages/listings)           | `uk-property-listings`     | Shared search-URL builders (Zoopla / Rightmove / OnTheMarket), an httpx-only `SimpleCrawler`, and `crawl_*_search` / `crawl_*_urls` pagination + hydration helpers typed against a `CrawlerProtocol`. |
| [`packages/geo`](packages/geo)                     | `uk-property-geo`          | Geospatial primitives: haversine distance, amenity search via OSM Overpass, postcode → coordinates. OSRM / OTP isochrones and H3 grids on the roadmap. |
| [`packages/data`](packages/data)                   | `uk-property-data`         | Static data loaders for UK government datasets: IMD 2019, Census 2021, MHCLG household projections, UKCP18 climate projections, broadband + elevation grids. |
| [`packages/avm`](packages/avm)                     | `uk-property-avm`          | Automated valuation model — hedonic regression, quantile hedonic, LightGBM ensemble with ONS HPI adjuster and neighbourhood features. Trained on PPD + EPC + listings. |
| [`packages/agent`](packages/agent)                 | `uk-property-agent`        | LangGraph ReAct agent with 23 `StructuredTool`s bound across every client above. Ships a CLI (`property-agent ask` / `chat` / `serve`) that routes across Anthropic, OpenAI, and Gemini. |
| [`packages/apify_client`](packages/apify_client)   | `uk-property-apify-client` | Typed client for delegating tool calls to the hosted Apify actor fleet. Thin layer on top of `apify-client` with env-based configuration. |

Every package is pre-alpha to alpha — APIs may still shift. Version pinning is recommended.

---

## Quickstart

```bash
git clone https://github.com/kubi-yavuz/uk-property-intel.git
cd uk-property-intel
uv sync
uv run pytest -q
```

The workspace uses [`uv`](https://docs.astral.sh/uv/). Python 3.12+ is required.

### A 60-second tour

Look up a postcode, fetch a sold-price history, and ask the agent a question:

```python
import asyncio
from uk_property_apis import PostcodesIOClient, PricePaidClient

async def main() -> None:
    async with PostcodesIOClient() as pc:
        info = await pc.lookup("CB1 2JW")
        print(info.latitude, info.longitude, info.admin_district)

    async with PricePaidClient() as ppd:
        sales = await ppd.for_postcode("CB1 2JW", limit=10)
        for s in sales:
            print(s.date, s.price, s.paon, s.street)

asyncio.run(main())
```

Parse a Zoopla search page you've already fetched (HTML-in, data-out):

```python
from uk_property_scrapers.zoopla import parse_zoopla_search

listings = parse_zoopla_search(html)
for lot in listings:
    print(lot.price_gbp, lot.bedrooms, lot.address)
```

Run the agent against a real LLM (requires `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY`):

```bash
uv run property-agent ask "Tell me about CB1 2JW"
uv run property-agent ask --stream "Best 3 family homes in Cambridge under £800k"
uv run property-agent chat       # interactive REPL with per-session memory
```

See each package's `README.md` for full usage.

### Is the data actually coming in?

Mocked tests prove the code is consistent with expected API shapes. To prove the real APIs return real data, use the live smoke harness:

```bash
uv run python scripts/smoke.py                              # all probes (needs network)
uv run python scripts/smoke.py postcodes overpass ons       # subset by probe id
uv run python scripts/smoke.py --help                       # list available probe ids
```

Covered probes: Postcodes.io, HMLR PPD, data.police.uk, Environment Agency Flood, planning.data.gov.uk, OSM Overpass, EPC Open Data, Companies House, ONS Nomis, Rightmove / Zoopla / OnTheMarket parsers (against bundled HTML fixtures), live HTTP probes of the listing portals (expect Cloudflare 403s), and an end-to-end postcode → amenity chain through the agent's tool layer.

Each probe prints `[OK] <probe> — <summary>` or `[FAIL] <probe> — <reason>`, with a final green / credential-skip / fail tally.

---

## Repo layout

```
uk-property-intel/
├── packages/
│   ├── scrapers/       → uk-property-scrapers
│   ├── apis/           → uk-property-apis
│   ├── listings/       → uk-property-listings
│   ├── geo/            → uk-property-geo
│   ├── data/           → uk-property-data
│   ├── avm/            → uk-property-avm
│   ├── agent/          → uk-property-agent
│   └── apify_client/   → uk-property-apify-client
├── scripts/
│   ├── smoke.py                     ← live integration harness (no mocks)
│   ├── refresh_fixtures.py          ← capture fresh Rightmove/Zoopla/OTM HTML
│   ├── check_fixture_freshness.py   ← drift audit used by CI
│   ├── capture_agent_fixtures.py
│   └── run_hosted_actors.py
├── .github/workflows/  ← fixture-freshness cron + parser canary
├── pyproject.toml      ← uv workspace over packages/*
├── .python-version     ← 3.12
├── LICENSE             ← MIT
└── README.md
```

Every package has its own `pyproject.toml`, `README.md`, `src/` tree, and (for most) a `tests/` directory.

---

## Sibling repositories

This monorepo is the "upstream" that four standalone repos depend on:

| Sibling repo | Visibility | Depends on | Purpose |
|--------------|------------|------------|---------|
| [`zoopla-mcp`](https://github.com/kubi-yavuz/zoopla-mcp)         | Public  | `uk-property-scrapers[crawler]` | MCP server for Zoopla (Claude Desktop / Cursor) |
| [`rightmove-mcp`](https://github.com/kubi-yavuz/rightmove-mcp)   | Public  | `uk-property-scrapers[crawler]` | MCP server for Rightmove |
| [`onthemarket-mcp`](https://github.com/kubi-yavuz/onthemarket-mcp) | Public  | `uk-property-scrapers[crawler]` | MCP server for OnTheMarket |
| `uk-property-apify`  | Private | `uk-property-scrapers[crawler]`, `uk-property-apis` | Hosted Apify actors (Zoopla / Rightmove / OTM + unified EPC+PPD) with the anti-bot, proxy, and Playwright layer. |

Today the siblings depend on this monorepo via local path references. Once the eight packages publish to PyPI, those references switch to pinned versions.

---

## Testing

```bash
uv run pytest -q                              # full mocked suite (1326 tests, <15s)

uv run pytest packages/scrapers -q            # scoped per package
uv run pytest packages/apis -q
uv run pytest packages/listings -q
uv run pytest packages/geo -q
uv run pytest packages/data -q
uv run pytest packages/avm -q
uv run pytest packages/agent -q
uv run pytest packages/apify_client -q

uv run ruff check .                           # lint
uv run mypy .                                 # type-check

uv run python scripts/smoke.py                # hit real APIs
```

The test count is produced by `uv run pytest --collect-only -q`.

### Continuous integration

[`.github/workflows/fixture-freshness.yml`](.github/workflows/fixture-freshness.yml) runs monthly and on any fixture-related PR. It audits the pinned Rightmove / Zoopla / OnTheMarket / Allsop fixtures against a quarterly freshness target and re-runs every scraper parser test against the committed fixtures as a drift canary. Fixture capture itself is a manual local workflow (`uv run scripts/refresh_fixtures.py`) because all three portals sit behind Cloudflare and will challenge headless CI runners.

---

## Contributing

Issues and pull requests are welcome — especially bug reports for the scrapers when portal markup drifts, and new government-API clients under `packages/apis`. Please:

1. Run `uv run ruff check . && uv run pytest -q` before opening a PR.
2. For new parsers, include an HTML fixture under `packages/scrapers/tests/fixtures/` with a dated filename (see the fixture-freshness workflow for the naming convention).
3. Keep new public surface typed — the whole monorepo is `mypy --strict`.

---

## License

MIT — see [LICENSE](LICENSE).
