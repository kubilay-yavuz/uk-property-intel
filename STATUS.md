# Status — `uk-property-intel`

Public monorepo of Python packages. Siblings (`zoopla-mcp`, `rightmove-mcp`,
`onthemarket-mcp`, `uk-property-apify`) live in their own repos and depend on
packages here.

## Headline numbers

| Metric | Value |
|---|---|
| Packages shipped | 8 (`scrapers`, `listings`, `apis`, `apify_client`, `geo`, `avm`, `agent`, `data`) |
| Tests (mocked) | **1138 green** across `packages/` (+180 in `packages/agent` — Agent v4 added 22 Chainlit-renderer tests + 14 REPL tests + 6 checkpointer tests on top of v3, and v4.1 added 3 tool-error-handling tests + 5 Chainlit self-heal tests after fixing the Gemini "orphan tool_call" regression; minus 4 Gradio-era tests removed with the `web.py` deletion) |
| Agent surfaces | **3**: `property-agent ask` (one-shot CLI), `property-agent chat` (REPL), `property-agent serve` (Chainlit web UI). All share the same `PropertyAgent` + tool context + checkpointer. |
| Live smoke probes | **18 green** (18 API-level: postcodes, HMLR, police, EA flood, planning.data, Overpass, ONS Beta, **Nomis (generic `observations`)**, **Natural England (4/5 layers post-2025 Defra migration)**, **Contracts Finder**, VOA, Idox ArcGIS Lambeth, Idox HTML Westminster, Rightmove/Zoopla/OnTheMarket parsers, listings live GET, agent chain), **2 credential-skip** (EPC + Companies House), **0 fail** |

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
| Environment Agency Flood | Full | Flood alerts + stations by postcode. **`MonitoringStation` hardened (2026-04-19)** to accept both list and URL-string shapes for `stage_scale` / `measures` — the live API returns either depending on the station. |
| Environment Agency Coastal Erosion | **Upstream-deprecated (2026-04-19)** | All methods raise `RuntimeError` with `_NCERM_MIGRATION_MESSAGE`. The DEFRA NCERM 2024 rebuild moved to BNG coordinates + a new layer schema; client needs a full rewrite. Downstream A11 surfaces this as a `partial_errors` row without failing the run. |
| British Geological Survey | **Upstream-deprecated (2026-04-19)** | All geohazard methods raise `RuntimeError` with `_BGS_MIGRATION_MESSAGE`. BGS withdrew the public ArcGIS REST layers we consumed. A11 surfaces this as a `partial_errors` row. |
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

- `ActorKey` — `Literal` union of all canonical UK-property actor slugs (`zoopla-listings`, `rightmove-listings`, `onthemarket-listings`, `epc-ct-ppd-unified`, `planning-aggregator`, `landlord-network`, `uk-tenders`, `uk-demographics`, `uk-avm`, **`uk-climate-risk`** (added 2026-04-19), **`uk-location-intel`** (added 2026-04-19))
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
- **OSRM + OTP routing clients** (`OSRMClient`, `OTPClient`, `NaPTANLookup`, `OverlayEngine`, `H3Grid`) carried over from geo v2 work.
- **`OSRMClient.isochrone(...)`** (new 2026-04-19) — synthesises a driving / walking / cycling isochrone from the vanilla OSRM `/table` endpoint by fanning out a **`radial_grid(lat, lng, step_m, max_radius_m)`** of destination probes. Returns a typed :class:`DriveIsochrone` — reachable points, per-cutoff counts, and per-cutoff max-reach distance. No hull polygon (Shapely stays an opt-in dep); callers can post-process the grid if they need a polygon. Shared between A12 `uk-location-intel` and the agent's `drive_time_isochrone` tool.

Status: **v1 DONE** + **v2 routing/isochrones DONE** + **v3-polish amenity adapter shipped**.

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
- **23 `StructuredTool`s** (17 listed below + the three optional Companies House tools when `COMPANIES_HOUSE_API_KEY` is set):
  - Listings: `search_zoopla`, `search_rightmove`, `search_onthemarket`
  - Postcodes: `lookup_postcode`, `distance_between_postcodes`
  - Sales: `sold_prices_for_postcode`
  - Area: `crime_stats_near`, `flood_warnings_near`, `listed_buildings_near`, `amenities_near_postcode`
  - Companies (when CH API key present): `company_profile`, `company_search`, `company_officers`, `company_psc`, `officer_appointments`, `landlord_network_for_company`
  - Planning: `list_planning_councils`, `search_planning_applications` (ArcGIS preferred / HTML fallback, `mode='recent'|'search'`), `lookup_planning_application`
  - Valuation: `estimate_property_value` (AVM)
  - **Travel-time (v2, 2026-04-19)**: `drive_time_isochrone` (OSRM radial-grid) + `transit_isochrone` (OTP native). Both auto-delegate to the hosted A12 `uk-location-intel` actor when `APIFY_API_TOKEN` is set; otherwise require `OSRM_BASE_URL` / `OTP_BASE_URL`.
  - **One-shot dossier (v2, 2026-04-19)**: `build_property_dossier` fans out across postcodes.io, HMLR, Overpass, listed-buildings, police, EA Flood, EPC (if creds) + Idox planning (when the postcode's council is in the public reference registry) in parallel via `asyncio.gather(return_exceptions=True)`. Returns a typed :class:`PropertyDossier` — every block nullable, partial failures logged as `DossierError` rows so one upstream outage never tanks the whole response.
- `ToolContext` DI pattern so tests can inject mocked clients — default crawler factory is `SimpleCrawler.from_env()` from `uk-property-listings`, plus `arcgis_planning_factory` / `html_planning_factory` for per-council Idox injection and **`osrm_factory` / `otp_factory`** (2026-04-19, auto-wired from `OSRM_BASE_URL` / `OTP_BASE_URL`)
- **Dual-mode delegation**: five tools now transparently delegate to hosted Apify actors when `APIFY_API_TOKEN` is set, falling back to local clients otherwise. The paid path rehydrates remote dataset rows through Pydantic models for byte-for-byte identical tool outputs.
  - `search_planning_applications` → A5 `planning-aggregator` (full private council registry; slugs unknown to the public seed still succeed).
  - `landlord_network_for_company` → A7 `landlord-network` (actor owns the BFS loop + CH rate-limit handling).
  - `estimate_property_value` → A10 `uk-avm` (hedonic / quantile / GBM / median ladder, HPI adjustment against the private ~85-point series, neighbourhood enrichment against the private station list). Extra fields — `method`, `hpi_to_date`, `neighbourhood` — land in the delegated output so callers can tell the paid path apart from the local median fallback.
  - **`drive_time_isochrone` + `transit_isochrone` → A12 `uk-location-intel`** (2026-04-19) — both reshape the actor's `isochrone_drive` / `isochrone_transit` block back to the local tool's payload shape so the LLM sees one contract regardless of transport.
- **Provider-aware prompt caching** (v2 → v3, 2026-04-19): the ~6.5 kB system prompt is emitted in a shape that matches each provider's caching mechanism. Anthropic gets a content-block with `cache_control={"type": "ephemeral", "ttl": "5m"}` so Claude caches the static prefix across turns. OpenAI + Gemini get a plain string that their SDKs auto-cache when the prefix clears their token threshold (1024 for OpenAI, Gemini's implicit cache). Opt out with `PropertyAgent(enable_prompt_cache=False)`; override TTL with `cache_ttl="1h"` (Anthropic-only). Also exported `build_system_message(provider=...)` + `cache_shape_for(provider)` + `CACHEABLE_SYSTEM_PROMPT` so callers can layer domain-specific context on top of the cached prefix.
- **Multi-provider routing** (v3, 2026-04-19) — `uk_property_agent.providers` module. `Provider` `StrEnum` (`anthropic` / `openai` / `gemini`), `TaskKind` (`default` / `dossier` / `analysis` / `tool_plan`), `ProviderSpec` frozen dataclass, `resolve_provider(task, *, explicit, env)` walks a five-level precedence chain (explicit `ProviderSpec` / enum / slug → `AGENT_MODEL_<TASK>` env → `AGENT_MODEL_DEFAULT` env → `AGENT_PROVIDER` env → `AGENT_PROVIDER_CHAIN` first-available), `build_chat_model(spec)` with lazy `langchain-anthropic` / `langchain-openai` / `langchain-google-genai` imports so the default install stays slim. Per-provider default models (`claude-sonnet-4-5-20250929` / `gpt-4o` / `gemini-2.5-pro`). `has_credentials` / `select_available_provider` / `describe_env` for diagnostics + fallback plumbing. `PropertyAgent(provider=..., task=...)` wires the resolved spec into `build_chat_model` + provider-aware `build_system_message`; `model=` and `provider=` are mutually exclusive.
- **Optional dependency groups** (v3): `uk-property-agent[anthropic]` / `[openai]` / `[gemini]` / `[all]` in `packages/agent/pyproject.toml` so tooling around a specific provider doesn't pull the other two SDKs.
- **Streaming narrative output (v3 polish, 2026-04-19)**: `PropertyAgent.astream_narrative(question)` — token-level async iterator over the final LLM narrative, powered by LangGraph's `stream_mode="messages"`. Filters out tool-call chunks so the caller only sees prose. `PropertyAgent.astream_events(question)` emits a structured event stream — `{"type": "tool_call"\|"tool_result"\|"narrative_chunk"\|"final"}` — for richer UIs that want to interleave tool reasoning with streamed prose. The existing `astream(question)` stays around for graph-level state updates. All three methods gracefully fall back to a buffered final emission when the underlying chat model doesn't expose token streaming (e.g. some fakes and older OpenAI routes), so the return shape is stable regardless of provider.
- CLI: `property-agent ask "Tell me about CB1 2JW"`, `property-agent ask --stream "..."` (narrative tokens → stdout, tool-call + tool-result events → stderr; plain buffered print without `--stream`), `property-agent ask --provider openai/gpt-4o --task dossier "..."`, `property-agent ask --show-provider "..."`, `property-agent env` (table view) / `property-agent env --json` (machine-readable LLM + infra snapshot).
- **Multi-turn memory (v4, 2026-04-19)**: `PropertyAgent(checkpointer=..., thread_id=...)` wires a LangGraph checkpointer so successive calls against the same `thread_id` see the full `messages` list. Default checkpointer is `langgraph.checkpoint.memory.InMemorySaver` (per-process, no network). `ainvoke`, `astream`, `astream_narrative`, and `astream_events` all accept an optional `thread_id=` kwarg that overrides the instance default — the REPL and the Chainlit app use this to key memory per conversation / per tab.
- **Interactive REPL (v4)**: `property-agent chat` drops into a terminal loop backed by the memory checkpointer. Slash commands mirror the `ask` flags so a single session can hop providers + toggle streaming without restarting: `/help`, `/clear` (reset thread, keep provider), `/provider SLUG`, `/tools`, `/stream on|off`, `/exit`. Implementation in `uk_property_agent.repl` (`ChatLoop`, `ChatLoopOptions`, `build_chat_loop`) is framework-free — takes injected stdin/stdout + a session factory, so the same logic is reused by the CLI wiring and exercised in tests without a TTY.
- **Chainlit web UI (v4)**: `property-agent serve` launches a locally-hosted web chat over the same agent. Two-module split — `chainlit_app.py` holds only the `@cl.on_chat_start` / `@cl.on_message` / `@cl.set_starters` decorators and shells out to `module_path()` for the CLI; `chainlit_render.py` owns the `ChainlitRenderer` dataclass, `ChainlitSession`, `build_session`, and `swap_provider`. The split is mandatory because Chainlit loads the entry-point file via `importlib.util.spec_from_file_location` without registering it in `sys.modules`, which breaks `@dataclass` forward-ref resolution. `ChainlitRenderer` translates `astream_events` (`tool_call` → `tool_result` → `narrative_chunk` → `final`) into `cl.Step` + `cl.Message` components, correlating tool call and result events by `tool_call_id` so parallel tool calls render as independent collapsible panels. Provider / task / temperature flow through as `PROPERTY_AGENT_WEB_*` env vars so the Chainlit module has no argparse coupling. On first `property-agent serve` the CLI seeds `~/.uk-property-agent/web/` with bundled `chainlit.md` + `.chainlit/config.toml` (app name, dark theme, wide layout, four starter prompts) so the demo is branded out of the box without polluting the cwd. Demo-only: no auth, no rate limiting, no persistence.
- **`[web]` optional extra**: `uk-property-agent[web]` pulls in `chainlit>=2.0`. `chainlit_config.toml` + `chainlit.md` are force-included in the wheel via `[tool.hatch.build.targets.wheel.force-include]`.
- Tests: **172** (134 v3 close + 42 new in v4 − 4 deleted Gradio tests): `test_agent_repl.py` (14 — slash commands, streaming + non-streaming output, history reset semantics, provider swap preserving `thread_id`), `test_agent_chainlit_app.py` (22 — `ChainlitRenderer` against `FakeMessage`/`FakeStep` fakes exercising narrative streaming, parallel tool-call-id correlation, orphaned tool results, `_short_input` / `_short_output` truncation, `build_session` + `swap_provider` preserving checkpointer + thread id), `test_agent_graph.py` gained 6 tests covering `checkpointer=` + `thread_id=` plumbing across `ainvoke` / `astream_narrative` / `astream_events`.

Status: **ALPHA (v4 interactive shipped 2026-04-19)**. Core flow works with 23 tools + dual-mode delegation on five paid-tier-worthy paths + isochrones + structured dossier + provider-aware prompt caching + multi-provider routing across Anthropic / OpenAI / Gemini + streaming narrative + **three interactive surfaces (CLI `ask`, CLI `chat` REPL, Chainlit web demo) sharing the same agent + memory checkpointer**. Next: deprecating the per-source tools once the model reliably picks the dossier first; optional migration from `InMemorySaver` to `SqliteSaver` or `PostgresSaver` once a SaaS-vs-CLI decision is made on conversation persistence.

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
- [x] `packages/geo` v2: OSRM / OTP routing clients, H3 grid, overlays, NaPTAN lookup, **`OSRMClient.isochrone()` radial-grid helper** (2026-04-19)
- [x] `packages/agent`: isochrone tools — `drive_time_isochrone` (OSRM radial grid) + `transit_isochrone` (OTP native) with auto-delegation to A12 `uk-location-intel` (2026-04-19)
- [x] `packages/agent`: Anthropic prompt caching on the system prompt via `build_system_message()` content blocks + `cache_ttl` knob (2026-04-19)
- [x] `packages/agent`: structured final output — `PropertyDossier` Pydantic model + `build_property_dossier` orchestrator returning a typed dossier across postcode / AVM / PPD / neighbourhood / crime / flood / EPC / planning, with partial-failure surfacing (2026-04-19)
- [x] `packages/agent`: **multi-provider routing + per-task model pinning + provider-aware prompt caching** — `providers.py` module (Anthropic / OpenAI / Gemini), `AGENT_MODEL_<TASK>` env, `[anthropic]` / `[openai]` / `[gemini]` / `[all]` optional-deps, CLI `--provider` / `--model` / `--task` / `--show-provider` + `env --json` (2026-04-19)
- [x] `packages/agent`: **streaming narrative output** via LangGraph `stream_mode="messages"` — `PropertyAgent.astream_narrative(...)` + `astream_events(...)` + CLI `--stream` flag (narrative → stdout, tool events → stderr); graceful fallback to buffered emission on models without token streaming (2026-04-19)
- [x] `packages/agent`: **interactive surfaces (v4)** — `InMemorySaver` checkpointer + `thread_id` plumbing, `property-agent chat` REPL with slash commands, `property-agent serve` Chainlit web demo (branded landing, per-tab memory, collapsible tool steps, four starter prompts), `[web]` optional extra, LangSmith env check in `property-agent env` (2026-04-19)
- [x] `packages/agent`: **MCP sibling repo cut from roadmap** — `uk-property-agent-mcp` deleted; demo surface is the Chainlit web UI, not Claude Desktop / Cursor. The agent's public API is MCP-ready and the wrapper can be brought back later if the audience shifts (2026-04-19)

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

## 2026-04-19 — Agent v2 (isochrones + dossier + prompt caching)

Three things shipped together because they compound — one structured dossier
call replaces what used to be 6-8 round-trips through individual tools, and
the cached system prompt means even long chat sessions don't re-pay the tool
catalog tokens on every turn.

### What landed

| Piece | Where | Notes |
|---|---|---|
| `DriveIsochrone` model + `radial_grid(...)` + `OSRMClient.isochrone(...)` | `packages/geo/src/uk_property_geo/osrm.py` | Fans out a ring grid of destination points around the origin and hits OSRM `/table/v1/{profile}` once for all of them. Buckets reachable points per cutoff and surfaces max-reach distance. No hull polygon (Shapely stays opt-in). |
| `drive_time_isochrone` + `transit_isochrone` tools | `packages/agent/src/uk_property_agent/isochrone.py` + `tools.py` | Local path uses `OSRMClient` / `OTPClient`; delegation path fires A12 `uk-location-intel` with `{"sources":["isochrone_drive"\|"isochrone_transit"],"isochroneCutoffsMin":[...]}`. Both paths return the exact same payload shape so the LLM never sees a transport distinction. |
| `ToolContext.osrm_factory` / `otp_factory` | `packages/agent/src/uk_property_agent/tools.py` | Wired from `OSRM_BASE_URL` / `OTP_BASE_URL` env vars. When both are unset and no Apify token is available, the tools raise a clear user-facing error listing both transport options. |
| `PropertyDossier` + `DossierOptions` + `build_property_dossier(...)` | `packages/agent/src/uk_property_agent/dossier.py` | Top-level Pydantic model covering `location`, `avm`, `ppd`, `neighbourhood`, `crime`, `flood`, `epc`, `planning`. Every block nullable. Builder orchestrates postcodes.io → HMLR/AVM/PPD → Overpass + listed-buildings → police + flood + (optional) EPC + (optional) Idox planning, in parallel, with `asyncio.gather(return_exceptions=True)` so one outage never tanks the whole dossier — failures land in `errors: list[DossierError]`. |
| `CACHEABLE_SYSTEM_PROMPT` + `build_system_message(...)` | `packages/agent/src/uk_property_agent/prompts.py` | System prompt emitted as an Anthropic content-block with `{"type":"text","cache_control":{"type":"ephemeral","ttl":"5m"}}`. `PropertyAgent(enable_prompt_cache=True, cache_ttl="5m"\|"1h")` by default; override the full message via `system_message=...` for hand-tuned caching. |
| Apify actor registry expansion | `packages/apify_client/src/uk_property_apify_client/actors.py` | `ActorKey` + `KNOWN_ACTOR_SLUGS` now include `uk-demographics`, `uk-climate-risk`, `uk-location-intel` so delegation can target the full actor fleet. |

### Tests

27 new tests across three files:

* `packages/geo/tests/test_osrm.py` — +6 tests for `radial_grid(...)` + `OSRMClient.isochrone(...)` (URL synthesis, cutoff bucketing, unreachable-point handling, validation).
* `packages/agent/tests/test_agent_prompt_cache.py` — 10 tests covering the cache-control content-block shape, TTL override, `enable_prompt_cache=False` escape hatch, and `PropertyAgent` wiring.
* `packages/agent/tests/test_agent_isochrone.py` — 8 tests exercising the local path (OSRM + OTP fakes + postcodes.io resolver), the delegation path (stubbed `ApifyDelegation.resolve`), and the no-transport error paths.
* `packages/agent/tests/test_agent_dossier.py` — 9 tests with stubs for every seam in `ToolContext`, covering the happy path, postcode failure, LR failure, police failure with others surviving, EPC factory-not-wired, planning-skipped-for-unknown-district, `include_planning=False`, empty-PPD, and severity-min-is-worst semantics.

Real bug fix along the way: `build_property_dossier` wasn't unpacking the
dual-value tuple from `_run_avm(...)` (which packages AVM + PPD together so
we only hit HMLR once) — `ppd` was silently dropped. Now the AVM coroutine
result is destructured and fanned into both `avm=` and `ppd=` before the
`PropertyDossier.model_validate(...)` call.

---

## 2026-04-19 — Agent v3 (multi-provider routing + per-task model pinning)

v3 generalises the agent's LLM plumbing so the same `PropertyAgent` can
front Anthropic, OpenAI, or Gemini — with graceful fallback when
credentials are missing and per-task model pinning for callers who want
Sonnet-for-narrative / Flash-for-scoring / GPT-for-reasoning on the same
graph.

### What landed

| Piece | Where | Notes |
|---|---|---|
| `Provider` / `TaskKind` / `ProviderSpec` | `packages/agent/src/uk_property_agent/providers.py` | `StrEnum`s + frozen dataclass. `Provider.{ANTHROPIC, OPENAI, GEMINI}`; `TaskKind.{DEFAULT, DOSSIER, ANALYSIS, TOOL_PLAN}`. `ProviderSpec.slug` returns the `provider/model` shorthand used in env overrides. |
| `resolve_provider(task, *, explicit, env)` | same | Five-level precedence: explicit arg → `AGENT_MODEL_<TASK>` env → `AGENT_MODEL_DEFAULT` env → `AGENT_PROVIDER` env → `AGENT_PROVIDER_CHAIN` first-available. Accepts a `ProviderSpec`, a `Provider` enum, or any `"provider"` / `"provider/model"` string. Raises a single actionable `RuntimeError` listing the three env vars to set when nothing in the chain has credentials. |
| `build_chat_model(spec)` | same | Lazy-imports `langchain-anthropic` / `langchain-openai` / `langchain-google-genai` per spec. A missing package raises a targeted `ImportError` pointing at the right install extra (`uk-property-agent[openai]` etc.) instead of a deep `ModuleNotFoundError`. |
| `cache_shape_for(provider)` + `build_system_message(provider=...)` | `prompts.py` | Anthropic → content-block list with `cache_control: {type: ephemeral, ttl}`. OpenAI + Gemini → plain string (both providers auto-cache eligible prefixes). Legacy callers that don't pass `provider` keep the Anthropic shape for backwards compat. |
| `has_credentials` / `select_available_provider` / `describe_env` | `providers.py` | Credential probing for diagnostics + fallback. Gemini accepts either `GOOGLE_API_KEY` **or** `GEMINI_API_KEY`. `describe_env()` returns a JSON-safe dict used by `property-agent env --json`. |
| `PropertyAgent(provider=..., task=...)` | `agent.py` | Replaces the hard-coded Anthropic instantiation. Resolved `ProviderSpec` is available on `agent.provider_spec`. `model=` and `provider=` are mutually exclusive to prevent double-resolution. |
| CLI surface | `cli.py` | `ask --provider openai/gpt-4o` / `--model claude-3-5-haiku-20241022` / `--task dossier` / `--show-provider`, `env --json`. `ask` prints `[provider] anthropic/claude-sonnet-4-5-20250929 (temperature=0.2)` to stderr when `--show-provider` is set so operators can audit what's running without re-reading env. |
| Optional-deps groups | `pyproject.toml` | `[anthropic]` / `[openai]` / `[gemini]` / `[all]` so the default install stays slim. |

### Tests

38 new / updated tests split across two files:

* `packages/agent/tests/test_agent_providers.py` — **29 tests** covering credential detection (including the Gemini dual-key behaviour), all five levels of the resolution precedence, bare-slug + `provider/model` parsing, custom `AGENT_PROVIDER_CHAIN` ordering, the `RuntimeError` for fully-empty env, cache-shape routing per provider, `build_system_message` payload shape, `describe_env` output, lazy `ImportError` with the right install-extra name, and `PropertyAgent` end-to-end with a fake resolver/builder pair so the integration is exercised without real LLM SDKs.
* `packages/agent/tests/test_agent_cli.py` — **7 tests** updated for the new CLI surface: provider detection in `env`, JSON emission in `env --json`, `--show-provider` stderr line, `--task` forwarding, and the `SystemExit` path when no provider credentials exist.

Two real gaps fixed during test wiring:

1. `_default_chain()` previously read `os.environ` directly, so
   tests injecting `AGENT_PROVIDER_CHAIN` via a dict were ignored.
   Now accepts an optional `env` parameter and `select_available_provider`
   / `describe_env` thread it through.
2. `resolve_provider(explicit="gemini")` fell through to the fallback
   chain because `_parse_provider_model_slug` treated any no-slash
   value as a bare model name. `resolve_provider` now tries the
   Provider enum first for no-slash strings, then falls back to the
   old model-name path.

Additional chores during this pass:

* `Provider` + `TaskKind` migrated from `str, Enum` to `enum.StrEnum`
  (ruff `UP042`); minimum Python is already 3.12 so `StrEnum` is
  available.
* Added `--import-mode=importlib` to `uk-property-apify/pyproject.toml`'s
  pytest `addopts` so the three actor packages with `tests/__init__.py`
  (`auctions`, `climate-risk`, `location-intel`) stop colliding at
  collection time. Every per-actor test run already worked in isolation;
  this fixes the monorepo-wide `uv run pytest` command.

---

## 2026-04-19 — Agent v3 streaming narrative

The final v3 surface: token-level narrative output so the CLI (and any
downstream UI — the MCPs, a future SaaS UI) can render prose as it's
produced instead of showing a spinner while the full response buffers.

### What landed

| Piece | Where | Notes |
|---|---|---|
| `PropertyAgent.astream_narrative(question)` | `packages/agent/src/uk_property_agent/agent.py` | `async for chunk in agent.astream_narrative(...)` yields narrative text fragments as the final LLM call produces them. Uses LangGraph's `stream_mode="messages"`, filters chunks through `_is_final_answer_chunk(...)` so the caller only sees prose — no `AIMessageChunk`s that carry `tool_calls` / `tool_call_chunks`, and no `ToolMessageChunk`s from the tool node. Graceful fallback: if the underlying chat model doesn't stream (some fakes, some OpenAI paths), the method still returns a single chunk carrying the full final message so downstream code doesn't have to special-case non-streaming providers. |
| `PropertyAgent.astream_events(question)` | same | Structured event stream for UIs that want to interleave tool reasoning with streamed prose. Events are tagged dicts: `{"type": "tool_call", "name", "args"}` → `{"type": "tool_result", "tool", "content"}` → one or more `{"type": "narrative_chunk", "text"}` → one final `{"type": "final", "text"}`. Implemented as an orchestrator over `astream(stream_mode="updates")` for graph node events and `astream(stream_mode="messages")` for token-level narrative, unified into a single async iterator. |
| `PropertyAgent.astream(question)` | same | Stayed in place as the graph-level-updates stream (old behaviour, `stream_mode="updates"`). Renamed-in-docstring to clarify that it returns node update dicts, not tokens. |
| `property-agent ask --stream` | `packages/agent/src/uk_property_agent/cli.py` | Narrative tokens → `sys.stdout`, tool-call + tool-result events → `sys.stderr` so scripts can pipe stdout cleanly (`property-agent ask --stream "..." > answer.md 2> trace.log`). Without `--stream`, the CLI still uses the plain buffered `ainvoke(...)` path. |
| `_extract_text_content(message)` + `_is_final_answer_chunk(chunk, metadata)` | `agent.py` | Two small static helpers. `_extract_text_content` normalises LangChain's message-content shape (string vs list-of-content-blocks for Anthropic content arrays) into plain prose. `_is_final_answer_chunk` is the filter that drops tool-calling chunks — checks both `tool_calls` and `tool_call_chunks`, and only admits chunks whose class name starts with `AIMessage*`. |

### Tests

4 new / updated tests across two files:

* `packages/agent/tests/test_agent_graph.py` — 3 new tests.
  `test_astream_narrative_yields_final_answer_text` verifies the method
  returns the full final-answer text even with a non-token-streaming
  fake model (exercises the buffered-fallback path).
  `test_astream_narrative_skips_tool_call_turn` proves the tool-call
  `AIMessage` in a two-turn script is filtered out and only the
  narrative message content is yielded.
  `test_astream_events_emits_tool_call_then_narrative_then_final`
  walks the full `tool_call → tool_result → narrative_chunk → final`
  event sequence.
* `packages/agent/tests/test_agent_cli.py` — existing
  `test_ask_streams_and_prints_final` renamed and reshaped to
  `test_ask_streams_narrative_to_stdout_and_tools_to_stderr`,
  asserting stdout captures only narrative chunks and stderr
  captures the tool-call + tool-result lines when `--stream` is set.
  `_FakeAgent.astream_events` was added to the CLI fake so both the
  old and new behaviours are testable side-by-side.

### Known follow-ups (not blocking v3)

* When the underlying LLM emits a single `AIMessage` per turn (e.g.
  `FakeMessagesListChatModel` in tests, or older OpenAI routes),
  `stream_mode="messages"` yields one chunk at a time instead of
  token-by-token. The narrative API still works — the caller just
  receives one big chunk followed by `final` — but real token
  streaming only kicks in for provider-native streaming endpoints.
  Documented behaviour, not a defect.
* ~~LangGraph checkpoints are not yet wired~~ **resolved in v4 below**
  — an `InMemorySaver` checkpointer now threads through
  `PropertyAgent` + `thread_id`, enabling multi-turn state in the
  REPL and the web UI without re-sending history. SQLite / Postgres
  savers remain deferred until a SaaS-vs-CLI call is made.

---

## 2026-04-19 — Agent v4 (interactive surfaces: memory + REPL + Chainlit demo)

The final v4 surface brings the agent to humans. Three interactive
surfaces ship together — CLI `chat` REPL, Chainlit web demo, and
shared memory plumbing that makes both possible. The **MCP wrapper
that was on the v3 roadmap was cut** on the same pass: the target
audience for this demo is not Claude Code / Cursor users; we
deliberately kept the surface focused on a browser UI anyone can
open.

### What landed

| Piece | Where | Notes |
|---|---|---|
| `PropertyAgent(checkpointer=..., thread_id=...)` | `packages/agent/src/uk_property_agent/agent.py` | Opt-in LangGraph `Checkpointer` injection. Default is `langgraph.checkpoint.memory.InMemorySaver`. Every async entry point (`ainvoke`, `astream`, `astream_narrative`, `astream_events`) accepts `thread_id=` so callers can multiplex sessions against the same `PropertyAgent` instance. When a `thread_id` is set, the graph reads prior `messages` from the checkpointer and appends the new turn, so follow-ups see the full conversation without the caller re-sending anything. Tool-result events also carry the upstream `tool_call_id` now so the UI can correlate parallel tool calls. |
| `ChatLoop` + `ChatLoopOptions` + `build_chat_loop(...)` | `packages/agent/src/uk_property_agent/repl.py` | Framework-agnostic REPL core: takes injected `stdin`/`stdout` streams + a session factory, so tests drive it without a TTY. Slash commands (`/help`, `/clear`, `/provider SLUG`, `/tools`, `/stream on|off`, `/exit`) all mutate the in-memory session rather than rebuilding the agent — `/clear` just rotates to a new `thread_id`, `/provider` swaps the chat model while preserving the checkpointer + thread id. Streams tool calls + narrative chunks to stdout with a minimal ANSI style so it's legible under `less` / pipes too. |
| `property-agent chat` CLI subcommand | `packages/agent/src/uk_property_agent/cli.py` | Thin wrapper around `build_chat_loop(...)`. Flags: `--provider`, `--model`, `--task`, `--temperature`, `--no-stream`. Provider resolution uses the same `_resolve_spec` helper as `ask`, so env-var precedence is consistent across both commands. |
| `chainlit_app.py` (thin) + `chainlit_render.py` (fat) | `packages/agent/src/uk_property_agent/` | Two-module split forced by Chainlit's `spec_from_file_location` loader: the entry-point file has no `@dataclass` so Chainlit can load it under any module path; all dataclasses (`ChainlitRenderer`, `ChainlitSession`), the renderer logic, `build_session`, `swap_provider`, and `session_banner` live in the neighbouring `chainlit_render.py` which imports normally. `@cl.set_starters` surfaces four demo prompts on the landing screen (Cambridge family homes, SW2 3RX dossier, N1 commute isochrone, Elizabeth St sold prices). |
| `ChainlitRenderer` event pump | `chainlit_render.py` | Consumes `astream_events` and drives `cl.Step` + `cl.Message` components via injected factories. Correlates tool-call / tool-result events by `tool_call_id` so parallel tool calls render as independent collapsible panels; handles orphaned results (result without a matching call) by creating a synthetic step with an unknown-source banner; truncates tool inputs / outputs with `_short_input` / `_short_output` so very long JSON bodies don't blow the UI. |
| CLI `property-agent serve` | `packages/agent/src/uk_property_agent/cli.py` | Shells out to `chainlit run <module_path()>` after seeding `~/.uk-property-agent/web/`. Flags: `--host`, `--port`, `--headless`, plus provider/model/task/temperature (passed through as `PROPERTY_AGENT_WEB_*` env vars so the Chainlit file has no argparse coupling). The runtime-dir seeder copies bundled `chainlit.md` + `.chainlit/config.toml` on first run and leaves user edits alone on subsequent runs. |
| Bundled Chainlit config + welcome | `packages/agent/src/uk_property_agent/_web_defaults/` | `chainlit_config.toml` sets UI name `UK Property Intelligence`, dark theme, wide layout, disables MCP / audio / upload widgets. `chainlit.md` is the landing copy: lists the four tool categories, flags demo-only / no-auth status, and explains per-tab memory. Both force-included in the wheel via `[tool.hatch.build.targets.wheel.force-include]`. |
| `[web]` optional extra | `packages/agent/pyproject.toml` | `uk-property-agent[web]` pulls `chainlit>=2.0`. Keywords updated (`chainlit` in, `mcp` / `gradio` out). Version bumped to `0.4.0`. |
| LangSmith env check | `cli.py` `_run_env` | `property-agent env` (and `env --json`) now reports `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_TRACING` alongside the provider + data-API rows. No code change was needed for tracing itself — LangChain picks up the env vars automatically; the `env` surface just makes it obvious whether they're set. |

### Tests

**42 new tests** across three files:

* `packages/agent/tests/test_agent_repl.py` — **14 tests**: `/help`,
  `/clear` resets thread id but keeps provider, `/provider SLUG`
  swaps the chat model without touching memory, `/tools` lists the
  current tool set, `/stream on|off` toggles inline rendering,
  `/exit` + EOF return exit code 0, streaming output routes tool
  events and narrative correctly, non-streaming path still uses the
  checkpointer, unrecognised slash commands print a clear error.
* `packages/agent/tests/test_agent_chainlit_app.py` — **22 tests**:
  `ChainlitRenderer` against `FakeMessage` / `FakeStep` fakes,
  including narrative streaming (multiple chunks into one
  `cl.Message`), tool-call rendering as a `cl.Step`, call → result
  correlation by `tool_call_id`, parallel tool calls keep their own
  step, orphaned tool result creates a synthetic step, `_short_input`
  truncates long JSON without breaking UTF-8, `_short_output` caps at
  280 chars with ellipsis, `build_session` threads
  `provider` / `temperature` / `task` through to
  `PropertyAgent`, `swap_provider` preserves the checkpointer +
  `thread_id` across provider swaps, `session_banner` mentions the
  resolved provider / model / tool count.
* `packages/agent/tests/test_agent_graph.py` — **6 new tests** on
  top of the v3 streaming suite: `ainvoke(thread_id=...)` remembers
  prior turns, `astream_narrative(thread_id=...)` reads checkpointed
  history, `astream_events(thread_id=...)` emits tool results with
  the upstream `tool_call_id`, two `PropertyAgent` instances sharing
  a checkpointer + thread see each other's writes, fresh
  `thread_id=` on the same instance starts a clean conversation,
  default thread id is stable within an instance.

### Gaps fixed during this pass

1. **`astream_events` tool-result events lost their `tool_call_id`.**
   Chainlit needs the id to correlate call + result into one
   collapsible step. Added `"id": tc.get("id")` to the `tool_call`
   branch and `"id": getattr(message_chunk, "tool_call_id", None)`
   to the `tool_result` branch of `PropertyAgent._stream_events`.
2. **Chainlit module loader broke `@dataclass`.** `chainlit_app.py`
   used to hold `ChainlitRenderer` as a dataclass, which crashed at
   import time because Chainlit loads the file with
   `importlib.util.spec_from_file_location` and never registers it
   in `sys.modules` — the dataclass decorator's forward-ref
   resolution then calls `sys.modules.get(cls.__module__)` and gets
   `None`. Resolved by splitting rendering logic into a normally-
   importable `chainlit_render.py`.
3. **`property-agent serve` littered the cwd with Chainlit files.**
   Chainlit auto-creates `chainlit.md` + `.chainlit/config.toml` in
   its working directory on first run. We now pre-seed those files
   in `~/.uk-property-agent/web/` from bundled defaults and `cd`
   the child process into that directory before execing
   `chainlit run`. Idempotent — the seeder doesn't stomp on user
   edits.
4. **Nested `asyncio.run()` in tests.** Sync-calling
   `cli.main()` from an `async def` test crashed with
   "cannot be called from a running event loop" because
   `cli.main` wraps coroutines via `asyncio.run` internally.
   Converted the affected REPL tests to synchronous `def`.

---

## 2026-04-19 — Agent v4.1 (tool error containment + self-healing UI)

Hotfix on the v4 surfaces. Field-testing the Chainlit demo against
Gemini 2.5 Pro surfaced a graph-level crash any time an agent tool
raised an exception upstream — e.g. `postcodes.io` returning `404` on
a hallucinated postcode or Zoopla Cloudflare-blocking with `403`.
LangGraph's default `ToolNode` handler only swallows
`ToolInvocationError`; arbitrary `Exception` re-raised through the
graph, killing the turn mid-stream and — crucially — leaving an
`AIMessage(tool_calls=...)` in the checkpointer **without a matching
`ToolMessage`**. Gemini then hard-rejected every subsequent turn on
that thread with `INVALID_CHAT_HISTORY: Found AIMessages with
tool_calls that do not have a corresponding ToolMessage`, so the user
couldn't even type "hello" to recover.

### What landed

| Piece | Where | Notes |
|---|---|---|
| `_format_tool_error(error: Exception) -> str` | `packages/agent/src/uk_property_agent/tools.py` | Stable LLM-readable serialiser: `"[tool-error] <Type>: <msg>"`. Exception type annotated as `Exception` (not `BaseException`) so LangGraph's `_infer_handled_types` accepts it — the helper rejects anything outside `Exception.__mro__`. |
| Explicit `ToolNode(tools, handle_tool_errors=_format_tool_error)` | `packages/agent/src/uk_property_agent/agent.py` | `PropertyAgent` now builds the tool node itself and passes it to `create_react_agent` as `tools=<ToolNode>`, so every tool exception becomes a `ToolMessage` the LLM can read and react to (retry with different args, apologise, switch tool). The graph no longer persists orphan tool_calls. |
| `StructuredTool.handle_tool_error` also wired | `packages/agent/src/uk_property_agent/tools.py` | Belt-and-suspenders: inside `build_tools()` every returned tool has `handle_tool_error = _format_tool_error`. Handles explicit `ToolException` raises within the tool's own tracing context for cleaner LangSmith traces — the ToolNode handler is the primary defence for everything else. |
| `is_invalid_history_error(exc)` + `reset_thread(session)` | `packages/agent/src/uk_property_agent/chainlit_render.py` | Heuristic matcher + in-place session rotator. `reset_thread` keeps the same `PropertyAgent`, `ProviderSpec`, tool context, and `InMemorySaver` — only the `thread_id` rotates. Old thread state stays reachable for debugging, next turn starts fresh. |
| `@cl.on_message` self-heal | `packages/agent/src/uk_property_agent/chainlit_app.py` | When the provider rejects replayed history, the handler now rotates the thread, shows a friendly "started a fresh thread" message, and retries the user's question on the healed session — so a user hitting the broken state doesn't have to hunt for the "New chat" button. |
| `chainlit.md` tool-count accuracy | `packages/agent/src/uk_property_agent/_web_defaults/chainlit.md` | Was "20+ tools (23 with CH key)"; now correctly lists `17 by default … up to 24 with EPC + Companies House keys`. Seeded on first boot of `property-agent serve`. |

### Tests

**8 new tests** — every new code path locked in:

* `packages/agent/tests/test_agent_graph.py::TestToolErrorHandling` — **3 tests**:
  (a) a raising tool produces a `ToolMessage` whose `tool_call_id`
      matches the originating AIMessage — verified by invoking the
      *real* LangGraph `ToolNode` end-to-end, not a mock;
  (b) after the error the checkpointed history has zero orphan
      tool_calls, so the next Gemini turn would succeed;
  (c) every tool returned from `build_tools()` carries
      `_format_tool_error` as its `handle_tool_error`.
* `packages/agent/tests/test_agent_chainlit_app.py::TestSessionHelpers` — **5 new tests**:
  `reset_thread` rotates the id but preserves agent / checkpointer /
  spec / tool context, plus a parametrised matcher for
  `is_invalid_history_error` covering all three known provider
  signatures (LangGraph's generic, OpenAI `INVALID_CHAT_HISTORY`,
  Gemini `function response parts`) and a negative case for
  unrelated exceptions (rate-limit, timeout).

### Test count delta

`packages/agent`: **172 → 180** tests. Monorepo total: **1130 → 1138**.

### Recovery note for users mid-regression

Users with an open Chainlit tab that hit the bug don't need to
restart the server — after pulling this fix, the old session's
broken thread is self-healed on the next message. The friendly
"started a fresh thread" banner fires once and the user's question
is retried on a valid history.

---

## 2026-04-19 — Agent v4 (interactive surfaces: memory + REPL + Chainlit demo)

### Roadmap cut — MCP deferred

The v3 plan listed an `uk-property-agent-mcp` sibling repo so the
agent could be invoked as a Claude Desktop / Cursor MCP tool. The
user's target demo audience isn't in those clients yet, so the
whole scaffolding (and the mid-stream plan entry) was deleted.
Easy to bring back later — the agent's public API
(`PropertyAgent.ainvoke` / `astream_events` + `build_tools`) is
MCP-ready as-is; an MCP wrapper would be a ~200-line adapter
rather than a redesign.

### Known follow-ups (not blocking v4)

* `InMemorySaver` is per-process; reloading the REPL or restarting
  Chainlit drops history. Switching to `SqliteSaver` (stdlib-only)
  or `PostgresSaver` (network) is a one-line constructor change on
  `PropertyAgent(checkpointer=...)`; postponed until a SaaS-vs-CLI
  persistence decision is made.
* No OAuth / auth on the web UI. Deliberately demo-only today;
  Chainlit supports header auth + OAuth, but enabling it would
  push this past "local demo" into "hosted product" which is a
  separate decision.
* LangSmith wiring is env-var-only. A `property-agent trace`
  command that opens the latest run in a browser would be a nice
  ergonomic win; deferred.

---

## 2026-04-19 — A6 `auctions` multi-source dispatch (intel side)

Intel-repo half of the A6 expansion: three new HTML parsers + three new
source-register clients under `uk-property-scrapers` / `uk-property-apis`,
all conforming to the `AuctionSourceRegister` protocol Allsop already
implemented. The `uk-auctions` actor now fans out across every
`source` in `sources=[]` with per-source error isolation.

### What landed

| Piece | Where | Notes |
|---|---|---|
| `AuctionSummary` promoted to `_core.py` + `source: AuctionHouse` + `extra_context: dict[str, Any]` | `packages/apis/src/uk_property_apis/auctions/_core.py` | Single source-agnostic discovery record used across all four sources. Back-compat: Allsop-produced summaries default `source=AuctionHouse.ALLSOP` so legacy call sites keep working. `extra_context` is the per-source escape hatch for fields that don't live on the shared shape (e.g. iamsold's continuous-auction reserve window). |
| `AuctionFetchResult` | same | Standardised return shape from `AuctionSourceRegister.fetch_auction(...)` — carries the `AuctionSummary` back-reference, the parsed catalogue, and the normalised `list[AuctionLot]`. |
| `AuctionSourceRegister` Protocol | same | Two-method contract: `list_upcoming_auctions(...)` (discovery) + `fetch_auction(summary, *, page_size, ...)` (per-auction catalogue + lots). Runtime-checkable so A6's `ClientFactories` can validate wiring without forcing a concrete base class on each register implementation. |
| `AllsopRegister` wrapper around the existing `AllsopClient` | `packages/apis/src/uk_property_apis/auctions/allsop_client.py` | Zero behaviour change for Allsop itself; just re-exposes the existing Allsop pipeline through the new protocol so the actor's dispatch loop doesn't need a `if source == ALLSOP` branch. |
| `AuctionHouseClient` + `AuctionHouseRegister` | `packages/apis/src/uk_property_apis/auctions/auction_house.py` + `packages/scrapers/src/uk_property_scrapers/auctions/auction_house.py` | Covers [auctionhouse.co.uk](https://www.auctionhouse.co.uk). Discovery page: `GET /national-property-auctions`. Per-auction pages: `GET /{regional-branch}/auction/{slug}`. Parser reads the lot-card grid (address, guide price low/high, auction-house branch, lot number when present, status, sale-method badge). Lot URLs are absolute; no JS hydration required. Live-smoked against 383 lots on the 2026-04 national catalogue (Cameford Court flat at guide £190-£210k). |
| `SavillsAuctionsClient` + `SavillsAuctionsRegister` | `packages/apis/src/uk_property_apis/auctions/savills.py` + `packages/scrapers/src/uk_property_scrapers/auctions/savills.py` | Covers [savills.com/auctions](https://www.savills.com/auctions). Discovery page: `GET /auctions/upcoming-auctions.html`. Per-auction: `GET /auctions/{date-slug}/list.html?start=0&pagesize=100` — 100 lots/page pagination. Parser also handles Savills' date-range header shape ("12\u201313 March 2026") via a unicode en-dash in the title regex. Live-smoked against 49 lots (Grafton Road flat at guide £575k). |
| `IamsoldClient` + `IamsoldRegister` | `packages/apis/src/uk_property_apis/auctions/iamsold.py` + `packages/scrapers/src/uk_property_scrapers/auctions/iamsold.py` | Covers [iamsold.com](https://www.iamsold.com). iamsold doesn't run calendar auctions — it runs a rolling "modern method of auction" with per-lot reserve windows. The register synthesises **one** catalogue auction per run, with an end-date anchored to "now + max live window", and the register fans lots out from `GET /properties/live`. Lot cards use "starting bid" rather than "guide price"; parser maps that to `AuctionGuidePrice(qualifier=STARTING_BID)`. Live-smoked: 5 lots (Percival Terrace starting £90k). |
| `_get_text(url, ...)` on `BaseAPIClient` | `packages/apis/src/uk_property_apis/_core/base_client.py` | Thin wrapper around `_request_raw` that returns the decoded HTML body as `str` (vs `_get_json` which returns parsed JSON). All three new clients use it; kept on the shared base so future HTML-scrape sources don't re-roll. |

### Tests

Parser tests (fixtures based, zero network) + smoke tests (live HTML hit
during recon) shipped alongside the clients. Existing Allsop tests were
not disturbed.

* `packages/scrapers/tests/fixtures/auctions/` gained `auction_house/`
  (index + one auction page), `savills/` (index + two paginated list
  pages to exercise the offset/pagesize pagination), and `iamsold/`
  (live properties page) subfolders.
* `packages/scrapers/tests/test_auctions_*_parser.py` — one file per
  new source, covering address + guide price + status + sale method
  parsing, pagination handling (Savills), and the synthetic-auction
  continuation for iamsold.
* `packages/apis/tests/test_auctions_*_client.py` — one file per new
  source, driving `list_upcoming_auctions` + `fetch_auction` through
  respx-mocked HTML fixtures. Protocol conformance is enforced by an
  `isinstance(register, AuctionSourceRegister)` assertion in each
  file so the runtime-checkable protocol isn't silently broken by a
  type-annotation-only drift.

Apify-side A6 wiring (actor dispatch, input schema, `source` /
`sources` fields, live smoke) is documented in the sibling
`uk-property-apify/STATUS.md` entry for the same date.

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
