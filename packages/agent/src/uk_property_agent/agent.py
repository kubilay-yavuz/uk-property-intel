"""High-level ``PropertyAgent`` wrapping a LangGraph ReAct agent.

The agent wires a chat model + UK property tools into a graph that loops:
``llm -> tool_node -> llm -> ...`` until the model returns a final message.

Agent v2 (2026-Q2) adds:

* Isochrone tools (``drive_time_isochrone`` / ``transit_isochrone``) that
  delegate to the hosted ``uk-location-intel`` actor when Apify creds are
  present and fall back to a locally-configured OSRM/OTP transport.
* A ``build_property_dossier`` orchestrator that returns a typed
  :class:`~uk_property_agent.dossier.PropertyDossier` in one tool call.
* Provider-aware prompt caching on the system prompt — Anthropic gets
  content-blocks with ``cache_control``, OpenAI/Gemini get the plain
  string their SDKs expect (both cache eligible prefixes automatically).

Agent v3 (2026-Q2) extends this with multi-provider routing: the same
:class:`PropertyAgent` can now sit on top of Anthropic, OpenAI, or
Google Gemini, with optional per-task model pinning via the
:func:`uk_property_agent.providers.resolve_provider` helper.

Agent v4 (2026-04-19) adds multi-turn memory. Pass
``checkpointer=InMemorySaver()`` (or any LangGraph-compatible checkpointer)
plus a stable ``thread_id`` on each ``ainvoke`` / ``astream_*`` call and
LangGraph persists the full ``messages`` list between turns, so a REPL,
chat UI, or long-running MCP session can hold a coherent conversation
without the caller rebuilding the prompt each turn. Every invocation
method now accepts ``thread_id``; if no checkpointer is configured the
argument is silently ignored, keeping the old single-shot semantics
intact for callers that don't care about memory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from uk_property_agent.prompts import build_system_message
from uk_property_agent.providers import (
    Provider,
    ProviderSpec,
    TaskKind,
    build_chat_model,
    resolve_provider,
)
from uk_property_agent.tools import ToolContext, _format_tool_error, build_tools


class PropertyAgent:
    """Natural-language UK property analyst.

    Parameters
    ----------
    model:
        A pre-built ``BaseChatModel`` with tool-calling. Mutually
        exclusive with the ``provider``/``task`` keyword arguments —
        supply one or the other. When omitted, the agent resolves a
        model via :func:`uk_property_agent.providers.resolve_provider`.
    provider:
        Optional explicit :class:`~uk_property_agent.providers.Provider`,
        :class:`~uk_property_agent.providers.ProviderSpec`, or
        ``"provider/model"`` slug. Overrides ``task`` when set.
    task:
        Optional :class:`~uk_property_agent.providers.TaskKind` (or its
        string slug) used for per-task model routing; honours
        ``AGENT_MODEL_<TASK>`` env overrides. Default is
        :attr:`TaskKind.DEFAULT`.
    tool_context:
        Optional :class:`ToolContext` to inject custom factories. Defaults to
        production factories built from environment variables.
    system_prompt:
        Override the default system prompt. Passing ``None`` uses the
        baked-in ``CACHEABLE_SYSTEM_PROMPT``.
    system_message:
        Full override for the system message, takes precedence over
        ``system_prompt`` and ``enable_prompt_cache``. Use this when you
        need custom caching / content-block shapes the builder doesn't
        support.
    enable_prompt_cache:
        When ``True`` (default) the system prompt is shaped per
        provider: Anthropic gets a content-block tagged
        ``cache_control={"type": "ephemeral"}``; OpenAI and Gemini get
        a plain string (both providers cache long prefixes
        automatically). Disable for fake / offline models that don't
        understand either shape.
    cache_ttl:
        ``"5m"`` (default) or ``"1h"``. Only applied to Anthropic
        content-blocks; ignored for OpenAI/Gemini.
    checkpointer:
        Optional LangGraph checkpointer (e.g.
        ``langgraph.checkpoint.memory.InMemorySaver()``) used to persist
        the conversation ``messages`` list across ``ainvoke`` /
        ``astream_*`` calls that share the same ``thread_id``. When
        ``None`` (default) the graph runs without memory and every
        invocation starts from a clean slate — preserves the historical
        single-shot behaviour used by the ``property-agent ask`` CLI
        and by the MCP servers' per-call tools. Pass an ``InMemorySaver``
        to power the REPL / Gradio UI / multi-turn MCP ``ask``.
    """

    def __init__(
        self,
        *,
        model: BaseChatModel | None = None,
        provider: Provider | ProviderSpec | str | None = None,
        task: TaskKind | str = TaskKind.DEFAULT,
        tool_context: ToolContext | None = None,
        system_prompt: str | None = None,
        system_message: SystemMessage | None = None,
        enable_prompt_cache: bool = True,
        cache_ttl: Literal["5m", "1h"] = "5m",
        checkpointer: Any | None = None,
    ) -> None:
        from langgraph.prebuilt import ToolNode, create_react_agent

        if model is not None and provider is not None:
            raise ValueError(
                "Pass either an explicit 'model' or a 'provider'/'task' pair, not both."
            )

        if model is not None:
            self._model = model
            self._provider_spec: ProviderSpec | None = None
        else:
            self._provider_spec = resolve_provider(task=task, explicit=provider)
            self._model = build_chat_model(self._provider_spec)

        cache_provider = (
            self._provider_spec.provider if self._provider_spec else Provider.ANTHROPIC
        )

        self._tools = build_tools(tool_context)
        if system_message is not None:
            self._system_message = system_message
        else:
            self._system_message = build_system_message(
                system_prompt,
                enable_cache=enable_prompt_cache,
                ttl=cache_ttl,
                provider=cache_provider,
            )
        self._checkpointer = checkpointer
        # Build a ToolNode with a custom error handler so ANY exception
        # raised inside a tool becomes a ToolMessage the LLM can read —
        # not an orphan tool_call that corrupts the checkpointed state.
        # LangGraph's default only swallows ``ToolInvocationError``; we
        # want HTTP 403/404/timeouts from the data-layer clients to fall
        # through to the model as well (Zoopla blocks, postcodes.io 404
        # on fabricated inputs, etc.).
        self._tool_node = ToolNode(self._tools, handle_tool_errors=_format_tool_error)
        graph_kwargs: dict[str, Any] = {
            "model": self._model,
            "tools": self._tool_node,
            "prompt": self._system_message,
        }
        if checkpointer is not None:
            graph_kwargs["checkpointer"] = checkpointer
        self._graph = create_react_agent(**graph_kwargs)

    @property
    def provider_spec(self) -> ProviderSpec | None:
        """The :class:`ProviderSpec` that built this agent's model, if any.

        ``None`` when the caller supplied a pre-built ``model`` — we
        don't fabricate provenance for externally-constructed models.
        """

        return self._provider_spec

    @property
    def graph(self) -> Any:
        """The compiled LangGraph. Exposed for LangGraph dev tools."""
        return self._graph

    @property
    def checkpointer(self) -> Any | None:
        """The LangGraph checkpointer bound to the graph, if any.

        Returned verbatim so consumers (the REPL / web UI) can peek at
        persisted thread state for debugging — e.g. dump
        ``checkpointer.list(config)`` to inspect history.
        """

        return self._checkpointer

    @property
    def system_message(self) -> SystemMessage:
        """The :class:`SystemMessage` bound into the graph.

        For prompt-caching-aware callers this is the way to inspect
        whether the attached content block carries ``cache_control``.
        """
        return self._system_message

    def tools(self) -> list[Any]:
        """Return the list of tools bound to this agent."""
        return list(self._tools)

    def _config_for(self, thread_id: str | None) -> dict[str, Any] | None:
        """Build the LangGraph ``config`` dict for a given thread.

        Returns ``None`` when neither a thread is requested nor a
        checkpointer is bound, so single-shot callers never pay the
        dict-construction cost. When a checkpointer *is* bound we
        always emit a config (with a fallback thread id) so the graph
        has somewhere to write.
        """

        if thread_id is None and self._checkpointer is None:
            return None
        resolved = thread_id or "default"
        return {"configurable": {"thread_id": resolved}}

    async def ainvoke(self, question: str, *, thread_id: str | None = None) -> str:
        """Run the agent end-to-end and return the final answer string.

        ``thread_id`` is only honoured when a checkpointer was passed
        to :class:`PropertyAgent`; without one the state is discarded
        at the end of the call regardless of the id.
        """
        config = self._config_for(thread_id)
        final = await self._graph.ainvoke(self._initial_state(question), config=config)
        return self._last_ai_text(final.get("messages", []))

    async def astream(
        self, question: str, *, thread_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield LangGraph node-level event chunks (``stream_mode='updates'``).

        Use this when you want to observe the agent's planning —
        intermediate tool calls and tool results — rather than the
        final narrative token stream. For the latter, use
        :meth:`astream_narrative`.
        """

        config = self._config_for(thread_id)
        async for chunk in self._graph.astream(
            self._initial_state(question),
            config=config,
        ):
            yield chunk

    async def astream_narrative(
        self, question: str, *, thread_id: str | None = None
    ) -> AsyncIterator[str]:
        """Yield narrative text as the final assistant message is generated.

        Uses LangGraph's ``stream_mode='messages'`` to get per-token
        ``AIMessageChunk`` objects from the underlying model, then
        filters to (a) chunks emitted by the final answer call and
        (b) the text portions of those chunks, so tool-call JSON never
        leaks into the stream.

        The last-AIMessage fallback keeps things robust when the
        underlying model doesn't implement ``_astream`` (eg. test
        fakes): we still yield the final message's text in one chunk.
        """

        config = self._config_for(thread_id)
        last_final_text: str = ""
        saw_any_token = False
        async for message_chunk, metadata in self._graph.astream(
            self._initial_state(question),
            stream_mode="messages",
            config=config,
        ):
            if not self._is_final_answer_chunk(message_chunk, metadata):
                continue
            text = self._extract_text_content(message_chunk)
            if not text:
                continue
            saw_any_token = True
            last_final_text = text
            yield text

        if not saw_any_token:
            final = await self._graph.ainvoke(
                self._initial_state(question), config=config
            )
            text = self._last_ai_text(final.get("messages", []))
            if text and text != last_final_text:
                yield text

    async def astream_events(
        self, question: str, *, thread_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield structured events mixing tool calls, tool results, and narrative tokens.

        Events shape:

        * ``{"type": "tool_call", "name": str, "args": dict, "id": str|None}``
        * ``{"type": "tool_result", "name": str|None, "content": str, "id": str|None}``
        * ``{"type": "narrative", "text": str}`` — per-token when the
          model streams, per-message otherwise.
        * ``{"type": "final", "text": str}`` — final answer once the
          graph settles, so consumers that skipped earlier tokens
          still get the full answer.

        This is the richest observation surface for UIs and the CLI
        ``--stream`` flag. Under the hood it pipes
        ``stream_mode='messages'`` through the same filter as
        :meth:`astream_narrative` while emitting tool-call and
        tool-result events off the raw chunks.
        """

        config = self._config_for(thread_id)
        final_text = ""
        async for message_chunk, metadata in self._graph.astream(
            self._initial_state(question),
            stream_mode="messages",
            config=config,
        ):
            cls_name = type(message_chunk).__name__
            tool_calls = getattr(message_chunk, "tool_calls", None) or []
            for tc in tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else None
                if not name:
                    continue
                yield {
                    "type": "tool_call",
                    "name": name,
                    "args": tc.get("args") if isinstance(tc, dict) else None,
                    "id": tc.get("id") if isinstance(tc, dict) else None,
                }
            if cls_name.startswith("ToolMessage"):
                content = self._extract_text_content(message_chunk)
                yield {
                    "type": "tool_result",
                    "name": getattr(message_chunk, "name", None),
                    "content": content,
                    "id": getattr(message_chunk, "tool_call_id", None),
                }
                continue
            if self._is_final_answer_chunk(message_chunk, metadata):
                text = self._extract_text_content(message_chunk)
                if text:
                    final_text = text
                    yield {"type": "narrative", "text": text}

        if final_text:
            yield {"type": "final", "text": final_text}
            return

        final = await self._graph.ainvoke(
            self._initial_state(question), config=config
        )
        text = self._last_ai_text(final.get("messages", []))
        if text:
            yield {"type": "narrative", "text": text}
            yield {"type": "final", "text": text}

    def _initial_state(self, question: str) -> dict[str, Any]:
        return {"messages": [HumanMessage(content=question)]}

    @staticmethod
    def _last_ai_text(messages: list[BaseMessage]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return PropertyAgent._extract_text_content(msg)
        return ""

    @staticmethod
    def _extract_text_content(message: Any) -> str:
        """Normalise ``AIMessage(Chunk)`` / ``ToolMessage(Chunk)`` content to text.

        Anthropic streams text as ``[{"type": "text", "text": "..."}]``
        content blocks (interleaved with ``tool_use`` blocks we want
        to skip); OpenAI/Gemini stream as plain strings. The fallback
        ignores exotic block types to keep the narrative stream clean.
        """

        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        parts.append(str(part.get("text", "")))
                elif isinstance(part, str):
                    parts.append(part)
            return "".join(parts)
        return ""

    @staticmethod
    def _is_final_answer_chunk(message_chunk: Any, metadata: Any) -> bool:
        """True when the chunk is a narrative :class:`AIMessage` fragment.

        Drops:

        * tool-call chunks (``tool_calls`` populated or the chunk only
          carries a ``tool_use`` content block),
        * non-AI messages (``ToolMessage``, ``HumanMessage``,
          ``SystemMessage``).

        ``metadata`` is the second item from ``stream_mode='messages'``;
        we don't currently branch on ``langgraph_node`` because the
        tool-call guard already covers mid-graph LLM turns (each
        planning turn carries ``tool_calls`` and is filtered out).
        """

        del metadata
        cls_name = type(message_chunk).__name__
        if not cls_name.startswith("AIMessage"):
            return False
        if getattr(message_chunk, "tool_calls", None):
            return False
        return not getattr(message_chunk, "tool_call_chunks", None)


__all__ = ["PropertyAgent", "ToolContext"]
