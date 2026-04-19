"""Chainlit-agnostic rendering logic for the UK Property agent.

Why a separate module? Chainlit's CLI loads the target file
(``chainlit_app.py``) with :func:`importlib.util.spec_from_file_location`
which creates a module object *without* registering it in
``sys.modules``. Any ``@dataclass`` declared there blows up during
``_process_class`` because CPython's dataclass machinery resolves
forward references via ``sys.modules.get(cls.__module__).__dict__``,
and that ``None`` lookup crashes.

The fix is to keep all dataclasses (plus the framework-free renderer
logic) here, in a module loaded via the normal import machinery, and
leave ``chainlit_app.py`` as a *thin* entry-point that only wires
:class:`ChainlitRenderer` + :func:`build_session` / :func:`swap_provider`
to ``@cl.on_message`` / ``@cl.on_chat_start`` decorators.

As a bonus, this makes everything here trivially unit-testable
without the Chainlit server: tests inject ``FakeMessage`` /
``FakeStep`` factories and assert on recorded ops.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from uk_property_agent.agent import PropertyAgent
from uk_property_agent.providers import (
    ProviderSpec,
    TaskKind,
    build_chat_model,
    resolve_provider,
)
from uk_property_agent.tools import ToolContext

# ---------------------------------------------------------------------------
# Factory protocols — fake-friendly so tests don't need the Chainlit runtime.
# ---------------------------------------------------------------------------


@runtime_checkable
class MessageLike(Protocol):
    """Minimal duck type for ``cl.Message``.

    Chainlit's real ``Message`` has a larger surface, but the renderer
    only uses these four operations, so the fake implementation in
    tests can stay tiny.
    """

    content: str

    async def send(self) -> Any: ...
    async def stream_token(self, text: str) -> Any: ...
    async def update(self) -> Any: ...


@runtime_checkable
class StepLike(Protocol):
    """Minimal duck type for ``cl.Step``.

    Chainlit steps are normally used as async context managers; we
    drive them manually because the agent's event stream is interleaved
    — tool A call, tool B call, tool A result, tool B result, narrative
    — so nested ``async with`` blocks would serialise them artificially.
    """

    name: str
    input: Any
    output: Any

    async def send(self) -> Any: ...
    async def update(self) -> Any: ...


MessageFactory = Callable[..., MessageLike]
StepFactory = Callable[..., StepLike]


# ---------------------------------------------------------------------------
# Renderer helpers
# ---------------------------------------------------------------------------


def _short_input(args: Any) -> Any:
    """Make tool args safely JSON-renderable for the Chainlit step panel.

    The Chainlit UI will best-effort render whatever we put in
    ``step.input``; wrapping with ``json.dumps`` keeps dict / list
    payloads readable and turns non-serialisable objects (rare but
    possible — eg. a ``pathlib.Path``) into their ``repr``.
    """

    if args is None:
        return None
    try:
        return json.dumps(args, default=str, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return repr(args)


def _short_output(content: str, limit: int = 4000) -> str:
    """Cap tool result text so huge JSON blobs don't crush the UI.

    Chainlit *can* render 50k-char blobs, but the step panel becomes
    unusable. 4k is enough to show the first page + 'truncated' marker
    while keeping the DOM responsive.
    """

    if len(content) <= limit:
        return content
    return content[:limit] + f"\n\n… [truncated {len(content) - limit} chars]"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


@dataclass
class ChainlitRenderer:
    """Translate ``PropertyAgent.astream_events`` into Chainlit UI ops.

    The renderer is a single-shot object per ``on_message`` call — the
    caller instantiates it, calls :meth:`render` with an event async
    iterator, and discards it. All state it tracks
    (step-by-tool-call-id, last narrative text) is local to one
    question/answer round; multi-turn history is owned by the
    LangGraph checkpointer, not this class.
    """

    message_factory: MessageFactory
    step_factory: StepFactory
    show_tool_events: bool = True

    _steps_by_id: dict[str, StepLike] = field(default_factory=dict)
    _orphan_steps_by_name: dict[str, list[StepLike]] = field(default_factory=dict)
    _answer: MessageLike | None = None
    _last_narrative: str = ""
    _streamed_any: bool = False

    async def render(self, events: AsyncIterator[dict[str, Any]]) -> str:
        """Consume ``events`` and return the final assistant text.

        We send the answer message *up front* (empty body) so
        subsequent ``stream_token`` calls have a target; otherwise
        Chainlit drops tokens that arrive before the first ``send``.
        Tool calls/results become sibling ``cl.Step`` entries
        (interleaved above the answer in the UI) and the final answer
        accumulates via ``stream_token``.
        """

        self._answer = self.message_factory(content="")
        await self._answer.send()

        final_text = ""
        async for event in events:
            etype = event.get("type")
            if etype == "tool_call":
                await self._on_tool_call(event)
            elif etype == "tool_result":
                await self._on_tool_result(event)
            elif etype == "narrative":
                await self._on_narrative(event.get("text") or "")
            elif etype == "final":
                candidate = event.get("text") or ""
                if candidate:
                    final_text = candidate

        # Fallback: the provider never streamed tokens (test fakes,
        # non-streaming models), but we do have a ``final`` event.
        # Emit the whole answer as one token so the message isn't empty.
        if not self._streamed_any and final_text:
            await self._answer.stream_token(final_text)
            self._last_narrative = final_text
            self._streamed_any = True

        await self._answer.update()
        return self._last_narrative or final_text

    async def _on_tool_call(self, event: dict[str, Any]) -> None:
        """Open a step for a tool call, indexed by ``tool_call_id``.

        We key by the LangChain-supplied tool_call id so the matching
        ``tool_result`` event (which carries the same id) can close
        the right step even when two tools run in parallel. If the id
        is absent (some providers drop it), we fall back to a
        name-keyed FIFO which is almost always correct because sibling
        tool calls are rarely duplicates.
        """

        if not self.show_tool_events:
            return
        name = str(event.get("name") or "tool")
        step = self.step_factory(name=name, type="tool")
        step.input = _short_input(event.get("args"))
        await step.send()
        call_id = event.get("id")
        if call_id:
            self._steps_by_id[str(call_id)] = step
        else:
            self._orphan_steps_by_name.setdefault(name, []).append(step)

    async def _on_tool_result(self, event: dict[str, Any]) -> None:
        """Close the step that matches this result's ``tool_call_id``.

        If no matching step exists — eg. the agent fired a tool in a
        code path that bypassed our ``tool_call`` event — we create a
        one-shot step so the result is still visible rather than
        silently swallowed.
        """

        if not self.show_tool_events:
            return
        content = _short_output(str(event.get("content") or ""))
        name = str(event.get("name") or "")
        call_id = event.get("id")
        step = self._pop_matching_step(call_id, name)
        if step is None:
            step = self.step_factory(name=name or "tool", type="tool")
            await step.send()
        step.output = content
        await step.update()

    def _pop_matching_step(self, call_id: Any, name: str) -> StepLike | None:
        """Match a tool_result back to its originating tool_call step."""

        if call_id and str(call_id) in self._steps_by_id:
            return self._steps_by_id.pop(str(call_id))
        if name and self._orphan_steps_by_name.get(name):
            bucket = self._orphan_steps_by_name[name]
            step = bucket.pop(0)
            if not bucket:
                self._orphan_steps_by_name.pop(name, None)
            return step
        return None

    async def _on_narrative(self, text: str) -> None:
        """Stream a narrative chunk into the answer message.

        The ``text != self._last_narrative`` guard mirrors the REPL
        path: it's only there so the fake test transport (which
        re-yields the whole answer as a single "narrative" event and
        then again as "final") doesn't print the same text twice.
        Real per-token streams always pass, since each chunk differs.
        """

        if not text or text == self._last_narrative:
            return
        assert self._answer is not None
        await self._answer.stream_token(text)
        self._last_narrative = text
        self._streamed_any = True


# ---------------------------------------------------------------------------
# Session-level helpers (independent of the chainlit runtime)
# ---------------------------------------------------------------------------


@dataclass
class ChainlitSession:
    """Per-tab state held by the Chainlit server between turns.

    Stored in ``cl.user_session`` under a single key so everything a
    turn needs (agent + thread_id + tool context) is fetched in one
    lookup.
    """

    agent: PropertyAgent
    spec: ProviderSpec
    thread_id: str
    tool_context: ToolContext | None
    checkpointer: Any


def _fresh_thread_id() -> str:
    """Short-ish unique id for a freshly-opened chat tab."""

    return f"web-{uuid.uuid4().hex[:12]}"


def build_session(
    *,
    provider: ProviderSpec | None = None,
    task: TaskKind | str = TaskKind.DEFAULT,
    temperature: float = 0.2,
    tool_context: ToolContext | None = None,
) -> ChainlitSession:
    """Assemble a brand-new :class:`ChainlitSession`.

    Returns a session with a freshly-minted :class:`InMemorySaver`
    checkpointer; callers don't need to care about any of the
    plumbing. Split out of the Chainlit decorators so unit tests can
    build a session directly and assert on the agent/thread wiring.
    """

    from langgraph.checkpoint.memory import InMemorySaver

    spec = provider or resolve_provider(task=task, temperature=temperature)
    model = build_chat_model(spec)
    ctx = tool_context or ToolContext.from_env()
    checkpointer = InMemorySaver()
    agent = PropertyAgent(
        model=model,
        tool_context=ctx,
        checkpointer=checkpointer,
    )
    return ChainlitSession(
        agent=agent,
        spec=spec,
        thread_id=_fresh_thread_id(),
        tool_context=ctx,
        checkpointer=checkpointer,
    )


def swap_provider(
    session: ChainlitSession,
    slug: str,
    *,
    task: TaskKind | str = TaskKind.DEFAULT,
    temperature: float | None = None,
) -> ChainlitSession:
    """Rebuild the agent in ``session`` against a different provider.

    The existing checkpointer is preserved, so memory is continuous
    across the swap — same trick the CLI REPL uses for ``/provider``.
    Returns a *new* session (immutability by replacement); the caller
    re-puts it into ``cl.user_session``.
    """

    new_spec = resolve_provider(
        task=task,
        explicit=slug,
        temperature=temperature if temperature is not None else session.spec.temperature,
    )
    model = build_chat_model(new_spec)
    new_agent = PropertyAgent(
        model=model,
        tool_context=session.tool_context,
        checkpointer=session.checkpointer,
    )
    return ChainlitSession(
        agent=new_agent,
        spec=new_spec,
        thread_id=session.thread_id,
        tool_context=session.tool_context,
        checkpointer=session.checkpointer,
    )


def reset_thread(session: ChainlitSession) -> ChainlitSession:
    """Return a copy of ``session`` pointing at a fresh thread_id.

    We reuse the same checkpointer (``InMemorySaver`` instance), provider
    spec, tool context, and agent — only the ``thread_id`` rotates. The
    old thread's state stays in the checkpointer but is effectively
    abandoned. Used by the Chainlit UI to self-heal when a provider
    rejects the replayed history for a corrupted thread (e.g. Gemini's
    ``INVALID_CHAT_HISTORY`` when a tool_call had no matching
    ToolMessage).
    """

    return ChainlitSession(
        agent=session.agent,
        spec=session.spec,
        thread_id=_fresh_thread_id(),
        tool_context=session.tool_context,
        checkpointer=session.checkpointer,
    )


def is_invalid_history_error(exc: BaseException) -> bool:
    """Heuristic: does this error mean "the thread's history is unreplayable"?

    Providers phrase this differently but the failure mode is identical:
    we have an ``AIMessage(tool_calls=...)`` with no matching
    ``ToolMessage``, or a tool_call with malformed args that the
    provider won't accept on replay. Matching on message text is
    deliberately loose — each provider uses slightly different wording
    and LangGraph wraps some of them in its own ``InvalidUpdateError``.
    """

    msg = str(exc)
    signatures = (
        "do not have a corresponding ToolMessage",
        "INVALID_CHAT_HISTORY",
        "tool_call_id",
        "function response parts",
    )
    return any(sig in msg for sig in signatures)


def session_banner(spec: ProviderSpec, tool_count: int) -> str:
    """Human-readable welcome banner for a new chat tab.

    Keeps the header tight (single paragraph + bullet list) rather
    than a tour — Chainlit users can see the starters right below.
    """

    return (
        f"### UK Property Intelligence Agent\n"
        f"Provider: **`{spec.slug}`** (temperature {spec.temperature}) · "
        f"{tool_count} tools bound.\n\n"
        "Ask about for-sale / to-let listings, sold prices (PPD), EPC, "
        "planning applications, flood risk, crime, commute isochrones, "
        "nearby amenities, or request a full **postcode dossier**. "
        "Your conversation is remembered per tab."
    )


__all__ = [
    "ChainlitRenderer",
    "ChainlitSession",
    "MessageFactory",
    "MessageLike",
    "StepFactory",
    "StepLike",
    "_short_input",
    "_short_output",
    "build_session",
    "is_invalid_history_error",
    "reset_thread",
    "session_banner",
    "swap_provider",
]
