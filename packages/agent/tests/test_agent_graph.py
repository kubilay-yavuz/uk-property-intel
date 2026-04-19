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
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
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


class TestToolErrorHandling:
    """Guards against the Chainlit + Gemini "orphan tool_call" regression.

    When a UK property tool raises an arbitrary :class:`Exception`
    (postcodes.io 404, Zoopla 403, OTP timeout…) LangGraph's default
    ``_default_handle_tool_errors`` only swallows ``ToolInvocationError``
    — everything else re-raises, the graph crashes mid-turn, and the
    checkpointer ends up with an ``AIMessage(tool_calls=...)`` that has
    no matching :class:`ToolMessage`. Gemini then rejects every future
    turn on that thread with::

        Found AIMessages with tool_calls that do not have a
        corresponding ToolMessage.

    We fix this in :class:`PropertyAgent.__init__` by wiring an explicit
    :class:`ToolNode` whose ``handle_tool_errors`` is
    :func:`uk_property_agent.tools._format_tool_error`. These tests lock
    that wiring in place so a future refactor can't silently regress it.
    """

    class _BoomError(Exception):
        pass

    @staticmethod
    async def _raising_postcode_lookup(postcode: str) -> dict[str, Any]:
        raise TestToolErrorHandling._BoomError(
            f"pretend 404 for {postcode}"
        )

    @classmethod
    def _raising_tool(cls) -> StructuredTool:
        """Tool that always raises — simulates postcodes.io 404 / Zoopla 403."""

        return StructuredTool.from_function(
            name="lookup_postcode",
            description="Stub that always raises to simulate an upstream API failure.",
            coroutine=cls._raising_postcode_lookup,
        )

    @classmethod
    def _build_agent_with_raising_tool(cls) -> PropertyAgent:
        """Monkey-patch ``build_tools`` so the agent uses only the raising stub.

        We replace the module-level hook rather than patching the
        ``ToolContext`` so the ``ToolNode`` gets exactly one tool: the
        one we control.
        """

        import uk_property_agent.agent as agentmod
        import uk_property_agent.tools as toolmod

        raising = cls._raising_tool()
        original_build = toolmod.build_tools

        def _patched(_ctx: ToolContext | None = None) -> list[StructuredTool]:
            return [raising]

        toolmod.build_tools = _patched  # type: ignore[assignment]
        agentmod.build_tools = _patched  # type: ignore[assignment]
        try:
            model = ToolCallingFakeModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "lookup_postcode",
                                "args": {"postcode": "CB1 1AA"},
                                "id": "tc-boom",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(
                        content=(
                            "That postcode looks off — could you double-check it?"
                        )
                    ),
                ]
            )
            return PropertyAgent(model=model, checkpointer=InMemorySaver())
        finally:
            toolmod.build_tools = original_build
            agentmod.build_tools = original_build

    async def test_raising_tool_becomes_tool_message_error(self) -> None:
        """ToolNode converts the raised exception into a ToolMessage.

        Without this, Gemini rejects the next turn with INVALID_CHAT_HISTORY
        because the AIMessage has a tool_call with no matching ToolMessage.
        """

        agent = self._build_agent_with_raising_tool()
        answer = await agent.ainvoke("Find houses near CB1 1AA", thread_id="err-smoke")
        assert "double-check" in answer

        state = agent.graph.get_state({"configurable": {"thread_id": "err-smoke"}})
        messages = state.values.get("messages", [])

        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_messages) == 1, (
            "exactly one ToolMessage should close the AIMessage's tool_call"
        )
        tm = tool_messages[0]
        assert tm.tool_call_id == "tc-boom"
        assert tm.content.startswith("[tool-error] ")
        assert "_BoomError" in tm.content
        assert "CB1 1AA" in tm.content

    async def test_tool_error_allows_follow_up_turn(self) -> None:
        """After the error the thread is still valid — a second turn doesn't crash.

        This is the regression: before the fix, the Chainlit user sent
        "hello" after a failed tool call and Gemini refused the replay.
        We don't need a live provider to assert it: checking that the
        checkpointed history has no orphan tool_calls is sufficient, as
        that's exactly the invariant Gemini enforces.
        """

        agent = self._build_agent_with_raising_tool()
        await agent.ainvoke("Find houses near CB1 1AA", thread_id="err-replay")

        state = agent.graph.get_state({"configurable": {"thread_id": "err-replay"}})
        messages = state.values.get("messages", [])

        open_tool_calls: set[str] = set()
        for msg in messages:
            tcs = getattr(msg, "tool_calls", None) or []
            for tc in tcs:
                tid = tc.get("id") if isinstance(tc, dict) else None
                if tid:
                    open_tool_calls.add(tid)
            if isinstance(msg, ToolMessage) and msg.tool_call_id in open_tool_calls:
                open_tool_calls.discard(msg.tool_call_id)

        assert open_tool_calls == set(), (
            f"orphan tool_calls would break the next Gemini turn: {open_tool_calls}"
        )

    def test_all_built_tools_carry_error_handler(self) -> None:
        """Belt-and-suspenders: every ``StructuredTool`` also has ``handle_tool_error`` set.

        The ``ToolNode``-level handler is the primary defence (catches
        arbitrary ``Exception``), but we also flag ``StructuredTool``
        itself so ``ToolException`` raised explicitly by a tool gets
        handled within the tool's tracing context — cleaner LangSmith
        traces.
        """

        from uk_property_agent.tools import _format_tool_error, build_tools

        for tool in build_tools():
            assert tool.handle_tool_error is _format_tool_error, (
                f"{tool.name} is missing the shared error handler"
            )
