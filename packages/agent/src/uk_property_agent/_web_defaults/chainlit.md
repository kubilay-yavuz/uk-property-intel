# UK Property Intelligence

A natural-language research agent for the UK property market — listings,
sold prices, EPCs, planning, flood risk, commute isochrones, and full
postcode dossiers, all in one conversation.

---

### What the agent can reach

The assistant ships with **17 tools** by default and unlocks more when
optional API keys are present — up to **24 tools** with both
`EPC_AUTH_EMAIL` / `EPC_AUTH_TOKEN` and `COMPANIES_HOUSE_API_KEY`:

- **Listings** — Rightmove, Zoopla, OnTheMarket for sale and to let
- **Market data** — Land Registry Price Paid Data + AVM valuations
- **EPC register** — domestic certificates by postcode
  *(needs `EPC_AUTH_EMAIL` + `EPC_AUTH_TOKEN`)*
- **Planning & risk** — planning applications, flood warnings,
  crime stats, listed buildings
- **Geospatial** — drive-time and public-transport isochrones,
  nearby amenities, postcode lookup
- **Companies House** — company profiles, officers, PSCs, landlord
  network graphs *(needs `COMPANIES_HOUSE_API_KEY`)*
- **Synthesis** — full postcode dossiers combining all of the above
  in a single call

---

### How to use it

Ask in plain English. The agent plans, fires the relevant tools, and
shows each call as a collapsible step above its answer. Your
conversation is remembered for the duration of this browser tab — click
**New chat** (top-right) to start fresh.

> **Heads-up:** this deployment is for **local demos only**. There is no
> authentication, rate limiting, or persistence beyond in-memory.
