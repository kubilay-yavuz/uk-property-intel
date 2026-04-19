"""Unit tests for the :mod:`uk_property_agent.repl` chat REPL.

Two layers of coverage:

1. **Pure REPL plumbing** — slash commands, thread-id rotation, stream
   toggle, provider-swap error surface — exercised against a
   pre-built :class:`PropertyAgent` with a scripted
   :class:`FakeMessagesListChatModel` so no live LLM traffic runs.

2. **Multi-turn memory** — two consecutive ``ask`` calls against the
   same ``ChatLoop`` share a LangGraph ``InMemorySaver`` checkpoint.
   The fake model returns a scripted final answer per turn; the test
   asserts the graph saw *both* the turn-1 user message and the
   turn-2 user message in its final state, which is only possible
   when the checkpointer is wired correctly.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from uk_property_agent import PropertyAgent, ToolContext
from uk_property_agent.providers import Provider, ProviderSpec
from uk_property_agent.repl import (
    ChatLoop,
    ChatLoopOptions,
    replay_scripted,
)


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """Fake model that accepts ``.bind_tools`` — mirrors test_agent_graph."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolCallingFakeModel:  # type: ignore[override]
        return self


def _simple_fake_model(answers: list[str]) -> ToolCallingFakeModel:
    return ToolCallingFakeModel(
        responses=[AIMessage(content=answer) for answer in answers]
    )


def _spec() -> ProviderSpec:
    """Small helper — the REPL only reads ``.slug`` for banner output."""

    return ProviderSpec(provider=Provider.ANTHROPIC, model="fake-model")


def _build_loop(
    answers: list[str],
    *,
    options: ChatLoopOptions | None = None,
    checkpointer: Any | None = None,
) -> tuple[ChatLoop, list[str], list[str]]:
    """Construct a ChatLoop with capturing output/error sinks.

    Returns ``(loop, stdout_lines, stderr_lines)`` so tests can
    introspect exactly what was written where.
    """

    stdout: list[str] = []
    stderr: list[str] = []
    if checkpointer is None:
        checkpointer = InMemorySaver()
    agent = PropertyAgent(
        model=_simple_fake_model(answers),
        tool_context=ToolContext(),
        checkpointer=checkpointer,
        enable_prompt_cache=False,
    )
    opts = options or ChatLoopOptions(stream=False)
    loop = ChatLoop(
        agent=agent,
        spec=_spec(),
        options=opts,
        tool_context=None,
        output_fn=stdout.append,
        error_fn=stderr.append,
    )
    return loop, stdout, stderr


class TestSlashCommands:
    async def test_help_prints_verbs(self) -> None:
        loop, out, _ = _build_loop(["stub"])
        await loop.handle_slash("/help")
        joined = "\n".join(out)
        assert "/help" in joined
        assert "/exit" in joined
        assert "/clear" in joined
        assert "/provider" in joined

    async def test_exit_returns_exit_verdict(self) -> None:
        loop, _, _ = _build_loop(["stub"])
        assert await loop.handle_slash("/exit") == "exit"
        assert await loop.handle_slash("/quit") == "exit"
        assert await loop.handle_slash("/q") == "exit"

    async def test_clear_rotates_thread_id(self) -> None:
        loop, out, _ = _build_loop(["stub"])
        original = loop.thread_id
        await loop.handle_slash("/clear")
        assert loop.thread_id != original
        assert any("new thread" in line.lower() for line in out)

    async def test_stream_toggle(self) -> None:
        loop, out, _ = _build_loop(["stub"])
        loop.options.stream = True
        await loop.handle_slash("/stream off")
        assert loop.options.stream is False
        assert any("stream" in line.lower() and "off" in line.lower() for line in out)

        await loop.handle_slash("/stream on")
        assert loop.options.stream is True

    async def test_stream_invalid_value_surfaces_error(self) -> None:
        loop, _, err = _build_loop(["stub"])
        await loop.handle_slash("/stream maybe")
        assert any("stream" in line.lower() and "maybe" in line for line in err)

    async def test_unknown_verb_prints_error(self) -> None:
        loop, _, err = _build_loop(["stub"])
        await loop.handle_slash("/nonsense")
        assert any("unknown verb" in line.lower() for line in err)

    async def test_tools_lists_bound_tools(self) -> None:
        loop, out, _ = _build_loop(["stub"])
        await loop.handle_slash("/tools")
        joined = "\n".join(out)
        assert "search_zoopla" in joined or "lookup_postcode" in joined

    async def test_thread_prints_current_id(self) -> None:
        loop, out, _ = _build_loop(["stub"])
        await loop.handle_slash("/thread")
        assert any(loop.thread_id in line for line in out)

    async def test_provider_without_args_prints_current(self) -> None:
        loop, out, _ = _build_loop(["stub"])
        await loop.handle_slash("/provider")
        assert any(loop.spec.slug in line for line in out)

    async def test_provider_swap_resolution_error_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Swap to a provider with no creds — error in error_fn, no crash."""

        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AGENT_PROVIDER",
            "AGENT_PROVIDER_CHAIN",
            "AGENT_MODEL_DEFAULT",
        ):
            monkeypatch.delenv(var, raising=False)

        loop, _, err = _build_loop(["stub"])
        original_spec = loop.spec
        await loop.handle_slash("/provider openai/gpt-4o")
        assert loop.spec == original_spec, "swap should not mutate state on error"
        joined = "\n".join(err)
        assert "openai/gpt-4o" in joined or "No provider credentials" in joined


class TestMultiTurnMemory:
    async def test_second_turn_sees_first_turn_messages(self) -> None:
        """LangGraph checkpointer must persist the first user message."""

        checkpointer = InMemorySaver()
        loop, _, _ = _build_loop(
            answers=["First answer.", "Second answer."],
            options=ChatLoopOptions(stream=False),
            checkpointer=checkpointer,
        )
        await loop.ask("Tell me about CB1.")
        await loop.ask("And CB2?")

        config = {"configurable": {"thread_id": loop.thread_id}}
        snapshot = loop.agent.graph.get_state(config)
        messages = snapshot.values.get("messages", [])
        human_texts = [m.content for m in messages if isinstance(m, HumanMessage)]
        assert "Tell me about CB1." in human_texts
        assert "And CB2?" in human_texts

    async def test_new_thread_isolates_history(self) -> None:
        """``/clear`` must produce a fresh, empty thread."""

        checkpointer = InMemorySaver()
        loop, _, _ = _build_loop(
            answers=["A", "B"],
            options=ChatLoopOptions(stream=False),
            checkpointer=checkpointer,
        )
        first_thread = loop.thread_id
        await loop.ask("first question")

        await loop.handle_slash("/clear")
        assert loop.thread_id != first_thread

        new_config = {"configurable": {"thread_id": loop.thread_id}}
        snapshot = loop.agent.graph.get_state(new_config)
        msgs = snapshot.values.get("messages", []) if snapshot.values else []
        assert not any(
            isinstance(m, HumanMessage) and "first question" in m.content
            for m in msgs
        )


class TestAskNonStreaming:
    async def test_ask_writes_answer_to_output(self) -> None:
        loop, out, _ = _build_loop(
            ["Ranked list of family homes..."],
            options=ChatLoopOptions(stream=False),
        )
        result = await loop.ask("Family homes in Cambridge?")
        assert "family homes" in result.lower()
        assert any("family homes" in line.lower() for line in out)


class TestReplayScripted:
    async def test_mixes_slash_and_plain_prompts(self) -> None:
        """Drive the loop with a scripted iterable: slash verbs vs questions."""

        loop, _, _ = _build_loop(
            ["Answer one.", "Answer two."],
            options=ChatLoopOptions(stream=False),
        )

        script = [
            "/help",
            "First question",
            "/clear",
            "Second question",
            "/exit",
        ]
        answers: list[str] = []
        async for answer in replay_scripted(loop, script):
            answers.append(answer)

        non_empty = [a for a in answers if a]
        assert len(non_empty) == 2
        assert "Answer one" in non_empty[0]
        assert "Answer two" in non_empty[1]


class TestCLIWiring:
    """Thin integration: the CLI `chat` subcommand dispatches to _run_chat.

    Declared sync (not ``async def``) because :func:`cli.main` uses
    :func:`asyncio.run` internally to drive the coroutine returned by
    ``_run_chat`` — and ``asyncio.run`` refuses to nest inside a running
    loop, which is what pytest-asyncio would give us.
    """

    def test_chat_subcommand_runs_and_exits_cleanly(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Patch ``build_chat_loop`` so we don't build a real model."""

        from uk_property_agent import cli
        from uk_property_agent import repl as repl_module

        recorded: dict[str, Any] = {}

        class _FakeLoop:
            def __init__(self) -> None:
                self.thread_id = "test-thread"

            async def run(self) -> int:
                recorded["ran"] = True
                return 0

        def _fake_build(spec: Any, **kw: Any) -> Any:
            recorded["spec"] = spec
            recorded["options"] = kw.get("options")
            return _FakeLoop()

        monkeypatch.setattr(cli, "_resolve_spec", lambda args: _spec())
        monkeypatch.setattr(repl_module, "build_chat_loop", _fake_build)

        rc = cli.main(["chat"])
        assert rc == 0
        assert recorded.get("ran") is True
        assert recorded["options"].stream is True

    def test_chat_subcommand_respects_no_stream(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from uk_property_agent import cli
        from uk_property_agent import repl as repl_module

        recorded: dict[str, Any] = {}

        class _FakeLoop:
            async def run(self) -> int:
                return 0

        def _fake_build(spec: Any, **kw: Any) -> Any:
            recorded["options"] = kw.get("options")
            return _FakeLoop()

        monkeypatch.setattr(cli, "_resolve_spec", lambda args: _spec())
        monkeypatch.setattr(repl_module, "build_chat_loop", _fake_build)

        rc = cli.main(["chat", "--no-stream"])
        assert rc == 0
        assert recorded["options"].stream is False
