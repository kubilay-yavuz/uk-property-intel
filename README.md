# uk-property-intel

**Public monorepo of Python packages for UK property intelligence.**

Five packages, each publishable as a standalone PyPI wheel, sharing a single
toolchain (`uv`, `ruff`, `mypy`, `pytest`) and a single LICENSE.

The consumers of these packages — the MCP servers (`zoopla-mcp`,
`rightmove-mcp`, `onthemarket-mcp`) and the private Apify actors repo
(`uk-property-apify`) — live in their own sibling repositories and depend on
these packages via path or PyPI references.

---

## Packages

| Directory | PyPI name | What it does | Status |
|-----------|-----------|--------------|--------|
| [`packages/scrapers`](packages/scrapers)   | `uk-property-scrapers` | Pure-Python parsers for Zoopla / Rightmove / OnTheMarket + Playwright crawler with anti-bot tiering. Browser-agnostic: takes HTML, returns Pydantic `Listing`s. | Alpha |
| [`packages/apis`](packages/apis)           | `uk-property-apis`     | Typed async clients for 9+ UK government / public APIs: EPC Open Data, HMLR Price Paid, Postcodes.io, data.police.uk, planning.data.gov.uk, Environment Agency Flood, Companies House, ONS Nomis, OSM Overpass. Retries, pagination, Pydantic models. | Alpha |
| [`packages/geo`](packages/geo)             | `uk-property-geo`      | Geospatial primitives: haversine distance, amenity search via Overpass, postcode → coordinates. OSRM/OTP isochrones + H3 grid planned. | Pre-alpha |
| [`packages/avm`](packages/avm)             | `uk-property-avm`      | Automated valuation model — hedonic regression + XGBoost ensemble trained on PPD + EPC + listings. Scaffolded. | Pre-alpha |
| [`packages/agent`](packages/agent)         | `uk-property-agent`    | LangGraph agent with StructuredTool access to every client above. Tools: `lookup_postcode`, `sold_prices_for_postcode`, `crime_stats_near`, `flood_risk_at`, `amenities_near_postcode`, `epc_certificates_for_postcode`. | Alpha |

---

## Quickstart

```bash
git clone <this repo>
cd uk-property-intel
uv sync
uv run pytest -q
```

Expect ~365 mocked tests across the 5 packages, all green.

### Is the data actually coming in?

Mocked tests prove the code is consistent with expected API shapes. To prove
real APIs return real data, run the live smoke harness:

```bash
uv run python scripts/smoke.py                  # all probes (needs network)
uv run python scripts/smoke.py postcodes overpass ons    # subset by probe id
uv run python scripts/smoke.py --help           # list available probe ids
```

Covers: Postcodes.io, HMLR PPD, data.police.uk, Environment Agency Flood,
planning.data.gov.uk, OSM Overpass, EPC Open Data, Companies House, ONS Nomis,
Rightmove / Zoopla / OnTheMarket parsers (against bundled HTML fixtures), live
HTTP probes of the listing portals (expect Cloudflare 403s), and an end-to-end
postcode → amenity chain through the agent's tool layer.

Prints `[OK] <probe> — <summary>` or `[FAIL] <probe> — <reason>` per probe, plus
a final green / credential-skip / fail tally.

---

## Repo layout

```
uk-property-intel/
├── packages/
│   ├── scrapers/       → uk-property-scrapers
│   ├── apis/           → uk-property-apis
│   ├── geo/            → uk-property-geo
│   ├── avm/            → uk-property-avm
│   └── agent/          → uk-property-agent
├── scripts/
│   └── smoke.py        ← live integration harness (no mocks)
├── PLAN.md             ← full product + engineering plan
├── STATUS.md           ← what's built vs what's left
├── README.md           ← this file
├── LICENSE             ← MIT
├── pyproject.toml      ← uv workspace over packages/*
└── .python-version     ← 3.12
```

Each package under `packages/*` has its own `pyproject.toml`, `README.md`,
`src/`, and (most of them) a `tests/` directory.

---

## Sibling repositories

This monorepo is the "upstream" that three standalone repos depend on:

| Sibling repo | Visibility | Depends on | Purpose |
|--------------|-----------|------------|---------|
| `zoopla-mcp`         | public  | `uk-property-scrapers[crawler]` | Claude Desktop / Cursor MCP server for Zoopla |
| `rightmove-mcp`      | public  | `uk-property-scrapers[crawler]` | Same, Rightmove |
| `onthemarket-mcp`    | public  | `uk-property-scrapers[crawler]` | Same, OnTheMarket |
| `uk-property-apify`  | private | `uk-property-scrapers[crawler]`, `uk-property-apis` | Apify actors (Zoopla / Rightmove / OTM listings + EPC-PPD unified + future paid scrapers) |

Today those siblings depend on this monorepo via local path references (see
`pyproject.toml` in each sibling). Once the 5 packages publish to PyPI, the
siblings switch to pinned PyPI versions.

---

## Testing

```bash
# Full mocked test suite (~365 tests, <10s):
uv run pytest -q

# Scoped per-package:
uv run pytest packages/scrapers -q
uv run pytest packages/apis -q
uv run pytest packages/geo -q
uv run pytest packages/agent -q

# Lint:
uv run ruff check .

# Live smoke (hits real APIs):
uv run python scripts/smoke.py
```

See [STATUS.md](STATUS.md) for the current coverage map, and
[PLAN.md](PLAN.md) for the full product + engineering plan.

---

## License

MIT — see [LICENSE](LICENSE).
