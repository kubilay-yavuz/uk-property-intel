"""Chainlit entry-point for the UK Property agent web demo.

This module is **deliberately thin** and holds only:

* The ``@cl.on_chat_start`` / ``@cl.on_message`` / ``@cl.set_starters``
  handlers that the Chainlit server introspects when it loads this
  file.
* A :func:`module_path` helper the CLI uses to hand this file to
  ``chainlit run``.

All of the rendering logic, dataclasses, and session plumbing live in
:mod:`uk_property_agent.chainlit_render`. That split exists because
Chainlit loads this file with :func:`importlib.util.spec_from_file_location`
rather than the normal import machinery — the file's module object
never makes it into ``sys.modules``, and any ``@dataclass`` declared
here crashes on ``_process_class`` trying to look up
``sys.modules.get(cls.__module__)``.

Import paths
------------

Direct ``chainlit``::

    chainlit run uk_property_agent/chainlit_app.py

Via the CLI convenience wrapper::

    property-agent serve --host 127.0.0.1 --port 8000

Environment variables the CLI sets for the child process:

* ``PROPERTY_AGENT_WEB_PROVIDER`` — provider slug (``anthropic``,
  ``openai/gpt-4o``, etc.) for the initial session.
* ``PROPERTY_AGENT_WEB_TASK`` — one of the :class:`TaskKind` values.
* ``PROPERTY_AGENT_WEB_TEMPERATURE`` — float string.

Missing or invalid values fall back to safe defaults.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from uk_property_agent.chainlit_render import (
    ChainlitRenderer,
    ChainlitSession,
    build_session,
    is_invalid_history_error,
    reset_thread,
    session_banner,
)
from uk_property_agent.providers import TaskKind, resolve_provider
from uk_property_agent.tools import build_tools

SESSION_KEY = "property_agent_session"


def module_path() -> str:
    """Absolute path to this module — used by the serve CLI to shell out.

    ``chainlit run`` wants a file path (or a ``package:attribute``
    string). We return the path so the CLI can pass it verbatim, and
    so users who ``pip install uk-property-agent[web]`` don't have to
    remember where the file lives.
    """

    return __file__


def _register_chainlit_handlers() -> None:
    """Register the ``@cl.*`` decorators if Chainlit is importable.

    Kept in a helper so importing :mod:`uk_property_agent.chainlit_app`
    from a test (or from ``property-agent serve`` for path lookup)
    never fails with ``ModuleNotFoundError: chainlit``. When Chainlit
    *is* present (the ``[web]`` extra), this function is called at
    module import time by ``chainlit run``. When absent, we return
    cleanly and the module still provides :func:`module_path` and
    access to :class:`~.chainlit_render.ChainlitRenderer`.
    """

    try:
        import chainlit as cl  # type: ignore[import-not-found]
    except ImportError:
        return

    def _cl_message_factory(*, content: str = "") -> Any:
        return cl.Message(content=content)

    def _cl_step_factory(*, name: str, type: str) -> Any:
        return cl.Step(name=name, type=type)

    @cl.set_starters
    async def starters() -> list[Any]:
        """Starter prompts surfaced on the landing screen.

        Icons are served from our seeded ``public/`` directory
        (``~/.uk-property-agent/web/public/``) and reference the
        brass-amber SVGs shipped with this package. Chainlit turns
        the ``icon`` field into an ``<img>`` tag on the starter card.
        """

        return [
            cl.Starter(
                label="Cambridge family homes under £800k",
                message=(
                    "Pick the 3 best family homes for sale in Cambridge under "
                    "£800,000. Explain each pick using commute to London, "
                    "schools, and flood risk."
                ),
                icon="/public/icon_home.svg",
            ),
            cl.Starter(
                label="Postcode dossier: SW2 3RX",
                message="Build a full property dossier for SW2 3RX.",
                icon="/public/icon_dossier.svg",
            ),
            cl.Starter(
                label="Commute from N1 to Canary Wharf",
                message=(
                    "What's the public-transport commute time and "
                    "30-minute isochrone from N1 9FB to Canary Wharf?"
                ),
                icon="/public/icon_route.svg",
            ),
            cl.Starter(
                label="Sold prices on one street",
                message=(
                    "Show me the last 5 years of sold-price transactions on "
                    "Elizabeth Street, Victoria, London, and flag anything "
                    "unusual."
                ),
                icon="/public/icon_trend.svg",
            ),
        ]

    @cl.on_chat_start
    async def on_chat_start() -> None:
        """Spin up a fresh session with an InMemorySaver checkpointer."""

        provider_env = os.getenv("PROPERTY_AGENT_WEB_PROVIDER") or None
        task_env = os.getenv("PROPERTY_AGENT_WEB_TASK") or None
        temperature_env = os.getenv("PROPERTY_AGENT_WEB_TEMPERATURE")
        try:
            temperature = float(temperature_env) if temperature_env else 0.2
        except ValueError:
            temperature = 0.2
        spec = resolve_provider(
            task=TaskKind(task_env) if task_env else TaskKind.DEFAULT,
            explicit=provider_env,
            temperature=temperature,
        )
        session = build_session(provider=spec, temperature=temperature)
        cl.user_session.set(SESSION_KEY, session)
        await cl.Message(
            content=session_banner(
                session.spec,
                tool_count=len(build_tools(session.tool_context)),
            ),
            author="agent",
        ).send()

    @cl.on_message
    async def on_message(message: Any) -> None:
        """Per-turn handler — delegates to :class:`ChainlitRenderer`.

        If the provider rejects the replayed history as corrupted — the
        classic "AIMessage tool_call with no matching ToolMessage"
        failure mode — we rotate the thread id in-place, tell the user,
        and try again on the fresh thread so they don't have to hunt
        for the "New chat" button.
        """

        session: ChainlitSession | None = cl.user_session.get(SESSION_KEY)
        if session is None:
            # Ultra-defensive: cold-start a session if the chat_start
            # hook hasn't fired yet (can happen when Chainlit
            # rehydrates a reconnected tab on older versions).
            session = build_session()
            cl.user_session.set(SESSION_KEY, session)

        async def _run_turn(sess: ChainlitSession) -> None:
            renderer = ChainlitRenderer(
                message_factory=_cl_message_factory,
                step_factory=_cl_step_factory,
                show_tool_events=True,
            )

            async def _events() -> AsyncIterator[dict[str, Any]]:
                async for event in sess.agent.astream_events(
                    str(message.content), thread_id=sess.thread_id
                ):
                    yield event

            await renderer.render(_events())

        try:
            await _run_turn(session)
        except Exception as exc:
            if is_invalid_history_error(exc):
                healed = reset_thread(session)
                cl.user_session.set(SESSION_KEY, healed)
                await cl.Message(
                    content=(
                        "The previous turn left the thread in an invalid state "
                        "(an earlier tool call didn't complete). I've started "
                        "a fresh thread and retried your question — your "
                        "ongoing in-memory conversation is otherwise intact."
                    ),
                    author="agent",
                ).send()
                try:
                    await _run_turn(healed)
                except Exception as retry_exc:
                    await cl.Message(
                        content=(
                            f"**Error after thread reset:** "
                            f"`{type(retry_exc).__name__}` — {retry_exc}"
                        ),
                        author="agent",
                    ).send()
                return
            await cl.Message(
                content=f"**Error:** `{type(exc).__name__}` — {exc}",
                author="agent",
            ).send()


_register_chainlit_handlers()


__all__ = ["module_path"]
