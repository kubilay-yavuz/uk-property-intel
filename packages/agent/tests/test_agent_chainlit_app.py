"""Unit tests for the Chainlit rendering layer.

The renderer is deliberately framework-agnostic: it takes message /
step factories and drives them based on the agent's event stream.
These tests exercise that contract with hand-rolled fakes so we
don't need the Chainlit websocket server, React frontend, or any
network sockets at all.

We also exercise :func:`build_session` and :func:`swap_provider` with
a stub chat model to confirm the checkpointer is preserved across a
provider swap (the thing memory continuity depends on) and that a
fresh session gets its own isolated thread.

The code under test lives in :mod:`uk_property_agent.chainlit_render`
(the fully framework-free layer); :mod:`uk_property_agent.chainlit_app`
is only the thin Chainlit decorator shim and is smoke-tested via its
``module_path`` helper.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from uk_property_agent import chainlit_app, chainlit_render
from uk_property_agent.chainlit_render import (
    ChainlitRenderer,
    ChainlitSession,
    _short_input,
    _short_output,
    build_session,
    is_invalid_history_error,
    reset_thread,
    session_banner,
    swap_provider,
)
from uk_property_agent.providers import Provider, ProviderSpec, TaskKind

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeMessage:
    """Duck-typed stand-in for ``cl.Message``.

    Records ``send`` / ``update`` / ``stream_token`` call order so
    tests can assert the renderer sent the message exactly once,
    streamed tokens only after ``send``, and closed with ``update``.
    """

    content: str = ""
    sent: bool = False
    updated: bool = False
    streamed: list[str] = field(default_factory=list)

    async def send(self) -> None:
        assert not self.sent, "Message.send should be called exactly once"
        self.sent = True

    async def stream_token(self, text: str) -> None:
        assert self.sent, "stream_token before send would drop tokens in Chainlit"
        self.streamed.append(text)
        self.content += text

    async def update(self) -> None:
        self.updated = True


@dataclass
class FakeStep:
    """Duck-typed stand-in for ``cl.Step`` driven manually."""

    name: str
    type: str
    input: Any = None
    output: Any = None
    sent: bool = False
    updated: bool = False

    async def send(self) -> None:
        self.sent = True

    async def update(self) -> None:
        self.updated = True


class FakeFactory:
    """Callable factory that returns fresh fake objects and remembers them."""

    def __init__(self, cls: type) -> None:
        self._cls = cls
        self.instances: list[Any] = []

    def __call__(self, **kwargs: Any) -> Any:
        obj = self._cls(**kwargs)
        self.instances.append(obj)
        return obj


def make_renderer(*, show_tool_events: bool = True) -> tuple[ChainlitRenderer, FakeFactory, FakeFactory]:
    """Build a renderer with fresh fake factories.

    Returns a ``(renderer, message_factory, step_factory)`` triple so
    tests can both drive the renderer and introspect the fakes it
    produced.
    """

    msg = FakeFactory(FakeMessage)
    step = FakeFactory(FakeStep)
    r = ChainlitRenderer(
        message_factory=msg,
        step_factory=step,
        show_tool_events=show_tool_events,
    )
    return r, msg, step


async def _stream(events: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Turn a plain list into an async iterator for the renderer."""

    for e in events:
        yield e


# ---------------------------------------------------------------------------
# Render tests
# ---------------------------------------------------------------------------


class TestRendererBasics:
    def test_single_narrative_becomes_streamed_message(self) -> None:
        renderer, msg_factory, step_factory = make_renderer()
        events = [
            {"type": "narrative", "text": "Hello, "},
            {"type": "narrative", "text": "world."},
            {"type": "final", "text": "world."},
        ]

        result = asyncio.run(renderer.render(_stream(events)))

        assert len(msg_factory.instances) == 1
        message = msg_factory.instances[0]
        assert message.sent is True
        assert message.updated is True
        assert message.streamed == ["Hello, ", "world."]
        assert message.content == "Hello, world."
        assert result == "world."
        # No tool events = no steps
        assert step_factory.instances == []

    def test_final_only_fallback_streams_the_final_text(self) -> None:
        renderer, msg_factory, _ = make_renderer()
        events = [{"type": "final", "text": "answer once, no stream"}]

        result = asyncio.run(renderer.render(_stream(events)))

        message = msg_factory.instances[0]
        assert message.streamed == ["answer once, no stream"]
        assert result == "answer once, no stream"

    def test_duplicate_narrative_text_is_skipped(self) -> None:
        renderer, msg_factory, _ = make_renderer()
        events = [
            {"type": "narrative", "text": "duplicate"},
            {"type": "narrative", "text": "duplicate"},
            {"type": "final", "text": "duplicate"},
        ]

        asyncio.run(renderer.render(_stream(events)))

        message = msg_factory.instances[0]
        assert message.streamed == ["duplicate"]

    def test_empty_stream_yields_empty_message(self) -> None:
        renderer, msg_factory, step_factory = make_renderer()

        result = asyncio.run(renderer.render(_stream([])))

        assert result == ""
        assert msg_factory.instances[0].sent is True
        assert msg_factory.instances[0].updated is True
        assert msg_factory.instances[0].content == ""
        assert step_factory.instances == []


class TestRendererToolEvents:
    def test_tool_call_then_result_opens_and_closes_a_step(self) -> None:
        renderer, _, step_factory = make_renderer()
        events = [
            {
                "type": "tool_call",
                "name": "lookup_postcode",
                "args": {"postcode": "SW2 3RX"},
                "id": "call-1",
            },
            {
                "type": "tool_result",
                "name": "lookup_postcode",
                "content": '{"lat": 51.4, "lon": -0.1}',
                "id": "call-1",
            },
            {"type": "final", "text": "done"},
        ]

        asyncio.run(renderer.render(_stream(events)))

        assert len(step_factory.instances) == 1
        step = step_factory.instances[0]
        assert step.name == "lookup_postcode"
        assert step.type == "tool"
        assert step.sent is True
        assert step.updated is True
        assert "SW2 3RX" in str(step.input)
        assert "51.4" in str(step.output)

    def test_parallel_tool_calls_match_by_id(self) -> None:
        """Same-name parallel calls must not be conflated.

        Two calls of the same tool fire, then results arrive in
        reverse order. Without id matching the renderer would set the
        first result on the second step.
        """

        renderer, _, step_factory = make_renderer()
        events = [
            {"type": "tool_call", "name": "epc_lookup", "args": {"q": "A"}, "id": "a"},
            {"type": "tool_call", "name": "epc_lookup", "args": {"q": "B"}, "id": "b"},
            {"type": "tool_result", "name": "epc_lookup", "content": "result-B", "id": "b"},
            {"type": "tool_result", "name": "epc_lookup", "content": "result-A", "id": "a"},
            {"type": "final", "text": "ok"},
        ]

        asyncio.run(renderer.render(_stream(events)))

        assert len(step_factory.instances) == 2
        step_a, step_b = step_factory.instances
        assert "A" in str(step_a.input) and step_a.output == "result-A"
        assert "B" in str(step_b.input) and step_b.output == "result-B"

    def test_tool_result_without_prior_call_creates_orphan_step(self) -> None:
        renderer, _, step_factory = make_renderer()
        events = [
            {"type": "tool_result", "name": "orphan_tool", "content": "hi", "id": None},
            {"type": "final", "text": "done"},
        ]

        asyncio.run(renderer.render(_stream(events)))

        assert len(step_factory.instances) == 1
        step = step_factory.instances[0]
        assert step.name == "orphan_tool"
        assert step.output == "hi"
        assert step.sent is True and step.updated is True

    def test_tool_call_without_id_falls_back_to_name_fifo(self) -> None:
        renderer, _, step_factory = make_renderer()
        events = [
            {"type": "tool_call", "name": "geo", "args": {"x": 1}, "id": None},
            {"type": "tool_call", "name": "geo", "args": {"x": 2}, "id": None},
            {"type": "tool_result", "name": "geo", "content": "first", "id": None},
            {"type": "tool_result", "name": "geo", "content": "second", "id": None},
            {"type": "final", "text": "ok"},
        ]

        asyncio.run(renderer.render(_stream(events)))

        assert len(step_factory.instances) == 2
        assert step_factory.instances[0].output == "first"
        assert step_factory.instances[1].output == "second"

    def test_show_tool_events_false_skips_steps_entirely(self) -> None:
        renderer, _, step_factory = make_renderer(show_tool_events=False)
        events = [
            {"type": "tool_call", "name": "x", "args": {}, "id": "1"},
            {"type": "tool_result", "name": "x", "content": "hi", "id": "1"},
            {"type": "narrative", "text": "answer"},
            {"type": "final", "text": "answer"},
        ]

        asyncio.run(renderer.render(_stream(events)))

        assert step_factory.instances == []

    def test_tool_input_is_serialised_to_readable_json(self) -> None:
        renderer, _, step_factory = make_renderer()
        events = [
            {
                "type": "tool_call",
                "name": "search",
                "args": {"postcodes": ["SW2 3RX", "N1 9FB"], "radius_m": 500},
                "id": "x",
            },
            {"type": "tool_result", "name": "search", "content": "ok", "id": "x"},
            {"type": "final", "text": "ok"},
        ]

        asyncio.run(renderer.render(_stream(events)))

        step_input = str(step_factory.instances[0].input)
        assert '"postcodes"' in step_input and "SW2 3RX" in step_input
        assert '"radius_m": 500' in step_input

    def test_long_tool_output_is_truncated(self) -> None:
        renderer, _, step_factory = make_renderer()
        huge = "x" * 10000
        events = [
            {"type": "tool_call", "name": "dump", "args": {}, "id": "1"},
            {"type": "tool_result", "name": "dump", "content": huge, "id": "1"},
            {"type": "final", "text": "ok"},
        ]

        asyncio.run(renderer.render(_stream(events)))

        output = str(step_factory.instances[0].output)
        assert len(output) < len(huge)
        assert "truncated" in output


# ---------------------------------------------------------------------------
# Short helpers
# ---------------------------------------------------------------------------


class TestShortHelpers:
    def test_short_input_serialises_dicts(self) -> None:
        out = _short_input({"a": 1, "b": [2, 3]})
        assert '"a": 1' in out and '"b": [' in out

    def test_short_input_handles_none(self) -> None:
        assert _short_input(None) is None

    def test_short_input_falls_back_to_repr_on_unserialisable(self) -> None:
        # ``default=str`` in json.dumps catches most things via ``str()``;
        # to hit the ``except`` branch we need an object whose ``__str__``
        # *also* raises, which forces ``_short_input`` into the repr path.
        class Raiser:
            def __repr__(self) -> str:
                return "<Raiser>"

            def __str__(self) -> str:
                raise ValueError("no str")

        out = _short_input({"x": Raiser()})
        assert "<Raiser>" in out or "Raiser" in out

    def test_short_output_below_limit_passes_through(self) -> None:
        assert _short_output("short", limit=100) == "short"

    def test_short_output_above_limit_cuts_with_marker(self) -> None:
        s = "a" * 200
        out = _short_output(s, limit=50)
        assert out.startswith("a" * 50)
        assert "truncated 150 chars" in out


# ---------------------------------------------------------------------------
# Session / banner / module path helpers
# ---------------------------------------------------------------------------


class _StubChatModel(FakeMessagesListChatModel):
    """LangGraph-compatible fake model.

    Subclasses :class:`FakeMessagesListChatModel` so the react-agent
    executor in ``create_react_agent`` can treat it as a real
    ``Runnable``. Overrides ``bind_tools`` to a no-op (the default
    raises), because the agent unconditionally calls it during graph
    construction.
    """

    def bind_tools(self, _tools: Any, **_: Any) -> _StubChatModel:  # type: ignore[override]
        return self


def _patch_build_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``build_chat_model`` with a scripted fake — no network."""

    def _fake(_spec: ProviderSpec) -> Any:
        return _StubChatModel(responses=[AIMessage(content="stubbed")])

    monkeypatch.setattr("uk_property_agent.chainlit_render.build_chat_model", _fake)


def _patch_resolve_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass env-driven provider resolution in unit tests."""

    def _fake(task: Any = TaskKind.DEFAULT, explicit: Any = None, temperature: float = 0.2) -> ProviderSpec:
        slug = str(explicit) if explicit else "anthropic/test-model"
        provider_str, _, model = slug.partition("/")
        return ProviderSpec(
            provider=Provider(provider_str or "anthropic"),
            model=model or "test-model",
            temperature=temperature,
        )

    monkeypatch.setattr("uk_property_agent.chainlit_render.resolve_provider", _fake)


class TestSessionHelpers:
    def test_build_session_creates_agent_with_checkpointer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_build_chat_model(monkeypatch)
        _patch_resolve_provider(monkeypatch)

        session = build_session(temperature=0.1)

        assert isinstance(session, ChainlitSession)
        assert session.agent.checkpointer is not None
        assert session.checkpointer is session.agent.checkpointer
        assert session.spec.temperature == 0.1
        assert session.thread_id.startswith("web-")

    def test_swap_provider_preserves_checkpointer_and_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_build_chat_model(monkeypatch)
        _patch_resolve_provider(monkeypatch)

        session = build_session()
        original_checkpointer = session.checkpointer
        original_thread = session.thread_id

        new_session = swap_provider(session, "openai/gpt-4o")

        assert new_session.spec.slug == "openai/gpt-4o"
        assert new_session.checkpointer is original_checkpointer
        assert new_session.thread_id == original_thread
        assert new_session.agent is not session.agent

    def test_build_session_produces_fresh_thread_each_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_build_chat_model(monkeypatch)
        _patch_resolve_provider(monkeypatch)

        first = build_session()
        second = build_session()

        assert first.thread_id != second.thread_id
        assert first.checkpointer is not second.checkpointer

    def test_reset_thread_rotates_id_but_preserves_agent_and_checkpointer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Self-healing path for the "orphan tool_call" failure mode.

        When a provider refuses to replay a corrupted thread we rebuild
        the session id without touching the agent or checkpointer —
        the old thread's state stays reachable for debugging but the
        next turn starts on a clean slate.
        """

        _patch_build_chat_model(monkeypatch)
        _patch_resolve_provider(monkeypatch)

        session = build_session()
        healed = reset_thread(session)

        assert healed is not session
        assert healed.thread_id != session.thread_id
        assert healed.thread_id.startswith("web-")
        assert healed.agent is session.agent
        assert healed.checkpointer is session.checkpointer
        assert healed.spec is session.spec
        assert healed.tool_context is session.tool_context

    @pytest.mark.parametrize(
        "message",
        [
            (
                "Found AIMessages with tool_calls that do not have a "
                "corresponding ToolMessage."
            ),
            "INVALID_CHAT_HISTORY: tool_call_id ... orphaned",
            "Expecting matching function response parts for every tool call",
        ],
    )
    def test_is_invalid_history_error_matches_provider_signatures(
        self, message: str
    ) -> None:
        """Lock the signature matcher so future provider bumps don't silently regress."""

        assert is_invalid_history_error(ValueError(message)) is True

    def test_is_invalid_history_error_ignores_unrelated_errors(self) -> None:
        assert is_invalid_history_error(RuntimeError("rate limit exceeded")) is False
        assert is_invalid_history_error(TimeoutError("gateway timeout")) is False


class TestBannerAndPath:
    def test_session_banner_includes_slug_and_tool_count(self) -> None:
        spec = ProviderSpec(
            provider=Provider.ANTHROPIC,
            model="claude-sonnet-4-5-20250929",
            temperature=0.3,
        )
        banner = session_banner(spec, tool_count=17)

        assert "anthropic/claude-sonnet-4-5-20250929" in banner
        assert "17 tools" in banner
        assert "temperature 0.3" in banner

    def test_module_path_points_to_chainlit_app(self) -> None:
        path = chainlit_app.module_path()
        assert path.endswith("chainlit_app.py")

    def test_module_imports_without_chainlit(self) -> None:
        """Importing the render module must not require the chainlit extra.

        The framework-agnostic renderer + helpers live in
        :mod:`chainlit_render`; they must be importable whether or
        not the ``[web]`` extra is installed so unit tests / CLI
        path-resolution work in every install profile.
        """

        import importlib

        importlib.reload(chainlit_render)
        assert callable(chainlit_render.ChainlitRenderer)
