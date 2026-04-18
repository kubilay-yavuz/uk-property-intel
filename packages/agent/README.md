# uk-property-agent

A natural-language agent for every property question that humans actually ask.

> *"I've got £1m for a London buy-to-let. Give me three properties that are
> most likely to appreciate over the next twenty years, and tell me why."*

## Architecture

LangGraph-based stateful workflow with the following high-level nodes:

1. **Intent parser** — classifies the user's goal (search / evaluate / compare
   / explain) and extracts structured constraints (budget, geography, yield
   targets, risk appetite).
2. **Search planner** — decomposes the query into portal searches, gov API
   lookups, and geospatial operations.
3. **Tool execution** — calls the scrapers, API clients, geo engine, and AVM
   in parallel, with retry / fallback logic.
4. **Ranking** — scores candidates on the user's objective function
   (appreciation, yield, liveability, etc.).
5. **Explainer** — writes a human-readable report with citations to every data
   source used.

## Why LangGraph over raw SDK?

- Durable state & checkpoints — a user can pause a search and resume.
- Parallel fan-out/fan-in is ergonomic.
- Observable traces via LangSmith.
- Streaming tokens + intermediate tool events are first-class.

The internal prompts target Anthropic Claude via `langchain-anthropic`, but the
graph is model-agnostic at the edges.

Pre-alpha: lands after the foundation packages (scrapers, apis, geo, avm) are
production-shaped.
