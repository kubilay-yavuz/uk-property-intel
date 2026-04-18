# Status — `uk-property-intel`

Public monorepo of Python packages. Siblings (`zoopla-mcp`, `rightmove-mcp`,
`onthemarket-mcp`, `uk-property-apify`) live in their own repos and depend on
packages here.

## Headline numbers

| Metric | Value |
|---|---|
| Packages shipped | 7 (`scrapers`, `listings`, `apis`, `apify_client`, `geo`, `avm`, `agent`) |
| Tests (mocked) | **823 green** across `packages/` |
| Live smoke probes | 15 green (incl. `idox_arcgis_lambeth` + `idox_html_westminster`), 2 credential-skip, 0 fail |

---

## Packages

### `packages/scrapers` — `uk-property-scrapers`
- Parsers for Zoopla, Rightmove, OnTheMarket (HTML → Pydantic `Listing`)
- Fixtures: real 2026-04 HTML snapshots from all 3 portals
- Status: **DONE** for HTML → Listing path. Pure parsers; zero I/O.
- As of 2026-04-18, `crawler/` and `actor_support/` were moved out of this package: the anti-bot moat went to `uk-property-apify-shared` in the private `uk-property-apify` repo, and the httpx-only `SimpleCrawler` + pagination helpers went to the new sibling package below.

### `packages/listings` — `uk-property-listings` (new 2026-04-18)
The "free tier" search layer. httpx-only, no anti-bot moat, no Playwright. Depends on `uk-property-scrapers` for parsers.
- `SearchQuery` + `TransactionKind`
- Per-portal URL builders: `build_zoopla_search_url`, `build_rightmove_search_url`, `build_onthemarket_search_url`
- `SimpleCrawler` — single-GET httpx, realistic Chrome UA, drop-in compatible with the private moat `Crawler`
- `CrawlerProtocol` + `FetcherError` — shared interface types so public pagination catches moat errors too
- Pagination loops: `crawl_zoopla_search`, `crawl_rightmove_search`, `crawl_onthemarket_search`
- Tests: 22
- Status: **DONE**. This is what the three MCPs and the public agent depend on.

### `packages/apis` — `uk-property-apis`
Typed async clients for every free UK property data source:

| Client | Coverage | Notes |
|---|---|---|
| Postcodes.io | Full | Coords + admin geography |
| HMLR Price Paid (SPARQL) | Full | `transaction-record.json` expand path, N+1 fix shipped |
| data.police.uk | Full | Crime counts near coord, handles 1-month publication lag |
| Environment Agency Flood | Full | Flood alerts + stations by postcode |
| planning.data.gov.uk | Full | Entities by dataset, coerces messy string ints |
| OSM Overpass | Full | Amenity search by category, haversine ranking |
| EPC Open Data | Full | Certificates by postcode / LMK key |
| VOA Council Tax | Full | HTML scrape of `tax.service.gov.uk`, CSRF + opaque pagination, `selectolax` parser, Scottish postcode short-circuit |
| Companies House | Full | Company, officers, PSCs, filings, **`search_officers` + `get_officer_appointments` + `build_landlord_graph`** graph primitive (BFS, corporate-PSC unfold, safety caps + `truncated` flag, 16 tests). Requires API key. |
| Idox Public Access | Full | `ArcGISPlanningClient` + `HTMLPlanningClient` + `KNOWN_COUNCILS` registry; feeds agent planning tools + A5 actor; live-smoke green on Lambeth ArcGIS + Westminster HTML |
| ONS Nomis | Full | Dataset versions + Census TS001 extraction |
| Tenders (Contracts Finder + Find a Tender Service) | Full | `ContractsFinderClient` (below-threshold, POST `search_notices`, no auth) + `FTSClient` (above-threshold, OCDS 1.1.5, `CDP-Api-Key` + cursor pagination). Unified `Tender` / `TenderQuery` models + `_normalise.py` with country-code resolution. 81 tests. Feeds A8 `uk-tenders` actor. |
| Base client | — | Retry + exponential backoff (tenacity), HTML-form POST + follow-redirects support, shared error classes |

Status: **DONE**. All clients hit real endpoints and return live data (verified via `scripts/smoke.py`).

### `packages/apify_client` — `uk-property-apify-client` (new 2026-04-18)
Public SDK wrapper for invoking our hosted Apify actors from consumers who want to delegate (MCPs + agent tools). Lets the free tier transparently escalate to the paid moat without linking any moat dependencies.

- `ActorKey` — `Literal` union of all canonical UK-property actor slugs (`zoopla-listings`, `rightmove-listings`, `onthemarket-listings`, `epc-ct-ppd-unified`, `planning-aggregator`, `landlord-network`, `uk-tenders`, …)
- `ActorId("username", "slug")` — frozen dataclass, validates both halves, supports `.from_string("user~slug")` and `APIFY_ACTOR_<KEY>` env override lookup
- `ApifyDelegation` — frozen dataclass carrying resolved call config (`api_token`, `actor_id`, `timeout_s`, `memory_mb`, `build`, `mode`). `.resolve(key, …)` reads `UK_PROPERTY_APIFY_MODE` (`auto`/`off`/`apify`) + `APIFY_API_TOKEN` + `APIFY_USERNAME` + `APIFY_ACTOR_<KEY>` and returns `None` when delegation isn't configured (callers fall back to local paths). `.call(run_input)` lazy-imports `apify-client`, runs the actor, and materialises default dataset + `RUN_META` + `ERRORS` KV records into an `ActorCallResult`.
- `ActorCallResult` — standardised envelope: `status`, `run_id`, `items`, `run_meta`, `errors`, `stats`. Consumers rehydrate items via whatever Pydantic model the target actor guarantees (`Listing`, `PlanningApplication`, `LandlordGraph`, etc.)
- `DelegationError` — wraps missing-SDK / bad-config / non-`SUCCEEDED` / underlying-SDK failures behind a single exception class
- Lazy SDK import: `apify-client` is never imported at module load. Consumers without `APIFY_API_TOKEN` set install zero extra wheels.
- Tests: **48** (`test_apify_client_actors.py` + `test_apify_client_delegation.py`), via a `_FakeApifyClient` that stubs `run()` / `dataset().list_items()` / `key_value_store().get_record()`

Status: **DONE**. This is the linchpin of the dual-mode funnel: MCPs + agent tools both consume it.

### `packages/geo` — `uk-property-geo`
- Haversine distance
- Overpass client (thin wrapper around `packages/apis`'s Overpass) for amenity-at-distance queries
- Postcode-to-coord convenience (via `packages/apis` Postcodes.io)
- **`OverpassAmenitySource`** (new 2026-04-18) — concrete adapter satisfying `uk_property_avm.AmenityDensitySource`. Aggregates `OverpassClient.amenities_near(...)` hits into a `{category_value: count}` mapping keyed by `AmenityCategory.value`, with zero-counts for configured-but-missing categories so the output schema stays stable. Owns-or-borrows an `OverpassClient` (closes the one it created, shares the one passed in). Wire into `NeighbourhoodFeatureExtractor(amenity_source=OverpassAmenitySource(...))` to close the last `None`-typed slot in the AVM v3 feature extractor.

Status: **v1 DONE** + **v3-polish amenity adapter shipped (2026-04-18)**. v2 pending — OSRM/OTP isochrones, H3 grid, polygons (Shapely).

### `packages/avm` — `uk-property-avm`
Eight composable modules (Phase C MVP shipped 2026-04-18; v3 ships same day):

| Module | Role | Tests |
|---|---|---|
| `baseline.py` | Rolling-median tiered estimate (postcode+type → postcode → area+type → area → national). Pure function, no fit step. | 14 |
| `join.py` | PPD + EPC in-memory DuckDB join on normalised `(postcode, paon, street)` key; time-nearest EPC lodgement selection; `prefer_before_sale` / `postcode_fallback` tunables. Produces `EnrichedComparable` rows with `floor_area_sqm` / `energy_rating` / `built_form` / `construction_age_band` + `match_quality` provenance. | 45 |
| `hedonic.py` | Log-price linear regression: `log(price) ~ log(floor_area) + property_type_OHE + tenure_OHE + postcode_area_OHE + age_band_OHE + energy_efficiency_centred`. IQR-residual bands. Falls back to median baseline when pool is thin (<20 rows). Canonical `HedonicModel` class + one-shot `estimate_value_hedonic(target, comparables)`. Exports `_design_row()` / `_build_training_frame()` as shared feature-engineering helpers the v3 quantile + GBM variants reuse. | 27 |
| `eval.py` | Time-based train/test split (`time_based_split(rows, holdout_months=6)`), model-agnostic `evaluate_model()` harness reporting median APE / mean APE / median £-error / coverage + per-(postcode_area × property_type) segment breakdown. Pydantic `EvalReport` + Markdown renderer. | 13 |
| `hpi.py` (v3 + v3-polish) | UK House Price Index adjuster. `HPIAdjuster(series)` + bundled quarterly snapshot (~85 points, 2005-2026, rebased 2015=100) + three loaders — `.from_csv()` / `.from_mapping()` for simple two-column inputs, and **`.from_ons_csv()`** (2026-04-18) for the official ONS UK HPI full-file CSV: filters on `RegionName` (or any column like `AreaCode`) to pick between the UK all-property series, regional aggregates (London / North East / …), or local authorities; supports `IndexSA` and per-type index columns (`IndexDetached`, `IndexFlat`, …) via an `index_col` kwarg; date normaliser handles the three shapes ONS has used (`YYYY-MM-DD`, `YYYY-MM`, `DD/MM/YYYY`). Also ships **`list_ons_regions(path)`** as a discoverability helper for region labels. `.adjust(price, from_date, to_date=None)` does most-recent-on-or-before lookups via binary search; out-of-range raises. Bulk helpers `adjust_comparable_prices(rows, adjuster, to_date=...)` + `adjust_enriched_prices(...)` return HPI-adjusted copies of a row iterable; unparseable / out-of-range rows pass through unchanged (downstream models never see NaNs). | 78 |
| `quantile.py` (v3) | Quantile hedonic. `QuantileHedonicModel(lower=0.1, median=0.5, upper=0.9)` fits three `sklearn.linear_model.QuantileRegressor` models on the shared design matrix from `hedonic.py`. Asymmetric bands from the tail quantiles, not symmetric IQR residuals. Degradation ladder: quantile → mean hedonic → rolling-median baseline; `methodology` tags which tier fired. One-shot `estimate_value_quantile_hedonic(...)`. | 17 |
| `gbm.py` (v3 + v3-polish) | Gradient-boosted hedonic. `GBMHedonicModel` fits three `SklearnQuantileRegressor`-compatible trees (default `sklearn.ensemble.HistGradientBoostingRegressor(loss='quantile')`) with monotonic constraints on `log_floor_area` + `energy_efficiency_c`. Defaults: `max_iter=150`, `learning_rate=0.05`, `max_depth=6`, `min_samples_leaf=10`, `l2_regularization=0.1`. `regressor_factory` injection point is typed against a new `@runtime_checkable` **`SklearnQuantileRegressor` Protocol** (two-method minimum: `.fit(X, y)` + `.predict(X) → np.ndarray`), so any sklearn-compatible quantile regressor slots in. **LightGBM backend** (2026-04-18, v3-polish): **`make_lightgbm_regressor_factory(...)`** returns a `RegressorFactory` that lazy-imports `lightgbm.LGBMRegressor` at fit time — **no top-level dep added** to `uk-property-avm`, raises a precise `ImportError` if `lightgbm` isn't installed. Forwards `quantile` → `alpha`, `monotonic_cst.tolist()` → `monotone_constraints`, plus the six hyperparameters that overlap with the sklearn default (learning rate, max depth, leaves, L2, min-samples-in-leaf, random state). Same three-step fallback ladder. One-shot `estimate_value_gbm_hedonic(...)`. | 32 |
| `features.py` (v3) | `NeighbourhoodFeatureExtractor` composes async-callable `PostcodeGeocoder` + `CrimeStatsSource` + `FloodWarningSource` + `AmenityDensitySource` protocols so `uk-property-avm` stays free of `uk_property_apis` deps (the library defines the interface; consumers wire production sources). Returns a `NeighbourhoodFeatures` Pydantic model with crimes-by-category (trailing 12mo) + violent / burglary subsets + active flood warnings + nearest-station haversine + `stations_within_1km` + amenity-density dict. Bundled `_DEFAULT_STATIONS` dataset (~60 major UK rail stations) with `stations=` override for production coverage. Upstream failures (geocode / crime / flood / amenity) are caught and surfaced as `None` fields — a missing neighbourhood signal never breaks a valuation run. | 24 |

Public surface (re-exported at package root, **44 symbols total** — up from 41 at v3 close): `estimate_value`, `comparables_from_ppd`, `enrich_comparables`, `JoinConfig`, `HedonicModel`, `HedonicTarget`, `estimate_value_hedonic`, `time_based_split`, `evaluate_model`, `format_report_markdown`, `HPIAdjuster`, `adjust_comparable_prices`, `adjust_enriched_prices`, `parse_month_key`, **`list_ons_regions`** (new 2026-04-18), `QuantileHedonicModel`, `estimate_value_quantile_hedonic`, `GBMHedonicModel`, `estimate_value_gbm_hedonic`, **`RegressorFactory`** (new), **`SklearnQuantileRegressor`** (new), **`make_lightgbm_regressor_factory`** (new), `NeighbourhoodFeatures`, `NeighbourhoodFeatureExtractor`, `Station`, `haversine_km`, plus all Pydantic models (`Comparable`, `EnrichedComparable`, `ValuationEstimate`, `EvalReport`, `EvalSegment`) and protocols (`PostcodeGeocoder`, `CrimeStatsSource`, `FloodWarningSource`, `AmenityDensitySource`).

Dependencies unchanged: `pydantic>=2.9`, `numpy>=2.1`, `pandas>=2.2`, `scikit-learn>=1.5`, `duckdb>=1.1`. v3 added zero new top-level deps, and v3-polish adds zero too — `HistGradientBoostingRegressor` and `QuantileRegressor` are both in scikit-learn, LightGBM is a lazy opt-in via `make_lightgbm_regressor_factory()`, ONS CSV parsing is stdlib-only, and `OverpassAmenitySource` lives in `packages/geo` so the `httpx` dep stays out of `uk-property-avm`.

Status: **Phase C MVP + v3 + v3-polish DONE** (263 tests total — +49 from v3 close at 214: +23 ONS HPI + 13 LightGBM factory + 13 GBM protocol/integration; +15 `OverpassAmenitySource` tests live in `packages/geo`). Library-tier now complete on the three v3 polish fronts:
- [x] **Regional HPI series** — `HPIAdjuster.from_ons_csv()` reads the official ONS UK HPI full-file CSV, handles three date formats, filters on `RegionName`/`AreaCode`, supports `IndexSA` and per-property-type index columns; `list_ons_regions()` helper lists discoverable regions. (2026-04-18)
- [x] **LightGBM alternate `regressor_factory` backend** — `make_lightgbm_regressor_factory(...)` with lazy `lightgbm` import; any sklearn-compatible quantile regressor conforming to the new `SklearnQuantileRegressor` Protocol slots in. (2026-04-18)
- [x] **Concrete `AmenityDensitySource`** — `uk_property_geo.OverpassAmenitySource` ships in `packages/geo` (see that package above). (2026-04-18)

Consumer-tier: **A10 `uk-avm` actor dispatcher DONE on 2026-04-18** — `method` + `hpiToDate` + `includeNeighbourhood` knobs now thread through the hosted actor, and `packages/agent`'s `estimate_property_value` tool delegates to it when `APIFY_API_TOKEN` is set. A10 can opt into regional HPI / LightGBM / Overpass amenities via its existing `ClientFactories` DI seam as a one-line override when we want the paid path to adopt the new adapters.

### `packages/agent` — `uk-property-agent`
- LangGraph `StateGraph` with a tool-using ReAct node + response node
- 20 `StructuredTool`s:
  - Listings: `search_zoopla`, `search_rightmove`, `search_onthemarket`
  - Postcodes: `lookup_postcode`, `distance_between_postcodes`
  - Sales: `sold_prices_for_postcode`
  - Area: `crime_stats_near`, `flood_warnings_near`, `listed_buildings_near`, `amenities_near_postcode`
  - Companies: `company_profile`, `company_search`, `company_officers`, `company_psc`, `officer_appointments`, `landlord_network_for_company` (graph traversal — conditional on CH API key)
  - Planning: `list_planning_councils`, `search_planning_applications` (ArcGIS preferred / HTML fallback, `mode='recent'|'search'`), `lookup_planning_application`
  - Valuation: `estimate_property_value` (AVM)
- `ToolContext` DI pattern so tests can inject mocked clients — default crawler factory is `SimpleCrawler.from_env()` from `uk-property-listings`, plus `arcgis_planning_factory` / `html_planning_factory` for per-council Idox injection
- **Dual-mode delegation** (2026-04-18): three tools now transparently delegate to hosted Apify actors when `APIFY_API_TOKEN` is set, falling back to local clients otherwise. The paid path rehydrates remote dataset rows through Pydantic models for byte-for-byte identical tool outputs.
  - `search_planning_applications` → A5 `planning-aggregator` (full private council registry; slugs unknown to the public seed still succeed).
  - `landlord_network_for_company` → A7 `landlord-network` (actor owns the BFS loop + CH rate-limit handling).
  - `estimate_property_value` → A10 `uk-avm` (hedonic / quantile / GBM / median ladder, HPI adjustment against the private ~85-point series, neighbourhood enrichment against the private station list). Extra fields — `method`, `hpi_to_date`, `neighbourhood` — land in the delegated output so callers can tell the paid path apart from the local median fallback.
- CLI: `property-agent "Tell me about CB1 2JW"`
- Supports Anthropic (default), Gemini, OpenAI via LangChain provider map
- Tests: **73** (34 baseline + 7 planning tool + 32 `test_agent_apify_mode.py` covering all three delegation paths + local fallbacks)

Status: **ALPHA**. Core flow works with 20 tools + dual-mode delegation on all three paid-tier-worthy ones. Pending: routing/isochrone tool (after geo v2), prompt caching, structured final output (Pydantic dossier).

---

## Cross-cutting infrastructure

| Item | Status |
|---|---|
| `uv` workspace over `packages/*` | DONE |
| `ruff` + `mypy` configured | DONE (ruff runs in CI plan; mypy strict declared, not yet running) |
| Pydantic v2 throughout | DONE |
| `pytest` + `pytest-asyncio` + `respx` | DONE |
| Live smoke harness `scripts/smoke.py` | DONE |
| GitHub Actions CI | Pending |

---

## Smoke harness — `scripts/smoke.py`

```bash
uv run python scripts/smoke.py              # all probes
uv run python scripts/smoke.py postcodes overpass   # subset by probe id
uv run python scripts/smoke.py --help               # list available probe ids
```

**What it proves**: real APIs and parsers still work end-to-end, using the same
code paths production would. Outputs `[OK]` / `[FAIL]` per probe.

### Probes

| Probe id | Hits | Asserts |
|---|---|---|
| `postcodes` | Postcodes.io | Coords come back for `CB1 2JW` |
| `hmlr` | HMLR Price Paid | `EC1V 3AP` returns ≥1 transaction record (fallback `SE1 9SG`) |
| `police` | data.police.uk | Last 3 months of crime near coord; skips unpublished months |
| `flood` | EA Flood | Alerts + stations for Cambridge |
| `planning` | planning.data.gov.uk | `conservation-area` entities return parseable `Entity` |
| `overpass` | OSM | ≥1 amenity within 500m of a known Cambridge coord |
| `epc` | EPC Open Data | `CB1 2JW` returns ≥1 certificate (if `EPC_API_KEY` set, else skip) |
| `voa` | VOA Council Tax | `EC1V 3AP` returns ≥40 rows across 2+ pages (bands + Islington LA) |
| `ch` | Companies House | `SEARCH` endpoint returns ≥1 result (if `COMPANIES_HOUSE_API_KEY` set, else skip) |
| `ons` | ONS Nomis | `TS001` 2021 edition metadata loads |
| `idox_arcgis_lambeth` | Lambeth Idox FeatureServer | `get_service_info()` returns 5 layers, `count()` returns the global total, `recent_applications(7d, <=5)` succeeds — exercises the exact `search_planning_applications(mode='recent')` code path |
| `idox_html_westminster` | Westminster Public Access (HTML) | `search('Victoria', max=5)` issues the CSRF form GET + `simpleSearchResults.do` POST and parses ≥1 result — proves Westminster is still on Idox Public Access with our result-list schema |
| `rightmove_parser` | Bundled HTML | Search + detail fixtures parse to Pydantic `Listing` |
| `zoopla_parser` | Bundled HTML | Same |
| `onthemarket_parser` | Bundled HTML | Same |
| `listings_live` | Rightmove / Zoopla / OTM | Best-effort HTTP GET; records status + Cloudflare detection |
| `agent_chain` | Postcodes.io + Overpass | End-to-end: postcode → coords → 500m amenity search |

### Bugs this harness caught (now fixed)

1. **HMLR postcode filter**: `transaction.json` silently ignored `postcode=…`. Fixed by switching to `transaction-record.json` which supports the filter and returns pre-expanded records (killed an N+1 at the same time).
2. **Police API 404s on current month**: Police publishes with a ~1-month lag. `PoliceClient.crime_stats_near` now catches `NotFoundError` per month and skips.
3. **Planning API type mismatches**: Live API returns `organisation-entity` as string and `geometry` as `""` for point-only entities. Added `field_validator`s to `Entity`.
4. **ONS dataset defaults**: `dataset_version` defaulted to `edition="time-series"` / `version=1`, which 404s on Census `TS001`. Smoke now passes the correct `edition="2021", version=3`.

---

## What's left for this repo

### Packages
- [x] `packages/listings`: `SearchQuery`, URL builders, `SimpleCrawler`, pagination helpers (2026-04-18)
- [x] `packages/apify_client`: `ActorKey`, `ActorId`, `ApifyDelegation`, `ActorCallResult`, `DelegationError` (2026-04-18)
- [x] `packages/avm`: baseline comparables valuation + `comparables_from_ppd` + 14 tests (2026-04-18)
- [x] `packages/agent`: Companies House tools (5), AVM tool (1), listings search tools (3), planning trio (3), landlord graph (1), dual-mode delegation on planning + landlord (2026-04-18)
- [x] `packages/avm` Phase C MVP: DuckDB PPD+EPC join + log-price hedonic + held-out eval harness, +85 tests (2026-04-18)
- [x] `packages/avm` v3: `hpi.py` + `quantile.py` + `gbm.py` + `features.py`, +115 tests (2026-04-18)
- [x] `packages/avm` v3 → A10 wiring: `method` / `hpiToDate` / `includeNeighbourhood` dispatcher in the `uk-avm` actor + `packages/agent` `estimate_property_value` dual-mode delegation (2026-04-18)
- [x] `packages/avm` v3 polish: regional HPI series loader (`from_ons_csv` + `list_ons_regions`), LightGBM `regressor_factory` (`make_lightgbm_regressor_factory` + `SklearnQuantileRegressor` Protocol), concrete `AmenityDensitySource` backed by `packages/geo` Overpass client (`OverpassAmenitySource`) — all three swap-in only, no A10 actor changes required (2026-04-18)
- [ ] `packages/geo` v2: OSRM / OTP isochrones, H3 grid, Shapely polygons
- [ ] `packages/agent`: isochrone tool (after geo v2 ships)
- [ ] `packages/agent`: prompt caching, multi-provider routing
- [ ] `packages/agent`: structured final output (Pydantic `PropertyDossier` model)

### Infra
- [ ] GitHub Actions CI (lint + test + smoke)
- [ ] mypy strict actually running (declared, not enforced yet)
- [ ] Fixture-drift monitoring on scraper parsers (quarterly re-snapshot)
- [ ] SQLite enricher cache w/ per-source TTLs
- [ ] Per-package CHANGELOG.md + semver policy
- [ ] Release to PyPI (TestPyPI first, then PyPI) for all 5 packages

### Decisions outstanding
- [x] GitHub namespace: `kubilay-yavuz` (for CI + docs)
- [ ] Org vs personal GitHub ownership for public release
- [ ] Brand name (affects domain + later SaaS)
- [ ] PyPI project names (keeping `uk-property-*` or prefixing)
- [ ] LLM provider defaults for `uk-property-agent`
- [ ] OSRM / OTP hosting approach (managed vs self-hosted)

### Once this ships to PyPI
- `zoopla-mcp`, `rightmove-mcp`, `onthemarket-mcp`, and every actor in
  `uk-property-apify` should replace their `[tool.uv.sources]` path entries
  with pinned PyPI versions in `dependencies`.

---

## 2026-04-18 — public/private crawler split

The crawler was split into two tiers so that the public repo never ships the
anti-bot / Playwright stack:

| Tier | Package | Repo | Lives in |
|---|---|---|---|
| Public, httpx-only | `uk-property-listings.SimpleCrawler` + pagination | `uk-property-intel` (here) | `packages/listings/` |
| Private, moat | `uk-property-apify-shared[crawler].Crawler` (curl-cffi, Playwright stealth, anti-bot, Discord alerts, domain rate-limits) | `uk-property-apify` | `shared/src/uk_property_apify_shared/crawler/` |

### Who depends on which tier

| Consumer | Uses |
|---|---|
| `packages/agent` (this repo, public) | `SimpleCrawler` via `uk-property-listings` |
| 3× portal MCPs (separate public repos) | `SimpleCrawler` via `uk-property-listings` |
| 3× listing Apify actors (private) | private `Crawler` via `uk-property-apify-shared[crawler]` |

### Consequences for this repo
1. `packages/scrapers` is now parsers-only (zero I/O). No `[crawler]` extra.
2. Pagination logic (`crawl_zoopla_search`, etc.) lives in `packages/listings` and
   uses a `CrawlerProtocol` so the private moat Crawler is a structural supertype
   of `SimpleCrawler`. Actors can pass either.
3. `FetcherError` lives in `uk-property-listings` so public pagination can
   catch errors raised by the private moat Crawler without importing it.
