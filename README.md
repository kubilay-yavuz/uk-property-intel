# uk-property-intel

**Public monorepo of Python packages for UK property intelligence.**

Eight packages, each publishable as a standalone PyPI wheel, sharing a single toolchain (`uv`, `ruff`, `mypy`, `pytest`) and a single LICENSE.

The consumers of these packages — the MCP servers (`zoopla-mcp`, `rightmove-mcp`, `onthemarket-mcp`) and the private Apify actors repo (`uk-property-apify`) — live in their own sibling repositories and depend on these packages via path or PyPI references.

---

## Packages

| Directory | PyPI name | What it does | Status |
|-----------|-----------|--------------|--------|
| [`packages/scrapers`](packages/scrapers)           | `uk-property-scrapers`     | Pure-Python parsers for Zoopla / Rightmove / OnTheMarket (search + detail + **agent-branch + branch-stock**) and auction houses. Browser-agnostic: takes HTML, returns Pydantic models. Owns the canonical `Listing`, `AuctionLot`, `AgentProfile` / `BranchTeamMember`, `InquiryRequest` / `ViewingRequest` / `FreeValuationRequest` / `InquiryResult`, and `ListingSnapshot` / `ListingChangeEvent` / `SnapshotDiff` schemas shared across every surface. | Alpha |
| [`packages/apis`](packages/apis)                   | `uk-property-apis`         | Typed async clients for 20+ UK government / public APIs: EPC Open Data, HMLR Price Paid, Postcodes.io, data.police.uk, planning.data.gov.uk (+ IDOX HTML / ArcGIS planning), Environment Agency Flood, Companies House, ONS + ONS Nomis, VOA council tax, DEFRA AURN air quality, BGS + radon, Natural England, elevation, coastal erosion, Contracts Finder + Find a Tender, and the four auction-house catalogues. Retries, pagination, Pydantic models. | Alpha |
| [`packages/listings`](packages/listings)           | `uk-property-listings`     | Shared search-URL builders (Zoopla / Rightmove / OnTheMarket), an httpx-only `SimpleCrawler`, and `crawl_*_search` + `crawl_*_urls` pagination / hydration helpers that wire any `CrawlerProtocol` to the `uk-property-scrapers` parsers. | Alpha |
| [`packages/geo`](packages/geo)                     | `uk-property-geo`          | Geospatial primitives: haversine distance, amenity search via Overpass, postcode → coordinates. OSRM/OTP isochrones + H3 grid planned. | Pre-alpha |
| [`packages/data`](packages/data)                   | `uk-property-data`         | Static data loaders for UK government datasets: IMD 2019, Census 2021, MHCLG household projections, UKCP18 climate projections, broadband + elevation grids. | Alpha |
| [`packages/avm`](packages/avm)                     | `uk-property-avm`          | Automated valuation model — hedonic regression, quantile hedonic, LightGBM ensemble + ONS HPI adjuster, neighbourhood features. Trained on PPD + EPC + listings. | Alpha |
| [`packages/agent`](packages/agent)                 | `uk-property-agent`        | LangGraph agent with StructuredTool access to every client above. Tools: `lookup_postcode`, `sold_prices_for_postcode`, `crime_stats_near`, `flood_risk_at`, `amenities_near_postcode`, `epc_certificates_for_postcode`, `estimate_property_value`. | Alpha |
| [`packages/apify_client`](packages/apify_client)   | `uk-property-apify-client` | Typed client for delegating UK-property tool calls to the hosted Apify actor fleet (A1..A14). Thin layer on top of `apify-client` with env-based configuration so MCPs and agent tools can route paying traffic through the moat actors without changing their public surface. | Alpha |

---

## Quickstart

```bash
git clone git@github.com:kubilayyavuz/uk-property-intel.git
cd uk-property-intel
uv sync
uv run pytest -q
```

Expect ~1250 mocked tests across the 8 packages, all green (`uv run pytest --collect-only -q`).

### Is the data actually coming in?

Mocked tests prove the code is consistent with expected API shapes. To prove real APIs return real data, run the live smoke harness:

```bash
uv run python scripts/smoke.py                  # all probes (needs network)
uv run python scripts/smoke.py postcodes overpass ons    # subset by probe id
uv run python scripts/smoke.py --help           # list available probe ids
```

Covers: Postcodes.io, HMLR PPD, data.police.uk, Environment Agency Flood, planning.data.gov.uk, OSM Overpass, EPC Open Data, Companies House, ONS Nomis, Rightmove / Zoopla / OnTheMarket parsers (against bundled HTML fixtures), live HTTP probes of the listing portals (expect Cloudflare 403s), and an end-to-end postcode → amenity chain through the agent's tool layer.

Prints `[OK] <probe> — <summary>` or `[FAIL] <probe> — <reason>` per probe, plus a final green / credential-skip / fail tally.

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
│   └── smoke.py        ← live integration harness (no mocks)
├── README.md           ← this file
├── LICENSE             ← MIT
├── pyproject.toml      ← uv workspace over packages/*
└── .python-version     ← 3.12
```

Each package under `packages/*` has its own `pyproject.toml`, `README.md`, `src/`, and (most of them) a `tests/` directory.

---

## Sibling repositories

This monorepo is the "upstream" that three standalone repos depend on:

| Sibling repo | Visibility | Depends on | Purpose |
|--------------|-----------|------------|---------|
| `zoopla-mcp`         | public  | `uk-property-scrapers[crawler]` | Claude Desktop / Cursor MCP server for Zoopla |
| `rightmove-mcp`      | public  | `uk-property-scrapers[crawler]` | Same, Rightmove |
| `onthemarket-mcp`    | public  | `uk-property-scrapers[crawler]` | Same, OnTheMarket |
| `uk-property-apify`  | private | `uk-property-scrapers[crawler]`, `uk-property-apis` | Apify actors (Zoopla / Rightmove / OTM listings + EPC-PPD unified + future paid scrapers) |

Today those siblings depend on this monorepo via local path references (see `pyproject.toml` in each sibling). Once the 8 packages publish to PyPI, the siblings switch to pinned PyPI versions.

---

## Testing

```bash
# Full mocked test suite (~1250 tests, <15s):
uv run pytest -q

# Scoped per-package:
uv run pytest packages/scrapers -q
uv run pytest packages/apis -q
uv run pytest packages/listings -q
uv run pytest packages/geo -q
uv run pytest packages/data -q
uv run pytest packages/avm -q
uv run pytest packages/agent -q
uv run pytest packages/apify_client -q

# Lint:
uv run ruff check .

# Live smoke (hits real APIs):
uv run python scripts/smoke.py
```

---

## License

MIT — see [LICENSE](LICENSE).
