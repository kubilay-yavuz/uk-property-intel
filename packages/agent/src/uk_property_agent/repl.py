"""Interactive terminal REPL for the UK Property agent.

The REPL layers three things on top of the existing single-shot
``property-agent ask`` surface:

1. **LangGraph memory.** An :class:`~langgraph.checkpoint.memory.InMemorySaver`
   is bound to the agent at construction time, so every line the user
   types is appended to a shared ``messages`` list keyed by a stable
   ``thread_id``. The LLM sees the full prior turn + tool output on every
   subsequent turn — no manual history stitching by the caller.

2. **Slash commands.** Everything that's not a property question is a
   REPL verb: ``/help`` lists verbs, ``/clear`` rotates to a new thread
   id (effectively wiping memory), ``/provider openai/gpt-4o`` rebuilds
   the agent against a different model, ``/tools`` lists the 17 bound
   tools, ``/stream on|off`` toggles the tool-call rendering, ``/exit``
   leaves.

3. **Streaming rendering.** When ``stream`` is on (the default), tool
   calls + tool results appear inline in the same stream as narrative
   tokens, so the user can watch the agent plan. Tool events are
   emitted via ``on_tool_event`` / narrative tokens via ``on_narrative``
   — callers embedding this loop in a different frontend (Gradio,
   tests) replace those callbacks with their own renderers.

The REPL is intentionally implemented as pure async functions plus a
small :class:`ChatLoop` state holder, not a heavy ``cmd.Cmd`` subclass,
so it can be exercised headlessly in unit tests by feeding a synthetic
``inputs`` iterator and asserting on the captured ``outputs`` list.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from uk_property_agent.agent import PropertyAgent
from uk_property_agent.providers import (
    ProviderSpec,
    TaskKind,
    build_chat_model,
    resolve_provider,
)
from uk_property_agent.tools import ToolContext

InputFn = Callable[[str], Awaitable[str | None]]
"""Async prompt callable. Returns ``None`` to signal EOF / ``Ctrl-D``."""

OutputFn = Callable[[str], None]
"""Synchronous write-line callable — defaults to ``print`` bound to stdout."""


def _new_thread_id() -> str:
    """Short, reasonably-unique thread id for a fresh conversation.

    The LangGraph checkpointer indexes by this string, so collisions
    would re-hydrate the wrong history; uuid4 is well under a trillionth
    of a percent chance of collision across a session.
    """

    return f"repl-{uuid.uuid4().hex[:12]}"


@dataclass
class ChatLoopOptions:
    """Knobs the REPL exposes as slash commands.

    Stored as a mutable dataclass so slash handlers can flip fields in
    place without reconstructing the whole loop. ``stream`` drives tool
    rendering, ``temperature`` is the default for newly-built agents
    after a ``/provider`` swap.
    """

    stream: bool = True
    temperature: float = 0.2
    task: TaskKind = TaskKind.DEFAULT


@dataclass
class ChatLoop:
    """State holder + transition methods for a single REPL session.

    Public methods (``run``, ``handle_slash``, ``ask``) are async so
    the loop can cooperate with LangGraph's async streaming APIs, and
    so tests can await it directly without spinning up an event loop
    manually.
    """

    agent: PropertyAgent
    spec: ProviderSpec
    options: ChatLoopOptions = field(default_factory=ChatLoopOptions)
    thread_id: str = field(default_factory=_new_thread_id)
    tool_context: ToolContext | None = None
    input_fn: InputFn | None = None
    output_fn: OutputFn = field(default=lambda line: print(line, flush=True))
    error_fn: OutputFn = field(default=lambda line: print(line, file=sys.stderr, flush=True))

    async def run(self) -> int:
        """Main loop. Returns a shell-style exit code.

        The returned code is ``0`` on a clean exit (``/exit``, EOF, or
        ``KeyboardInterrupt``) and only non-zero if something catastrophic
        bubbles out of the graph — fine-grained tool or LLM errors are
        rendered inline and don't terminate the session.
        """

        self._print_banner()
        prompt = self._prompt_for(self.input_fn)
        while True:
            try:
                line = await prompt("property> ")
            except (EOFError, KeyboardInterrupt):
                self.output_fn("")
                return 0
            if line is None:
                self.output_fn("")
                return 0
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("/"):
                verdict = await self.handle_slash(stripped)
                if verdict == "exit":
                    return 0
                continue
            await self.ask(stripped)

    async def ask(self, question: str) -> str:
        """Send ``question`` through the agent and render the result.

        Returns the final narrative text so callers / tests can assert
        on it directly; the same text is also written to ``output_fn``.
        """

        if self.options.stream:
            return await self._ask_streaming(question)
        answer = await self.agent.ainvoke(question, thread_id=self.thread_id)
        self.output_fn(answer.rstrip())
        return answer

    async def handle_slash(self, line: str) -> str:
        """Dispatch a ``/verb ...`` line. Returns ``"exit"`` to break."""

        parts = line.split()
        verb = parts[0].lower()
        rest = parts[1:]
        if verb in {"/exit", "/quit", "/q"}:
            return "exit"
        if verb in {"/help", "/h", "/?"}:
            self._print_help()
            return "continue"
        if verb in {"/clear", "/new", "/reset"}:
            self.thread_id = _new_thread_id()
            self.output_fn(f"[new thread: {self.thread_id}]")
            return "continue"
        if verb == "/tools":
            self._print_tools()
            return "continue"
        if verb == "/provider":
            if not rest:
                self.output_fn(f"[provider] {self.spec.slug} (temperature={self.spec.temperature})")
                return "continue"
            await self._swap_provider(rest[0])
            return "continue"
        if verb == "/stream":
            if not rest or rest[0].lower() in {"on", "true", "1"}:
                self.options.stream = True
            elif rest[0].lower() in {"off", "false", "0"}:
                self.options.stream = False
            else:
                self.error_fn(f"[err] /stream expects on|off, got {rest[0]!r}")
                return "continue"
            self.output_fn(f"[stream] {'on' if self.options.stream else 'off'}")
            return "continue"
        if verb == "/thread":
            self.output_fn(f"[thread] {self.thread_id}")
            return "continue"
        self.error_fn(f"[err] unknown verb: {verb}. Try /help.")
        return "continue"

    def _prompt_for(self, custom: InputFn | None) -> InputFn:
        """Return the prompt callable; default is non-blocking ``input``.

        The default must be async because ``run`` awaits it; we wrap
        the stdlib ``input`` in a tiny coroutine rather than pulling in
        ``aioconsole``, which would make cold-start noticeably slower
        and add a transitive dep for a ~4-line wrapper.
        """

        if custom is not None:
            return custom

        async def _default_prompt(prompt: str) -> str | None:
            try:
                return input(prompt)
            except EOFError:
                return None

        return _default_prompt

    async def _ask_streaming(self, question: str) -> str:
        """Drive the structured event stream and render it in-line.

        Tool calls and tool results render to ``error_fn`` (stderr by
        default) so they remain visually separable from the
        narrative, which is streamed delta-by-delta to stdout. The
        ``text != last_narrative`` guard mirrors ``cli._run_ask_streaming``
        so the test-fake path (which yields the whole final answer as a
        single "narrative" event that's later echoed as the "final"
        event) doesn't print the same text twice.
        """

        printed = False
        last_narrative = ""
        final_text = ""
        try:
            async for event in self.agent.astream_events(
                question, thread_id=self.thread_id
            ):
                etype = event.get("type")
                if etype == "tool_call":
                    args = event.get("args")
                    self.error_fn(f"  [tool_call] {event.get('name')}({args})")
                elif etype == "tool_result":
                    short = str(event.get("content") or "")[:240].replace("\n", " ")
                    name = event.get("name") or "?"
                    self.error_fn(f"  [tool_result] {name}: {short}")
                elif etype == "narrative":
                    text = event.get("text") or ""
                    if text and text != last_narrative:
                        sys.stdout.write(text)
                        sys.stdout.flush()
                        printed = True
                        last_narrative = text
                        final_text = text if not final_text else final_text + text
                elif etype == "final":
                    candidate = event.get("text") or ""
                    if candidate:
                        final_text = candidate
        except Exception as exc:  # pragma: no cover - surfaces to the caller
            self.error_fn(f"  [error] {type(exc).__name__}: {exc}")
            return ""

        if printed:
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif final_text:
            self.output_fn(final_text.rstrip())
        return final_text

    async def _swap_provider(self, slug: str) -> None:
        """Rebuild ``self.agent`` against the named provider/model.

        We preserve the existing checkpointer, tool context, and
        streaming toggle so the conversation continues seamlessly
        against the new model. A failure to resolve or build the new
        model surfaces as an error line without mutating state.
        """

        try:
            new_spec = resolve_provider(
                task=self.options.task,
                explicit=slug,
                temperature=self.options.temperature,
            )
            new_model = build_chat_model(new_spec)
        except (RuntimeError, ImportError) as exc:
            self.error_fn(f"[err] /provider {slug}: {exc}")
            return

        self.spec = new_spec
        self.agent = PropertyAgent(
            model=new_model,
            tool_context=self.tool_context,
            checkpointer=self.agent.checkpointer,
        )
        self.output_fn(f"[provider] {new_spec.slug} (temperature={new_spec.temperature})")

    def _print_banner(self) -> None:
        self.output_fn(
            f"UK Property Agent REPL — {self.spec.slug} "
            f"(temperature={self.spec.temperature})"
        )
        self.output_fn(f"Thread: {self.thread_id}. Type /help for commands.")

    def _print_help(self) -> None:
        self.output_fn(
            "\n".join(
                [
                    "Commands:",
                    "  /help              Show this help.",
                    "  /exit, /quit, /q   Leave the REPL.",
                    "  /clear, /new       Start a new conversation thread.",
                    "  /thread            Show the current thread id.",
                    "  /tools             List the configured agent tools.",
                    "  /provider          Show the current provider/model.",
                    "  /provider SLUG     Swap to a new provider (e.g. openai/gpt-4o).",
                    "  /stream on|off     Toggle inline tool-call rendering.",
                ]
            )
        )

    def _print_tools(self) -> None:
        tools = self.agent.tools()
        self.output_fn(f"Configured tools ({len(tools)}):")
        for tool in tools:
            first_line = (tool.description or "").strip().splitlines()[0]
            self.output_fn(f"  - {tool.name}: {first_line}")


def build_chat_loop(
    spec: ProviderSpec,
    *,
    tool_context: ToolContext | None = None,
    checkpointer: Any | None = None,
    options: ChatLoopOptions | None = None,
    input_fn: InputFn | None = None,
    output_fn: OutputFn | None = None,
    error_fn: OutputFn | None = None,
) -> ChatLoop:
    """Wire up a :class:`ChatLoop` ready to ``await .run()``.

    Callers that want to reuse an existing agent (tests, web UI) can
    build the ``PropertyAgent`` themselves and instantiate
    :class:`ChatLoop` directly. The helper is here to keep the CLI
    entry point short.
    """

    from langgraph.checkpoint.memory import InMemorySaver

    if checkpointer is None:
        checkpointer = InMemorySaver()
    model = build_chat_model(spec)
    agent = PropertyAgent(
        model=model,
        tool_context=tool_context,
        checkpointer=checkpointer,
    )
    kwargs: dict[str, Any] = {
        "agent": agent,
        "spec": spec,
        "options": options or ChatLoopOptions(),
        "tool_context": tool_context,
    }
    if input_fn is not None:
        kwargs["input_fn"] = input_fn
    if output_fn is not None:
        kwargs["output_fn"] = output_fn
    if error_fn is not None:
        kwargs["error_fn"] = error_fn
    return ChatLoop(**kwargs)


async def replay_scripted(
    loop: ChatLoop, lines: Iterable[str]
) -> AsyncIterator[str]:
    """Test helper: feed ``lines`` to ``loop``, yield the final answer of each.

    Slash commands are routed through ``loop.handle_slash`` (yielding
    an empty string so test assertions stay lined up); plain prompts
    are run through ``loop.ask`` (yielding the final narrative).
    """

    for line in lines:
        stripped = line.strip()
        if not stripped:
            yield ""
            continue
        if stripped.startswith("/"):
            await loop.handle_slash(stripped)
            yield ""
            continue
        answer = await loop.ask(stripped)
        yield answer


__all__ = [
    "ChatLoop",
    "ChatLoopOptions",
    "InputFn",
    "OutputFn",
    "build_chat_loop",
    "replay_scripted",
]
