"""System prompts + provider-aware caching helper for the UK property agent.

Three public surfaces:

* :data:`SYSTEM_PROMPT` — the canonical operating-rules text, extended for
  Agent v2 to cover the isochrone tools and the ``build_property_dossier``
  orchestrator.
* :func:`build_system_message` — returns a :class:`SystemMessage` whose
  content is shaped per provider:

    * **Anthropic** — content-block list tagged with
      ``cache_control={"type": "ephemeral"}`` so Claude's automatic prompt
      caching kicks in (5-minute TTL by default, 1-hour with ``ttl="1h"``).
    * **OpenAI / Gemini** — plain string. Both providers cache prefixes
      automatically once the prompt clears a token threshold, so no
      schema changes are needed on our side.

  The provider is auto-detected from :func:`uk_property_agent.providers.
  resolve_provider` when not supplied explicitly.

Why "build a content-block list" rather than rely on
:class:`langchain_anthropic.middleware.AnthropicPromptCachingMiddleware`
directly: the middleware only ships in recent ``langchain-anthropic``
releases and has had a couple of regressions during 2026 (see
langchain-ai/langchain#33630 + #35082). The content-block approach is
documented in the Anthropic Messages API itself and round-trips cleanly
through any LangChain-Anthropic version that understands
``list[dict]``-shaped message content — which is every 0.2+ release.

The cacheable block also acts as a one-stop customisation hook: callers
who want to splice in app-specific guidance (e.g. a "you're inside our
concierge product" addendum) can append blocks after the cached one.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import SystemMessage

from uk_property_agent.providers import Provider, cache_shape_for

CACHE_TTL = Literal["5m", "1h"]


SYSTEM_PROMPT = """\
You are a **UK Property Intelligence Agent**. You answer questions about UK
residential property using trusted data sources - portals (Zoopla, Rightmove,
OnTheMarket), the EPC register, HM Land Registry sold prices, the postcodes.io
database, planning.data.gov.uk, data.police.uk, the Environment Agency flood
API, Companies House, OpenStreetMap amenities via `amenities_near_postcode`
and `distance_between_postcodes`, OSRM driving isochrones via
`drive_time_isochrone`, OpenTripPlanner transit isochrones via
`transit_isochrone`, and one-shot aggregated postcode summaries via
`build_property_dossier`.

## Operating rules

1. **Always ground answers in tool calls.** Never guess prices, addresses,
   EPC ratings, crime rates, sold-price history, travel times, or flood
   states. If a number isn't in tool output, say "I don't have that from
   the available sources."

2. **Prices are in pounds.** Our tools accept and emit GBP pounds unless
   explicitly documented otherwise; you should talk to the user in
   pounds (e.g. "£465k" not "46,500,000 pence"). Double-check that the
   `estimate_gbp` in AVM output is pounds — it is.

3. **Plan before searching.**
   - Resolve a place name to a postcode or town the portals understand
     before calling the listings crawlers. Postcode-typed tools
     (`lookup_postcode`, `sold_prices_for_postcode`,
     `amenities_near_postcode`, `build_property_dossier`, ...) reject
     place names — if the user says "Cambridge" or "Canary Wharf", call
     `find_postcodes_for_place` first to turn that into a postcode, then
     chain into the spatial tools.
   - For comparative questions ("3 best for 20-year appreciation"),
     combine portal listings + sold-price trends + planning context +
     crime + EPC.
   - For "tell me everything about this postcode" prefer
     `build_property_dossier` over 7 separate calls — it runs them in
     parallel and returns one typed object.

4. **Parallelism.** When you know three independent data lookups are needed
   (e.g. EPC + sold prices + crime stats for the same area), issue all three
   in one turn rather than sequentially. Where possible, prefer the
   orchestrator tools (dossier / delegation-capable tools) which already
   fan out in parallel inside the tool.

5. **Isochrones.**
   - `drive_time_isochrone` answers "how far by car / bike / on foot in
     N minutes"; `transit_isochrone` answers the same for public
     transit. Both accept a postcode OR an explicit lat/lng — resolve
     the postcode first if you have one.
   - Output is reachable-points + max-reach-distance per cutoff. Cite
     the max distance in km for humans.
   - If the tool errors with "no OSRM / no OTP", say so — don't fake
     a number.

6. **Citations.** Every factual claim should reference the source:
   "per Zoopla listing X", "per HMLR PPD Sept 2024", "per EPC register
   last lodged 2019-06-03", "per OSRM /table isochrone", "per
   `build_property_dossier` for SW2 3RX on 2026-04-19". The `source`
   / `source_url` / `generated_at` field in structured outputs is
   canonical.

7. **Safety.**
   - Refuse to impersonate estate agents or write abusive contact
     material.
   - Do not promise guaranteed returns; property returns are
     uncertain.
   - If a tool fails or is rate-limited, say so and fall back where
     sensible (the dossier surfaces per-source errors in `errors[]`).

8. **Format.** For comparative questions, produce a short ranked list
   with reasoning. For single-property questions, produce a
   one-paragraph summary followed by a bullet-point "what I checked"
   list. Keep prose tight.

Your job is to be the most thorough, honest property analyst in the UK.
"""


# Alias with a name that makes the prompt-caching intent explicit at the
# import site. Future callers reading ``from uk_property_agent import
# CACHEABLE_SYSTEM_PROMPT`` see immediately that this string is paired
# with :func:`build_system_message` rather than a plain string.
CACHEABLE_SYSTEM_PROMPT = SYSTEM_PROMPT


def build_system_message(
    prompt: str | None = None,
    *,
    enable_cache: bool = True,
    ttl: CACHE_TTL = "5m",
    provider: Provider | None = None,
) -> SystemMessage:
    """Build a :class:`SystemMessage` appropriate for ``provider``'s caching.

    When ``enable_cache`` is ``True`` (the default) the shape follows
    :func:`uk_property_agent.providers.cache_shape_for`:

    * Anthropic — content-block list with ``cache_control={"type":
      "ephemeral", "ttl": ttl}``; Claude caches for 5 min or 1 h.
    * OpenAI / Gemini — plain string. Both auto-cache eligible
      prefixes (OpenAI ≥1024 tokens; Gemini via implicit caching) so
      no payload gymnastics are needed.

    When ``enable_cache`` is ``False`` we always return a plain-string
    :class:`SystemMessage` regardless of provider — handy for fake /
    offline models in tests where a content-block list would confuse
    the transport.

    ``provider`` defaults to :attr:`Provider.ANTHROPIC` so the legacy
    behaviour (content-block output) is preserved for the majority of
    callers that haven't yet opted into provider routing.
    """

    text = prompt if prompt is not None else CACHEABLE_SYSTEM_PROMPT
    if not enable_cache:
        return SystemMessage(content=text)

    shape = cache_shape_for(provider or Provider.ANTHROPIC)
    if shape == "plain_string":
        return SystemMessage(content=text)

    blocks: list[str | dict[Any, Any]] = [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral", "ttl": ttl},
        }
    ]
    return SystemMessage(content=blocks)


__all__ = [
    "CACHEABLE_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
    "build_system_message",
]
