# uk-property-agent

A natural-language agent for every property question that humans actually ask.

> *"I've got £1m for a London buy-to-let. Give me three properties that are
> most likely to appreciate over the next twenty years, and tell me why."*

The agent is a LangGraph ReAct graph with 23 `StructuredTool`s bound for UK
property data — listings (Rightmove / Zoopla / OnTheMarket), sold prices
(HMLR Price Paid), EPC, planning, flood + crime, commute isochrones, an AVM,
and a one-shot postcode dossier that fans out across all of the above in
parallel. It routes across Anthropic, OpenAI, and Gemini, streams tokens,
caches prompts, and has been through 134 mocked tests.

## Quickstart — three ways to drive it

You need credentials for at least one LLM provider. Set one of
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` / `GEMINI_API_KEY`
before any of the commands below.

### 1. One-shot: `property-agent ask`

Best for scripts, piping into logs, and quick smoke tests.

```bash
# Buffered: full narrative printed when the agent finishes.
uv run property-agent ask "Tell me about CB1 2JW"

# Streamed: tokens to stdout, tool calls to stderr — pipe stdout cleanly.
uv run property-agent ask --stream "Best 3 family homes in Cambridge under £800k" \
    > answer.md 2> trace.log

# Pin a provider / model / task.
uv run property-agent ask --provider openai/gpt-4o "..."
uv run property-agent ask --provider gemini --task dossier "..."
uv run property-agent ask --show-provider "..."   # prints resolved route to stderr
```

### 2. Interactive: `property-agent chat`

A terminal REPL backed by a LangGraph `InMemorySaver` checkpointer so the
model sees the whole conversation each turn. Slash commands mirror the
`ask` flags so you can swap provider / streaming mid-session without
restarting.

```bash
uv run property-agent chat
# > /help                show slash commands
# > /provider openai     swap provider for the next turn onwards
# > /tools               list the tools bound to the current session
# > /stream on|off       toggle inline tool-call rendering
# > /clear               reset conversation memory (new thread_id)
# > /exit                quit
```

Memory is per-session only — closing the REPL drops the history.

### 3. Web UI: `property-agent serve` (demo only)

A Chainlit chat UI over the same agent, with per-tab thread memory, streamed
answers, and collapsible step panels showing each tool call + result.
Intended for **local demos only** — there is no auth, no rate limiting, no
persistence beyond the in-memory checkpointer.

```bash
# First install the web extra (pulls in chainlit):
uv pip install 'uk-property-agent[web]'

# Then launch:
uv run property-agent serve                           # http://127.0.0.1:8000
uv run property-agent serve --port 7861 --host 0.0.0.0   # expose on LAN
uv run property-agent serve --headless                # don't auto-open browser
uv run property-agent serve --provider gemini         # pin initial provider
```

On first run, a branded runtime directory is seeded at
`~/.uk-property-agent/web/` (contains `chainlit.md` + `.chainlit/config.toml`)
so the UI shows "UK Property Intelligence" with dark theme and four starter
prompts out of the box. Edit either file to customise; the CLI re-uses them
as-is on subsequent runs.

### Environment snapshot: `property-agent env`

```bash
uv run property-agent env           # human-readable table
uv run property-agent env --json    # machine-readable (CI-friendly)
```

Shows: which LLM providers have credentials, the Apify + data-API keys the
tools expect, LangSmith tracing vars, and per-task `AGENT_MODEL_<TASK>`
overrides that have been set.

### LangSmith tracing (optional)

Set three env vars and every graph run — `ask`, `chat`, or `serve` — streams
traces to your LangSmith project:

```bash
export LANGSMITH_API_KEY=ls__...
export LANGSMITH_PROJECT=uk-property-agent
export LANGSMITH_TRACING=true
```

`property-agent env` reports whether they're picked up.

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

Five tools transparently delegate to hosted Apify actors when
`APIFY_API_TOKEN` is set (planning, landlord graph, AVM, drive/transit
isochrones); the rest run local clients.

## Why LangGraph over raw SDK?

- Durable state & checkpoints — `chat` + `serve` use `InMemorySaver` to keep
  a conversation's `messages` list across turns without reshipping it.
- Parallel fan-out/fan-in is ergonomic (used by `build_property_dossier`).
- Observable traces via LangSmith.
- Streaming tokens + intermediate tool events are first-class — what powers
  `ask --stream`, the REPL, and the Chainlit step panels.

## Install extras

```toml
uk-property-agent                 # core, Anthropic by default
uk-property-agent[anthropic]      # explicit Anthropic SDK
uk-property-agent[openai]         # OpenAI SDK
uk-property-agent[gemini]         # Gemini SDK
uk-property-agent[web]            # + Chainlit for property-agent serve
uk-property-agent[all]            # all three providers + web
```

## Status

Alpha. 134 mocked tests green. Agent v3 (multi-provider routing + streaming
narrative) shipped 2026-04-19. Agent v4 (memory checkpointer + CLI chat REPL
+ Chainlit web UI) shipped 2026-04-19.
