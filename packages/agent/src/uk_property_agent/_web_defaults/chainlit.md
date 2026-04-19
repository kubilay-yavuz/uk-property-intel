# UK Property Intelligence Agent

A local chat UI over the `uk-property-agent` LangGraph agent.
The assistant ships with **17 tools** by default and unlocks more when
optional API keys are present — up to **24 tools** with both
`EPC_AUTH_EMAIL` / `EPC_AUTH_TOKEN` and `COMPANIES_HOUSE_API_KEY`:

- **Listings** — Rightmove, Zoopla, OnTheMarket for sale / to let
- **Market data** — Land Registry Price Paid Data, AVM valuations
- **EPC register** (with `EPC_AUTH_EMAIL` + `EPC_AUTH_TOKEN`) — domestic certificates by postcode
- **Planning & risk** — planning applications, flood warnings, crime stats, listed buildings
- **Geospatial** — drive-time + public-transport isochrones, nearby amenities, postcode lookup
- **Companies House** (with `COMPANIES_HOUSE_API_KEY`) — company profiles, officers, PSCs, landlord network graphs
- **Synthesis** — full postcode dossiers combining all of the above in a single call

Ask in natural language. The agent will show which tools it fires
as collapsible steps above its answer. Your conversation is
remembered for the duration of this browser tab — click **New chat**
(top-right) to start fresh.

This deployment is intended for **local demos only** — there is no
authentication, rate limiting, or persistence beyond in-memory.
