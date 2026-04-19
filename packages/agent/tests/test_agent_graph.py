"""End-to-end test of the ``PropertyAgent`` graph with a scripted fake model.

We avoid any real LLM call by scripting the response sequence:

1. ``AIMessage`` with a tool call to ``search_zoopla``.
2. ``AIMessage`` with the final natural-language answer.

Tool calls hit a respx-stubbed Zoopla search, so the whole chain exercises
the tool binding, tool execution, and final-answer extraction paths.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import respx
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from uk_property_agent import PropertyAgent, ToolContext
from uk_property_listings import SimpleCrawler


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """Fake chat model that supports ``.bind_tools`` as a no-op.

    ``create_react_agent`` calls ``bind_tools`` on the model before the first
    invocation. The stock ``FakeMessagesListChatModel`` raises for this, so we
    trivially implement it to return ``self`` - the scripted messages already
    encode the intended tool calls.
    """

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolCallingFakeModel:  # type: ignore[override]
        return self


FIXTURES = Path(__file__).parents[3] / "packages/scrapers/tests/fixtures"


@asynccontextmanager
async def _fast_crawler_factory():
    async with SimpleCrawler(request_timeout_s=5.0) as crawler:
        yield crawler


def _scripted_model() -> ToolCallingFakeModel:
    """Turn 1: call ``search_zoopla``; Turn 2: final text answer."""
    return ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_zoopla",
                        "args": {
                            "location": "Cambridge",
                            "transaction": "sale",
                            "max_pages": 1,
                        },
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=(
                    "I searched Zoopla and found listings in Cambridge. "
                    "Top picks depend on your appreciation horizon - see summary."
                )
            ),
        ]
    )


class TestPropertyAgent:
    @respx.mock
    async def test_ainvoke_runs_tool_and_returns_final_answer(self) -> None:
        html = (FIXTURES / "zoopla/search_cambridgeshire_2026-04.html").read_text()
        respx.get(url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*").mock(
            return_value=httpx.Response(200, html=html),
        )
        agent = PropertyAgent(
            model=_scripted_model(),
            tool_context=ToolContext(crawler_factory=_fast_crawler_factory),
        )
        answer = await agent.ainvoke("Show me family homes for sale in Cambridge.")
        assert "Cambridge" in answer
        assert "searched Zoopla" in answer

    def test_tools_property_exposes_all_built_tools(self) -> None:
        agent = PropertyAgent(
            model=_scripted_model(),
            tool_context=ToolContext(crawler_factory=_fast_crawler_factory),
        )
        names = {t.name for t in agent.tools()}
        assert "search_zoopla" in names
        assert "search_rightmove" in names
        assert "lookup_postcode" in names


class TestStreamingNarrative:
    @respx.mock
    async def test_astream_narrative_yields_final_answer_text(self) -> None:
        """FakeMessagesListChatModel can't stream tokens — we still get the answer.

        The fake model doesn't implement ``_astream`` in a way that
        yields mid-generation ``AIMessageChunk`` pieces, so
        ``stream_mode='messages'`` delivers the whole final ``AIMessage``
        in one shot. ``astream_narrative`` must still emit its text so
        the CLI and any downstream consumer has something to show.
        """

        html = (FIXTURES / "zoopla/search_cambridgeshire_2026-04.html").read_text()
        respx.get(url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*").mock(
            return_value=httpx.Response(200, html=html),
        )
        agent = PropertyAgent(
            model=_scripted_model(),
            tool_context=ToolContext(crawler_factory=_fast_crawler_factory),
        )

        chunks: list[str] = []
        async for token in agent.astream_narrative(
            "Show me family homes for sale in Cambridge."
        ):
            chunks.append(token)

        joined = "".join(chunks)
        assert "Cambridge" in joined
        assert "searched Zoopla" in joined

    @respx.mock
    async def test_astream_narrative_skips_tool_call_turn(self) -> None:
        """Turn 1's ``AIMessage(tool_calls=...)`` must not leak into the stream."""

        html = (FIXTURES / "zoopla/search_cambridgeshire_2026-04.html").read_text()
        respx.get(url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*").mock(
            return_value=httpx.Response(200, html=html),
        )
        agent = PropertyAgent(
            model=_scripted_model(),
            tool_context=ToolContext(crawler_factory=_fast_crawler_factory),
        )

        emitted: list[str] = []
        async for token in agent.astream_narrative(
            "Show me family homes for sale in Cambridge."
        ):
            emitted.append(token)

        joined = "".join(emitted)
        # The scripted tool call's args were search_zoopla(...); if the
        # tool-call turn had leaked, we'd see 'search_zoopla' or a JSON
        # representation of the args in the stream.
        assert "search_zoopla" not in joined
        assert "tool_calls" not in joined

    @respx.mock
    async def test_astream_events_emits_tool_call_then_narrative_then_final(
        self,
    ) -> None:
        """Structured events carry tool_call, tool_result, narrative, final in order."""

        html = (FIXTURES / "zoopla/search_cambridgeshire_2026-04.html").read_text()
        respx.get(url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*").mock(
            return_value=httpx.Response(200, html=html),
        )
        agent = PropertyAgent(
            model=_scripted_model(),
            tool_context=ToolContext(crawler_factory=_fast_crawler_factory),
        )

        events: list[dict[str, Any]] = []
        async for event in agent.astream_events(
            "Show me family homes for sale in Cambridge."
        ):
            events.append(event)

        types = [e["type"] for e in events]
        assert "tool_call" in types
        assert "narrative" in types or "final" in types
        # ``final`` must always be the last event when we see one.
        if "final" in types:
            assert types[-1] == "final"

        tool_calls = [e for e in events if e["type"] == "tool_call"]
        assert tool_calls and tool_calls[0]["name"] == "search_zoopla"

        narrative = "".join(
            e["text"] for e in events if e["type"] == "narrative"
        )
        final = next(
            (e["text"] for e in events if e["type"] == "final"), ""
        )
        full = narrative or final
        assert "Cambridge" in full
