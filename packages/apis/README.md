# uk-property-apis

Typed async Python clients for every UK government / public API that matters for
property intelligence. Designed to be composed inside Apify actors, MCP servers,
or the LangGraph agent.

| Client | Source | Auth |
| --- | --- | --- |
| `epc` | [Energy Performance Certificates](https://epc.opendatacommunities.org/) | Email + token (Basic) |
| `land_registry` | [Price Paid Data](https://landregistry.data.gov.uk/) | None |
| `companies_house` | [Companies House REST](https://developer.company-information.service.gov.uk/) | API key |
| `planning` | [planning.data.gov.uk](https://www.planning.data.gov.uk/) | None |
| `police` | [data.police.uk](https://data.police.uk/) | None |
| `postcodes` | [postcodes.io](https://postcodes.io/) | None |
| `flood` | [Environment Agency Flood APIs](https://environment.data.gov.uk/flood-monitoring/doc/reference) | None |
| `ons` | [Office for National Statistics](https://developer.ons.gov.uk/) | None |

## Design principles

1. **Async-first** — every client is `httpx.AsyncClient`-based so actors can
   parallelise requests with minimal overhead.
2. **Typed responses** — Pydantic v2 models for every endpoint. No `dict[str, Any]`
   leaks across the public API.
3. **Polite by default** — built-in rate-limiting and exponential backoff via
   `tenacity`; clients accept an optional `semaphore` for cross-request concurrency
   control.
4. **Deterministic errors** — all transport and parsing failures surface as
   typed exceptions (`RateLimitError`, `AuthError`, `NotFoundError`, etc.).
