# UK Property Intelligence — Master Plan

> Status: **build phase** — see the top-level `STATUS.md` for the live snapshot. As of 2026-04-19: 8 public packages + 1 private shipped, 3 MCPs dual-mode, **12/12 actors code-complete** (A6 `auctions` multi-source dispatch shipped 2026-04-19 covering Allsop + Auction House UK + Savills + iamsold), LangGraph Agent v4.1 with multi-provider routing + streaming narrative + 17–24 tools (conditional on API keys) + **three interactive surfaces** (CLI `ask` / CLI `chat` REPL / Chainlit web demo, all sharing an `InMemorySaver` checkpointer, Chainlit thread self-heals when a provider rejects corrupted replay history), **1138 intel tests green**.
> Owner: Kubilay Yavuz
> Single source of truth — keep this file current as decisions evolve.

---

## 0. TL;DR

Build a **UK Property Intelligence stack** as an open-source funnel into a private, paid Apify layer:

- **Public side** — core monorepo (`uk-property-intel`) containing pure-Python parsers, gov-API clients, geospatial engine, AVM model, and a LangGraph agent; two single-purpose MCP repos (`zoopla-mcp`, `rightmove-mcp`) that wrap the monorepo's parsers for Claude Desktop / Cursor / Smithery distribution.
- **Private side** — `uk-property-apify` repo housing **12 monetised Apify actors** covering listings, sold prices, EPC, planning, auctions, landlord graphs, tenders, demographics, AVM valuations, climate risk, and location intelligence.
- **The agent binds it all together**, answering natural-language queries like *"£1m budget, pick 3 houses likely to grow in value over 20 years with good transport and schools, no flood risk"* with a full dossier per recommendation.

**Revenue thesis**: low four-figure MRR from Apify alone serves as validation → direct SaaS licensing + enterprise deals as the real upside. Primary payoff is a category-defining portfolio artifact + optional business when signals land.

---

## 1. Product Vision

### Use cases the stack must handle

| Use case | Primary inputs | Primary data needs |
|---|---|---|
| *"3-bed house to rent in Norwich within 10-min walk of a train station, under £1,800"* | Location + filters | Listings + transport routing + flood + schools |
| *"£1m, give me 3 houses likely to grow in value over 20 years"* | Budget + horizon | Listings + AVM + infrastructure pipeline + regen zones + demographics forecasts + climate risk |
| *"Is this Zoopla listing a good HMO opportunity?"* | Specific listing URL | EPC + council tax + licensing register + yield comparables + landlord network |
| *"Where in the Midlands has highest planning application approval rate and lowest flood risk?"* | Region + constraints | Planning aggregator + flood + housing delivery tracker |
| *"Who owns this postcode? Map the landlord's portfolio"* | Postcode / address | Companies House graph + Land Registry corporate ownership |
| *"Watch every planning application in CB2 and alert me when something ≥5 units appears"* | Polygon + threshold | Planning aggregator + webhook + LLM classification |
| *"Auction lots under £200k near upcoming HS2 stations"* | Budget + infra filter | Auctions + infrastructure pipeline + proximity |

### Who it's for

| Audience | Entry point | Converts to |
|---|---|---|
| Developers / AI tinkerers | MCPs on Smithery + PyPI (free) | Apify paid mode when rate-limited |
| Python devs, data journalists, individual investors | `uk-property-agent` CLI (free, BYO-keys) | Apify API token for paid enrichment actors |
| PropTech startups, BTL investors, family offices, mortgage brokers | Apify store (pay-per-result) | Direct enterprise licensing (£500–£5k/mo) |
| Insurance, lenders | Enterprise tier from day one | Long-term data contracts |
| Recruiters | GitHub repos + blog post + demo | Job offer (£20–50k comp uplift equivalent) |

### What makes it defensible

1. **Breadth nobody else has** — no single competitor combines listings + sold prices + EPC + planning + auctions + landlord graph + AVM + climate + demographics + location intelligence under one schema.
2. **Government-data moat** — EPC+CT+PPD unified, landlord graph, planning-with-documents, climate composite, UK demographics, and AVM are all either absent or poorly served on Apify today.
3. **LangGraph agent on top** — answers questions Zoopla/RM structurally cannot, because their filters don't support isochrones, multi-factor scoring, or cross-source synthesis.
4. **MCP-first distribution** — direct install path into Claude Desktop / Cursor / any MCP client for the funnel scrapers. Wider surface than any competitor.
5. **Write-once, deploy-three-ways architecture** — parsers and enrichers live in the OSS monorepo; MCPs, Apify actors, and a future SaaS all reuse them unchanged.

---

## 2. Architecture

### Four repositories, clean responsibilities

```
┌─────────────────────────────────────────────────────────────────┐
│  PUBLIC — OSS                                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  uk-property-intel/           ← this folder, core monorepo      │
│  ├── packages/                                                   │
│  │   ├── scrapers/            → uk-property-scrapers (PyPI)     │
│  │   ├── apis/                → uk-property-apis (PyPI)         │
│  │   ├── geo/                 → uk-property-geo (PyPI)          │
│  │   ├── avm/                 → uk-property-avm (PyPI)          │
│  │   └── agent/               → uk-property-agent (PyPI)        │
│  └── docs/                                                       │
│                                                                  │
│  zoopla-mcp/                  ← SEPARATE REPO                    │
│   depends on: uk-property-scrapers, uk-property-geo             │
│   publishes:  uvx zoopla-mcp on Smithery + awesome-mcp + PyPI   │
│                                                                  │
│  rightmove-mcp/               ← SEPARATE REPO                    │
│   depends on: uk-property-scrapers, uk-property-geo             │
│   publishes:  uvx rightmove-mcp                                  │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  PRIVATE                                                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  uk-property-apify/                                              │
│  ├── actors/                  ← 12 thin wrappers                │
│  │   ├── zoopla-listings/                                        │
│  │   ├── rightmove-listings/                                     │
│  │   ├── onthemarket-listings/                                   │
│  │   ├── epc-ct-ppd-unified/                                     │
│  │   ├── planning-aggregator/                                    │
│  │   ├── auctions/                                               │
│  │   ├── landlord-network/                                       │
│  │   ├── uk-tenders/                                             │
│  │   ├── uk-demographics/                                        │
│  │   ├── uk-avm/                                                 │
│  │   ├── climate-risk/                                           │
│  │   └── location-intel/                                         │
│  └── shared/                  ← proxies, caches, rate limits    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Monorepo package layout (`packages/`)

```
packages/
├── scrapers/       ← pure parsers (zoopla/, rightmove/, onthemarket/)
│                     + canonical Listing schema, no IO, testable via fixtures
├── apis/           ← httpx clients for every free gov API
│                     (epc, police, flood, ofsted, postcodes, land_registry,
│                      companies_house, ons_nomis, historic_england, ...)
├── geo/            ← routing (OSRM + OTP), isochrones, overlays
│                     (flood/GreenBelt/conservation), proximity, amenities (OSM)
├── avm/            ← PPD + EPC join pipeline, hedonic regression + XGBoost
│                     (Middle accuracy tier ±15–20%)
└── agent/          ← LangGraph orchestrator + CLI + Pydantic-typed tools
                      uses every package above, plus MCP clients and Apify REST
```

### Key architectural decisions

- **Parsers are pure functions** (`html: str → list[Listing]`), Browser-agnostic. Tested against saved HTML fixtures. Same parser runs in MCP local mode, Apify actor production mode, and agent orchestration mode.
- **Crawler layer is separate** — Crawlee + Playwright + Apify residential proxy + playwright-stealth lives in the Apify actors, not in the scrapers package.
- **60% of sources aren't scrapers** — gov APIs go through `packages/apis/` with plain `httpx`, not Playwright. Playwright is reserved for Zoopla, Rightmove, OnTheMarket, IDOX/Civica planning portals, auction sites, VOA council tax, and new-build developer sites.
- **Canonical schema is the contract** — every source normalizes to `uk_property_scrapers.Listing`. Downstream code never sees source-specific shapes.
- **Dual-mode MCPs** — each MCP runs `MODE=local` (user's IP, Playwright, free, may hit Cloudflare) or `MODE=apify` (delegates to hosted actor via user's API token). This is the funnel: casual users install free, upgrade when they hit limits.
- **LangGraph for the agent** — graph-based multi-step tool orchestration, Pydantic-typed state, visualisable runs for debugging. Not plain SDK; not DSPy.
- **`uv` workspaces** for monorepo dependency management, `hatchling` for per-package builds.
- **Parsers and clients in OSS, orchestration in private** — the interesting code is public (recruiters see substance), the reliability moat (proxy rotation, retry strategy, Cloudflare tuning, caching layers) stays private.

---

## 3. The 15 Deliverables

### OSS artefacts (3 + 3 agent surfaces)

| # | Name | Purpose | Distribution |
|---|---|---|---|
| O1 | `uk-property-intel` (monorepo → 5 PyPI packages) | Core parsers, API clients, geo engine, AVM model, agent | GitHub + PyPI |
| O2 | `zoopla-mcp` | MCP server for Zoopla, dual-mode | GitHub + PyPI + Smithery + awesome-mcp |
| O3 | `rightmove-mcp` | MCP server for Rightmove, dual-mode | GitHub + PyPI + Smithery + awesome-mcp |

The `uk-property-agent` package ships **three interactive surfaces** (all in
the same PyPI wheel, all over the same `PropertyAgent` + tool set +
checkpointer):

| Surface | Command | Purpose | Audience |
|---|---|---|---|
| One-shot CLI | `property-agent ask "..."` (`--stream` optional) | Scriptable, pipe-friendly, runs the graph once and exits | Shell users, CI, quick smoke tests |
| Interactive REPL | `property-agent chat` | Terminal loop with `InMemorySaver` memory + slash commands (`/provider`, `/tools`, `/stream`, `/clear`, `/help`, `/exit`) | Devs iterating on prompts locally |
| Web demo | `property-agent serve` (`[web]` extra, Chainlit) | Browser chat UI with per-tab thread memory, streamed answers, collapsible tool steps, four starter prompts, seeded branded config at `~/.uk-property-agent/web/`. Demo-only — no auth, no rate limiting, no persistence beyond the in-memory saver | Non-technical demo attendees, screen-share pitches |

A sibling `uk-property-agent-mcp` repo was briefly scaffolded then **cut**
from the roadmap on 2026-04-19 — the target audience for this agent isn't
on Claude Desktop / Cursor yet, and the Chainlit UI covers the demo use
case with much lower friction. The agent's public API
(`PropertyAgent.ainvoke` / `astream_events` + `build_tools`) is MCP-ready
and the wrapper can be brought back as a ~200-line adapter if the audience
shifts.

### Apify actors (12)

| # | Actor | Category | Moat | Indicative price | Status |
|---|---|---|---|---|---|
| A1 | `zoopla-listings` | Listings | Real market gap; existing actors have 36–70% success rates | $1.50 / 1k results | **Scaffold + run-loop hardened** (retry, proxy rotation, dedupe, concurrency, standardized errors). Awaiting Apify deploy. |
| A2 | `rightmove-listings` | Listings | Matches memo23 price floor; completeness play | $0.95 / 1k results | **Scaffold + run-loop hardened.** Awaiting Apify deploy. |
| A3 | `onthemarket-listings` | Listings | Third UK portal, completeness | $1.00 / 1k results | **Scaffold + run-loop hardened.** Awaiting Apify deploy. |
| A4 | `epc-ct-ppd-unified` | Foundational | **Zero competitors.** EPC + VOA council tax + HMLR Price Paid in one call | $0.015 / address | **Built, 26 tests green.** |
| A5 | `planning-aggregator` | Foundational | IDOX/Civica direct + document downloads (PlanIt aggregates, doesn't fetch docs) | $0.02 / application + $0.005 / doc | **Built, 53 tests green.** |
| A6 | `auctions` | Domain intel | Empty niche: Allsop + Auction House + Savills + iamsold unified | $0.015 / lot | **DONE 2026-04-19** — All four sources shipped behind a shared `AuctionSourceRegister` protocol. Live-smoked: Allsop 362 lots, Auction House UK 383 lots, Savills 49 lots, iamsold 5 lots. 50 actor tests + parser + client suites green. |
| A7 | `landlord-network` | Domain intel | CH flat scrapers exist; graph traversal + PSC cross-ref unique | $0.05 / root company | **Built, 52 tests green.** |
| A8 | `uk-tenders` | Domain intel | Narrowed scope: construction + PropTech + housing CPV codes only | $0.005 / opportunity | **Built, 35 tests green.** |
| A9 | `uk-demographics` | Signals | UK-focused, ONS + Census 2021 + IMD + Nomis + MHCLG projections unified | $0.003 / area | **Built, 52 tests green.** |
| A10 | `uk-avm` | Signals | Middle AVM (±15–20%) via PPD+EPC hedonic + XGBoost | $0.05 / valuation | **Built, 75 tests green, AVM v3 + neighbourhood + HPI shipped.** |
| A11 | `climate-risk` | Signals | Flood + coastal + subsidence + UKCP18 20-yr projections, composite score | $0.01 / postcode | **Built, 68 tests green, live-smoked.** Coastal / BGS upstream APIs moved - clients raise clear migration errors, A11 surfaces as `partial_errors`. Flood + UKCP18 production-ready today. |
| A12 | `location-intel` | Signals | Routing + isochrones + overlays + amenity density + proximity dossier | $0.02 / postcode | **Built, 101 tests green, live-smoked** against real postcodes.io + Overpass. OSRM / OTP are optional self-hosted infra and surface as `SkippedSource` when unconfigured. Agent `drive_time_isochrone` + `transit_isochrone` tools now auto-delegate here when `APIFY_API_TOKEN` is set (2026-04-19). |

### Optional bundle actor (maybe)

A13 — `uk-postcode-dossier` that composes A9+A10+A11+A12 in one call, priced at $0.10 (vs. $0.103 for the four separately — convenience premium). Ship only if there's demand.

---

## 4. Build Strategy

### Dependency graph (what must exist before what)

```
             ┌──────────────────────────────┐
             │ Canonical Listing schema     │  ← foundational
             │ (packages/scrapers/schema)   │
             └────────────┬─────────────────┘
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
   ┌──────────┐   ┌──────────┐    ┌──────────────┐
   │  Zoopla  │   │ Rightmove│    │ OnTheMarket  │
   │  parser  │   │  parser  │    │    parser    │
   └────┬─────┘   └────┬─────┘    └──────┬───────┘
        │              │                  │
        ▼              ▼                  ▼
   ┌──────────┐   ┌──────────┐    ┌──────────────┐
   │zoopla-mcp│   │rightmove-│    │  onthemarket │
   │          │   │   mcp    │    │     actor    │
   └────┬─────┘   └────┬─────┘    └──────┬───────┘
        ▼              ▼                  ▼
   (each parser also backs its paid Apify actor; same code)

        API clients (packages/apis) ─ foundational, fully parallelizable
          ├─ epc, police, flood, ofsted
          ├─ postcodes, land_registry, historic_england
          └─ ons_nomis, companies_house, env_agency

        Geo engine (packages/geo) ← depends on apis (postcodes, OSM)
          └─ routing, isochrones, overlays, proximity

        AVM (packages/avm) ← depends on apis (land_registry, epc)
          └─ PPD+EPC join → hedonic regression + XGBoost

        Agent (packages/agent) ← depends on everything above
          └─ LangGraph, Pydantic-typed tools, CLI, scoring
```

### Priority order (no timings)

1. **Scrapers package** — canonical schema + Zoopla parser + fixture tests (DONE)
2. **Rightmove + OnTheMarket parsers** — parallel subagents, same schema (DONE)
3. **API clients package** — each client is independent, fully parallelizable across subagents (DONE; coastal/BGS clients raise upstream-migration errors pending a rewrite against the new NCERM 2024 schema / replacement data source)
4. **Geo package** — routing + overlays + proximity, depends on postcodes + OSM clients (v1 DONE; v2 OSRM / OTP routing + `OSRMClient.isochrone` radial-grid + H3 grid DONE 2026-04-19; Shapely polygons still opt-in)
5. **AVM package** — PPD+EPC join pipeline first (the real asset), then Middle accuracy model (DONE incl. v3: hedonic/quantile/GBM + HPI + neighbourhood)
6. **Zoopla + Rightmove MCPs** — thin wrappers over the scrapers, dual-mode support
7. **First Apify listings actor** (Zoopla) — proves the production crawler pattern (DONE + hardened 2026-04-19)
8. **EPC+CT+PPD unified actor** — cheapest revenue, zero competition (DONE)
9. **Planning aggregator actor** — IDOX selectors already proven in prior session (DONE)
10. **Agent (LangGraph)** — first end-to-end demo (ALPHA **v4 shipped 2026-04-19**: 23 tools incl. `drive_time_isochrone`, `transit_isochrone`, `build_property_dossier`; dual-mode delegation to A10 AVM, A12 location-intel, A5 planning, A7 landlord network; **multi-provider routing across Anthropic / OpenAI / Gemini** with provider-aware prompt-cache shape + per-task model pinning via `AGENT_MODEL_<TASK>` env + `[anthropic]` / `[openai]` / `[gemini]` / `[all]` optional-deps; **streaming narrative output** via `PropertyAgent.astream_narrative(...)` + `astream_events(...)` + CLI `--stream` flag; **three interactive surfaces** — `property-agent ask` (one-shot), `property-agent chat` (REPL with memory + slash commands), `property-agent serve` (Chainlit web demo, `[web]` extra) — all sharing a LangGraph `InMemorySaver` checkpointer keyed by `thread_id`)
11. **AVM, Climate Risk, Location Intelligence, Demographics, Auctions actors** — the signals + domain-intel stack (A9 + A10 + A11 + A12 + A6 multi-source DONE 2026-04-19)
12. **Auctions, landlord network, tenders** — domain intel (all DONE: landlord + tenders shipped 2026-04-18, auctions multi-source shipped 2026-04-19)
13. **Launch push** — blog post, Show HN, LinkedIn, Smithery, Discord community

### Deploying subagents

Natural parallel boundaries where subagents accelerate work:

- **Scraper parsers** — 3 × `python-pro` subagents (Rightmove, OnTheMarket, shared helpers) once Zoopla pattern is validated
- **API clients** — ~8 × `python-pro` subagents (one per client, all independent)
- **Geo engine** — 3 parallel subagents (routing, overlays, proximity/amenities)
- **Apify actors** — batches of ~4 parallel, each wrapping a monorepo package
- **AVM** — 1 dedicated `python-pro` subagent (substantial work, not parallelizable mid-model)

---

## 5. Pricing & Unit Economics

### Price list

| Deliverable | Free tier | Paid tier | Target buyer |
|---|---|---|---|
| OSS monorepo + both MCPs + agent | Unlimited local use | — | Devs, tinkerers, recruiter audience |
| A1 Zoopla Apify | 100 results / mo | $1.50 / 1k | Professionals |
| A2 Rightmove Apify | 100 / mo | $0.95 / 1k | Professionals |
| A3 OnTheMarket Apify | 100 / mo | $1.00 / 1k | Professionals |
| A4 EPC+CT+PPD unified | 10 / mo | $0.015 / address | BTL investors, due diligence |
| A5 Planning aggregator | 10 / mo | $0.02 / app + $0.005 / doc | Planning consultants, architects, developers |
| A6 Auctions | Free preview | $0.015 / lot | Auction investors |
| A7 Landlord network | 1 graph free | $0.05 / root | Due diligence, journalists, tenants' unions |
| A8 UK tenders | Free preview | $0.005 / opportunity | Construction SMEs, PropTech |
| A9 UK demographics | 10 / mo | $0.003 / area | Journalists, councils, consultants, academics |
| A10 UK AVM | 5 / mo | $0.05 / valuation | Estate agents, investors, lenders |
| A11 Climate risk composite | 10 / mo | $0.01 / postcode | Insurance, lenders, investors |
| A12 Location intel | 10 / mo | $0.02 / postcode | PropTech, agent users, journalists |
| A13 (optional) Postcode dossier bundle | 3 / mo | $0.10 / postcode | Prosumer bundle |

### Indicative MRR mix (base case, projected when stack is mature)

| Source | £/mo | Notes |
|---|---|---|
| Listings trio (A1–A3) | ~£1,000 | Price-floor match, funnel-driven |
| EPC+CT+PPD (A4) | ~£1,000 | Zero competition, sticky professional use |
| Planning (A5) | ~£1,000 | Premium feature (documents) |
| Signals stack (A9–A12) | ~£800 | Combined demographics, AVM, climate, location |
| Auctions + landlord + tenders (A6–A8) | ~£400 | Niche but loyal |
| **Apify total** | **~£4,200** | Revenue validation layer |
| Direct licensing (when it comes) | £500–10k | 2-5 enterprise deals |
| Career uplift amortised | £1,500–4,200 | Salary bump equivalent |
| **All-in upside** | **£6k–18k/mo** | Including indirect |

### LLM cost posture

| Role | Model | Per-call cost |
|---|---|---|
| Parse natural-language query | Claude Haiku (~500 input tokens, ~200 output) | ~$0.0002 |
| Batch-score candidate listings | Gemini Flash (~400 tokens × 60 listings, cached system prompt) | ~$0.006 |
| Final narrative / thesis | Claude Sonnet (~1.5k input, ~1k output) | ~$0.004 |
| Enricher classification (trivial cases caught by rules first) | Gemini Flash | ~$0.0001 / call |

Prompt caching is mandatory on anything with >500 shared tokens. Structured output mode (JSON schema) used wherever the LLM returns to a fixed schema — zero parse errors, zero retry cost.

---

## 6. Data Sources (Free + Paid)

### Free UK government APIs (backbone of the agent's enricher layer)

| Source | Endpoint | Auth | Use |
|---|---|---|---|
| **EPC Register** | `epc.opendatacommunities.org/api` | Email signup | Every EPC since 2008 |
| **HM Land Registry Price Paid** | CSV downloads + monthly HPI API | None | Every UK sale since 1995 |
| **HM Land Registry INSPIRE** | Polygon downloads | None | Every freehold parcel |
| **HM Land Registry Overseas Ownership** | CSV | None | Foreign ownership register |
| **data.police.uk** | `data.police.uk/api` | None | Crime by lat/lng |
| **Environment Agency Flood Risk** | `environment.data.gov.uk/flood-monitoring` | None | Rivers + coastal + surface water |
| **UKCP18 Climate Projections** | CEDA | Academic account | 20+ yr climate forecasts |
| **BGS Geology of Britain** | Open data portal | None | Subsidence risk |
| **Defra UK-AIR** | API | None | Air quality |
| **Ofcom Connected Nations** | CSV + API | None | Broadband + mobile quality |
| **Coal Authority** | Interactive map | Scrape | Mining subsidence risk |
| **Ofsted GIAS** | `get-information-schools.service.gov.uk` | None | School ratings |
| **NHS Data** | Open API | None | GP surgeries, hospitals |
| **Food Standards Agency** | API | None | Restaurant hygiene (gentrification proxy) |
| **Charity Commission** | API | Free key | Local charities |
| **ONS Nomis** | `nomisweb.co.uk/api` | None | Employment, wages, census |
| **ONS Census 2021 Bulk API** | `census.gov.uk` | None | Demographic detail to LSOA |
| **MHCLG Household Projections** | CSV | None | 25-year demand forecasts |
| **MHCLG Housing Delivery Test** | Open data | None | Supply-side signal |
| **Homes England** | Open data portal | None | Grant allocations |
| **National Infrastructure Commission** | Pipeline published | None | Every major infra project |
| **Historic England NHLE** | API | None | Listed buildings |
| **Natural England MAGIC** | WFS | None | Conservation areas, AONB, SSSI, Green Belt |
| **Companies House** | API | Free key, 600 req / 5 min | Director + PSC data |
| **Bank of England** | Statistical Interactive Database | None | Rates, SONIA |
| **DLUHC planning.data.gov.uk** | API | None | Partial planning geospatial register |
| **postcodes.io** | API | None | Free postcode → lat/lng, LSOA, ward |
| **OS OpenData** | Download + API | None | OpenMap Local, OpenRoads |
| **OS Places API** | API | Free tier + paid | Full address lookup |
| **NaPTAN** | Download | None | Every UK transport stop |
| **TfL Unified API** | API | Free key | London multimodal |
| **Rail Data Marketplace / OpenLDBWS** | API | Free registration | National Rail timetables |
| **BODS (Bus Open Data Service)** | GTFS feeds | None | Every UK bus |
| **OSM Overpass** | Overpass API | None | Amenities, POIs, roads |

### Paid APIs (sparingly)

| Source | Use | Cost tier |
|---|---|---|
| Google Places / Maps | Fallback for POI density where OSM is thin | Pay-per-call |
| Mapbox Directions | Routing fallback, 100k/mo free | Free tier, then $0.50 / 1k |
| Google Directions | Premium tier only | $5 / 1k |

### Self-hosted infra

| Component | Cost / notes |
|---|---|
| **OSRM** (routing engine, Docker container with UK OSM extract) | ~£5–10 / mo VPS or ad-hoc on Apify |
| **OpenTripPlanner** (multimodal with GTFS) | ~£25–40 / mo (4–8GB RAM), used only for public transport routing |
| **DuckDB** (local spatial joins + PPD queries) | Free, in-process |
| **SQLite cache** (enricher TTLs) | Free, local |

### Sources that become our own scrapers (feed the Apify actors)

Zoopla, Rightmove, OnTheMarket, SpareRoom, OpenRent, Allsop, Auction House UK, Savills auctions, iamsold, Barratt / Bellway / Persimmon / Taylor Wimpey / Vistry / Redrow developer sites, IDOX / Civica / Ocella planning portals, Planning Inspectorate appeals, VOA council tax pages, HMO licence registers (350 councils), The Gazette probate notices.

---

## 7. Technical Stack Decisions (Locked)

### Core
- **Python 3.12** (cross-compat with Apify; 3.13 alt tolerated by libs)
- **`uv` workspaces** for monorepo management
- **`hatchling`** for per-package builds
- **Pydantic v2** throughout — every IO boundary is schema-validated

### Scraping layer
- **Crawlee + Playwright** for browser-rendered sources (Zoopla, Rightmove, OTM, IDOX, Civica, auctions, VOA, developer sites)
- **`httpx`** for JSON/REST/CSV sources (all the free gov APIs)
- **`selectolax`** for fast HTML parsing (CSS selectors, no browser)
- **`curl-cffi`** as fallback for TLS-fingerprinted sites if needed
- **playwright-stealth** for fingerprint evasion
- **Apify residential proxies** for Cloudflare bypass

### Agent
- **LangGraph** — orchestrator (confirmed)
- **Anthropic, Gemini, OpenAI SDKs** — multi-provider, not locked to one
- **Pydantic-typed tool schemas** — every tool input/output is a model
- **Prompt caching** — mandatory on system prompts >500 tokens
- **Structured output mode** — wherever the LLM returns to a schema

### Geo
- **OSRM** for driving / walking / cycling routing (self-hosted)
- **OpenTripPlanner** for multimodal public transport
- **Shapely + GeoPandas** for geometry operations
- **H3** (Uber hexagonal index) for density aggregation
- **DuckDB + spatial extension** for fast spatial joins without PostGIS

### AVM
- **pandas + DuckDB** for the PPD+EPC join pipeline
- **XGBoost + statsmodels** for hedonic regression + GBM
- **scikit-learn** for cross-validation + calibration
- **joblib** for model serialization

### Observability (alerts)
- **Discord webhooks** — per-actor severity channels (`#alerts-critical`, `#alerts-degraded`, `#alerts-drift`, `#runs-daily`, `#revenue`)
- **Apify webhook integration** — runs, payments, failures all route to Discord

### Tooling
- **ruff** for lint + format
- **mypy** (strict) for types
- **pytest + pytest-cov** for tests
- **Fixture-based tests** for every parser — real HTML saved from live runs
- **GitHub Actions** for CI on every repo

---

## 8. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Selector rot (Zoopla/RM/OTM change markup) | ~90% within 3 months | -20% revenue per event | Fixture-based tests fail loudly; Discord `#alerts-drift` on schema drift |
| Cloudflare escalation on listings sites | 15-25% in first year | Success drops to 40-60% | Apify residential proxies + stealth + session warming + curl-cffi fallback |
| Zoopla/Rightmove legal notice | 5-10% | Forced repo private | Research/educational framing; polite rate limits; no login bypass; public robots.txt respect on paths |
| Selector on IDOX/Civica breaks mid-month | ~30% quarterly | One council's scraping pauses | Per-portal monitoring; auto-fallback to PlanIt for affected councils |
| AVM accuracy below claimed ±15-20% | 25% | Loss of trust, refunds | Out-of-sample eval on held-out PPD slice; public accuracy dashboard |
| Burnout on 12-actor maintenance | 40% | Actor ratings drop | Hard maintenance budget: target ~1 day / month covering all actors; Discord alerting for triage focus |
| Competitor copies schema | 60% within 9 months | Listings gap closes | By then, moat sits in AVM + Location Intel + Climate + Landlord graph — not copyable in 9 months |
| LLM provider price shock | 20% | LLM costs triple | Multi-provider routing in agent + per-task model pinning; batch scoring on cheap models |
| Apify policy change (pricing, TOS) | 10% | Revenue channel shift | SaaS escape hatch planned (see §11) |

---

## 9. Decision Log

### Locked
- [x] **Stack shape**: 3 OSS artefacts (monorepo + 2 MCPs) + 12 Apify actors
- [x] **Agent framework**: **LangGraph** (not Anthropic SDK, not DSPy)
- [x] **Monorepo structure**: `packages/{scrapers,apis,geo,avm,agent}` under `uk-property-intel`
- [x] **MCPs**: separate repos (`zoopla-mcp`, `rightmove-mcp`), dual-mode (local + Apify)
- [x] **Python**: 3.12, `uv` workspaces, `hatchling` builds
- [x] **Parsers**: pure functions, HTML → Pydantic. No browser in the scrapers package.
- [x] **Crawlers**: Crawlee + Playwright live in Apify actors. MCPs use plain Playwright for local mode.
- [x] **Scraping vs API**: Playwright only where required (~6 sources); `httpx` for ~20+ free APIs
- [x] **AVM depth**: **Middle** (PPD + EPC + XGBoost + hedonic regression, ±15–20%)
- [x] **Demographics scope**: UK-only (ONS + Census 2021 + Nomis + IMD + MHCLG), not global
- [x] **Climate risk**: composite actor (flood + coastal + subsidence + UKCP18 20-yr projection)
- [x] **Location intelligence**: separate actor (A12), also baked into the agent via `packages/geo`
- [x] **Agent interactive surfaces (2026-04-19)**: three surfaces over the same `PropertyAgent` + `InMemorySaver` checkpointer — `property-agent ask` (one-shot), `property-agent chat` (terminal REPL with slash commands), `property-agent serve` (Chainlit web demo). **No `uk-property-agent` MCP wrapper** — target demo audience isn't in Claude Desktop / Cursor; the Chainlit UI covers the gap. The agent's public API stays MCP-compatible so a wrapper can be added later.
- [x] **Alerts**: **Discord** webhooks (per-actor severity channels), not Slack
- [x] **Canonical `Listing` schema**: defined in `uk-property-scrapers`, imported everywhere
- [x] **Pricing philosophy**: cost-plus floor; premium on gap actors (A4, A5, A10)
- [x] **Private-side philosophy**: reliability/proxy/caching as competitive moat, kept closed
- [x] **No timing commitments in this plan**

### Pending
- [ ] **Brand name** — repos + Apify handle + domain; `property-intel` is a placeholder
- [ ] **Apify account** — sign up, confirm username, configure residential proxy tier
- [ ] **GitHub namespace** — personal (`kyavuz/...`) vs org
- [ ] **Domain(s) to squat** — `propertyintel.uk`, `plotintel.uk`, `nestlens.uk`, etc.
- [ ] **LLM provider defaults** — Anthropic primary, Gemini Flash for batch, budget caps
- [ ] **OSRM / OTP hosting** — co-locate on Apify or self-host VPS

### Deferred (out of scope for v1)
- Hosted SaaS at `propertyintel.uk` (after Apify traction)
- Mobile app
- Scotland-specific data extensions (Home Reports, different planning regime)
- Commercial property beyond Rightmove/Zoopla Commercial
- Pre-2008 heritage property records (EPC predates this)
- Bundle actor A13 (ship if demand shows)
- `uk-property-agent-mcp` sibling repo — cut on 2026-04-19; revisit if the user base shifts to Claude Desktop / Cursor
- Persistent agent memory — `InMemorySaver` (per-process) is the v1 checkpointer; `SqliteSaver` / `PostgresSaver` swap is a one-line constructor change, deferred until a SaaS vs CLI persistence decision is made
- Auth on the Chainlit demo UI — explicitly demo-only; no hosting commitment today

---

## 10. Open Questions (by phase)

### Immediate (blocks scaffolding completion)
- Publish-as-whom on PyPI + Smithery? (brand or personal)
- Discord server name + logo — low priority but worth having a placeholder

### Before first Apify deploy
- Apify proxy tier — residential IPs are paid add-on
- Actor pricing — do we launch at list price or undercut 20% for first month?
- Legal disclaimer wording for OSS READMEs — standard research/educational phrasing

### Before agent ship
- Customer discovery — 5 calls with target users (BTL investor, PropTech founder, data journalist, renter, mortgage broker) before finalising agent UX
- Cache backend — SQLite local vs Supabase KV vs Redis
- First launch campaign — Show HN, LinkedIn, Reddit sequencing

### Before SaaS spin-out
- See §11

---

## 11. SaaS Upgrade Path

The Apify + OSS phase is free market validation for a larger product. Watch for these signals and flip the switch when any arrives:

| Signal | Action |
|---|---|
| Single Apify actor crosses 20+ paying users | Stand up direct SaaS (`propertyintel.uk`), migrate top users off Apify |
| Enterprise DM (PropTech, fund, lender) asking for volume pricing / SLA | Direct £2–10k/mo contract, skip Apify's cut |
| Apify MRR crosses £3–5k | De-risk leaving day-job for 6-month focused push |
| VC inbound | Either raise or ignore; keep IP either way |

The architectural commitment that makes this cheap: **every actor's core logic lives in the monorepo as a Python package. The Apify actor is a thin wrapper. The same package can be deployed to Apify, a FastAPI SaaS, or a CLI with zero rewrite.** Write once, deploy anywhere.

---

## 12. Metrics To Track

| Metric | Milestone signal |
|---|---|
| GitHub stars (monorepo + 2 MCPs) | 300 early traction → 1,500 strong signal |
| PyPI installs / week (scrapers, apis, agent, 2 MCPs) | 50 → 500 |
| Smithery installs | 100 → 1,000 |
| Actor registered users (total across 12 actors) | 50 → 250 |
| Actor monthly active users | 15 → 60 |
| Actor success rate (per actor) | Keep above 90% at all times |
| MRR on Apify | £500 validation → £4,000+ scale |
| Newsletter subscribers | 200 → 1,500 |
| Blog post cumulative views | 20,000 |
| LinkedIn DMs from recruiters or PropTech | Non-zero is success |
| Enterprise inquiries | 1 is success |

---

## 13. Technical Notes

### MCP specifics

- Official `mcp` Python SDK (Anthropic-maintained)
- Both stdio (Claude Desktop) and HTTP/SSE transports
- Tool naming: `search_zoopla`, `get_zoopla_listing` — verb_noun, specific enough for LLM selection
- Per-tool JSON Schemas derived from Pydantic models
- Smithery auto-discovery: `smithery.yaml` in repo root
- Dual-mode dispatch: `MODE` env var → delegates to local Playwright or Apify REST

### Anti-bot specifics per source

| Source | Protection | Strategy |
|---|---|---|
| Zoopla | Cloudflare | Residential proxy + stealth + session warming |
| Rightmove | Rate limit + UA checks | UA rotation + jitter + residential proxy |
| OnTheMarket | Moderate | UA + throttle |
| IDOX / Civica | Soft rate limits | Polite throttle, session cookies |
| Auction sites | Generally loose | Throttle |
| VOA | Lightly protected | Throttle + UA |
| Developer sites | Varies | Per-site tuning |

### Free gov API cheat sheet

| Source | Endpoint | Rate limit |
|---|---|---|
| EPC | `epc.opendatacommunities.org/api` | ~10 req/s |
| HMLR Price Paid | CSV download + small API | Generous |
| VOA council tax | `tax.service.gov.uk/check-council-tax-band` | Polite scrape |
| data.police.uk | `data.police.uk/api` | 15 req/s |
| Environment Agency Flood | `environment.data.gov.uk/flood-monitoring` | Reasonable |
| Ofsted | `reports.ofsted.gov.uk` + GIAS | Polite |
| ONS | `api.beta.ons.gov.uk` + Nomis | Generous |
| Companies House | `api.company-information.service.gov.uk` | 600 / 5 min |
| OSM Overpass | `overpass-api.de/api` | Fair use |
| postcodes.io | `api.postcodes.io` | Very generous |

### SQLite cache TTLs (per source)

| Source | TTL |
|---|---|
| EPC | 365 days |
| Council tax band | 365 days |
| Price Paid (most recent sale) | 90 days |
| Ofsted | 180 days |
| Crime stats | 30 days |
| Planning (list) | 1 day |
| Planning (application detail) | 7 days |
| Flood risk | 365 days |
| Climate projections | 365 days |
| Landlord graph | 30 days |
| Listings | never cached |
| LLM narratives | never cached |

### Apify webhook → Discord mapping

| Severity | Condition | Channel |
|---|---|---|
| Critical | Success rate < 70% OR run error | `#alerts-critical` |
| Degraded | 70–90% success | `#alerts-degraded` |
| Drift | Row count / schema anomaly | `#alerts-drift` |
| Info | Scheduled run completed OK | `#runs-daily` |
| Revenue | Apify payment event, new user | `#revenue` |

---

## 14. How to Use This Plan

- Treat as single source of truth; update as decisions evolve.
- Use the **Decision Log** (§9) to lock in every non-trivial architectural choice, with a timestamp on significant entries.
- Check off deliverables in §3 as they ship.
- Prune the **Open Questions** (§10) as they resolve.
- Revisit the **Risks** (§8) quarterly — probabilities and impacts shift as the market changes.

End of plan.
