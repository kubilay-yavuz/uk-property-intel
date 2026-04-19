"""Command-line entry point for the UK Property agent.

Usage
-----
.. code-block:: shell

    property-agent ask "Best 3 family homes for sale in Cambridge under £800k?"
    property-agent ask --stream "..."
    property-agent ask --provider openai --model gpt-4o "..."
    property-agent ask --provider gemini/gemini-2.5-pro "..."
    property-agent chat                  # interactive REPL with memory (Agent v4)
    property-agent chat --no-stream      # single-turn outputs, still remembered
    property-agent serve                 # launch the Chainlit web UI (Agent v4)
    property-agent serve --port 7861 --host 0.0.0.0
    property-agent tools                 # list configured tools
    property-agent env                   # detected env / credentials per provider

``ask`` runs the graph to completion and prints the final narrative to
stdout.

``--stream`` (Agent v3) splits the transport: intermediate tool calls and
tool results are emitted to **stderr**, and the final narrative streams
to **stdout** token-by-token as the model generates it (falling back to
a single chunk when the underlying provider doesn't implement token
streaming). Pipe stdout to a UI or log collector; redirect stderr to
``/dev/null`` if you only want the answer.

``chat`` (Agent v4) drops into an interactive REPL backed by a LangGraph
``InMemorySaver`` checkpointer, so every line the user types is appended
to a persistent ``messages`` list keyed by a thread id. Slash commands
(``/help``, ``/clear``, ``/provider openai/gpt-4o``, ``/tools``,
``/stream on|off``) mirror the ``ask`` flags for in-session
customisation. See :mod:`uk_property_agent.repl`.

``serve`` (Agent v4) launches a Chainlit web UI with the same agent —
per-session thread memory, streamed answers, tool calls rendered as
collapsible step panels. Under the hood this shells out to
``chainlit run <path>``; the provider/temperature/task flags are
passed through as environment variables read by
:mod:`uk_property_agent.chainlit_app`. Requires
``uk-property-agent[web]`` (which pulls in ``chainlit``). This surface
is intended for local demos — there's no built-in auth, rate limiting,
or persistence beyond the in-memory checkpointer.

Model selection routes through
:func:`uk_property_agent.providers.resolve_provider`, so any provider whose
credentials are present (Anthropic / OpenAI / Gemini) will be used
automatically; use ``--provider`` or ``AGENT_PROVIDER`` to pin explicitly.

LangSmith tracing integrates automatically: set ``LANGSMITH_API_KEY``
(plus optional ``LANGSMITH_PROJECT`` and ``LANGSMITH_TRACING=true``) and
every graph run in this CLI — ``ask``, ``chat``, or ``serve`` — streams
traces to your LangSmith project for free observability. ``env`` reports
whether the vars are set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from uk_property_agent.agent import PropertyAgent
from uk_property_agent.providers import (
    ProviderSpec,
    TaskKind,
    build_chat_model,
    describe_env,
    resolve_provider,
)
from uk_property_agent.tools import ToolContext, build_tools


def _resolve_spec(args: argparse.Namespace) -> ProviderSpec:
    """Walk CLI flags through :func:`resolve_provider`.

    ``--provider`` may be a bare slug (``openai``) or a
    provider/model pair (``openai/gpt-4o``). ``--model`` overrides the
    model half only; if both are supplied together we stitch them.
    """

    explicit: str | None
    if args.provider and args.model:
        if "/" in args.provider:
            explicit = f"{args.provider.split('/', 1)[0]}/{args.model}"
        else:
            explicit = f"{args.provider}/{args.model}"
    else:
        explicit = args.provider or args.model or None

    task = TaskKind(args.task) if args.task else TaskKind.DEFAULT
    return resolve_provider(
        task=task,
        explicit=explicit,
        temperature=args.temperature,
    )


def _build_model(args: argparse.Namespace) -> Any:
    """Build the chat model requested by the CLI, with friendly errors."""

    try:
        spec = _resolve_spec(args)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        return spec, build_chat_model(spec)
    except ImportError as exc:
        raise SystemExit(str(exc)) from exc


async def _run_ask(args: argparse.Namespace) -> int:
    spec, model = _build_model(args)
    ctx = ToolContext.from_env()
    agent = PropertyAgent(model=model, tool_context=ctx)

    if args.show_provider:
        print(f"[provider] {spec.slug} (temperature={spec.temperature})", file=sys.stderr)

    if args.stream:
        return await _run_ask_streaming(agent, args.question)

    final = await agent.ainvoke(args.question)
    print(final)
    return 0


async def _run_ask_streaming(agent: PropertyAgent, question: str) -> int:
    """Stream structured events: tool calls to stderr, narrative to stdout.

    The narrative is emitted to stdout as the model generates it (or
    at ``final`` time when the underlying transport can't stream
    tokens, eg. test fakes), so ``property-agent ask --stream`` can
    be piped into a UI or long-running log. Tool calls and tool
    results go to stderr so the primary stream stays clean.
    """

    printed_any_token = False
    last_narrative = ""
    final_text = ""
    async for event in agent.astream_events(question):
        etype = event.get("type")
        if etype == "tool_call":
            args_part = event.get("args")
            print(
                f"[tool_call] {event.get('name')}({args_part})",
                file=sys.stderr,
                flush=True,
            )
        elif etype == "tool_result":
            short = str(event.get("content") or "")[:280].replace("\n", " ")
            name = event.get("name") or "?"
            print(f"[tool_result] {name}: {short}", file=sys.stderr, flush=True)
        elif etype == "narrative":
            text = event.get("text") or ""
            if text and text != last_narrative:
                sys.stdout.write(text)
                sys.stdout.flush()
                printed_any_token = True
                last_narrative = text
        elif etype == "final":
            final_text = event.get("text") or ""

    if not printed_any_token and final_text:
        sys.stdout.write(final_text)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


def _run_tools(_: argparse.Namespace) -> int:
    ctx = ToolContext.from_env()
    tools = build_tools(ctx)
    print(f"Configured tools ({len(tools)}):")
    for tool in tools:
        print(f"  - {tool.name}: {tool.description.strip().splitlines()[0]}")
    return 0


async def _run_chat(args: argparse.Namespace) -> int:
    """Interactive REPL with LangGraph memory + slash commands.

    Wires :class:`~uk_property_agent.repl.ChatLoop` to stdin/stdout and
    awaits it. On EOF or ``/exit`` we return cleanly with exit code 0;
    a provider-resolution failure surfaces as a SystemExit with a
    one-line explanation (same as ``ask``).
    """

    from uk_property_agent.repl import ChatLoopOptions, build_chat_loop

    try:
        spec = _resolve_spec(args)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    options = ChatLoopOptions(
        stream=not args.no_stream,
        temperature=args.temperature,
        task=TaskKind(args.task) if args.task else TaskKind.DEFAULT,
    )
    tool_context = ToolContext.from_env()
    try:
        loop = build_chat_loop(
            spec,
            tool_context=tool_context,
            options=options,
        )
    except ImportError as exc:
        raise SystemExit(str(exc)) from exc
    return await loop.run()


def _run_serve(args: argparse.Namespace) -> int:
    """Launch the Chainlit web UI by execing ``chainlit run``.

    Chainlit owns its own ASGI server (``uvicorn`` + websocket) and is
    not designed to be embedded programmatically, so the cleanest
    integration is to locate ``chainlit_app.py`` inside the installed
    package and hand it to the ``chainlit`` CLI.

    We also seed a writable runtime directory under
    ``~/.uk-property-agent/web/`` with our bundled ``.chainlit/config.toml``
    (for branding — app name, dark theme) and ``chainlit.md`` (landing
    copy). Chainlit resolves those files from its current working
    directory; by running the child process with ``cwd`` pointed at
    the seeded directory we get a themed UI out of the box and the
    user doesn't end up with ``chainlit.md`` / ``.chainlit/`` junk
    files strewn wherever they ran the CLI from. Users can edit the
    seeded files to customise the theme; we only copy on first run or
    if the files are missing.

    Provider / task / temperature flags flow through to the child
    process as ``PROPERTY_AGENT_WEB_*`` env vars which
    :func:`~uk_property_agent.chainlit_render.build_session` reads
    inside the ``@cl.on_chat_start`` handler. That keeps the Chainlit
    module free of argparse coupling.
    """

    try:
        from uk_property_agent.chainlit_app import module_path
    except ImportError as exc:  # pragma: no cover - chainlit missing path
        raise SystemExit(
            "chainlit is required for `property-agent serve`. Install with "
            "`uv pip install 'uk-property-agent[web]'`."
        ) from exc

    try:
        spec = _resolve_spec(args)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    import os
    import shutil
    import subprocess

    chainlit_exe = shutil.which("chainlit")
    if chainlit_exe is None:  # pragma: no cover - requires chainlit absent
        raise SystemExit(
            "Found `uk_property_agent.chainlit_app` but `chainlit` CLI is "
            "not on PATH. Install the extra: "
            "`uv pip install 'uk-property-agent[web]'`."
        )

    runtime_dir = _prepare_chainlit_runtime_dir()

    env = os.environ.copy()
    env["PROPERTY_AGENT_WEB_PROVIDER"] = spec.slug
    env["PROPERTY_AGENT_WEB_TEMPERATURE"] = str(spec.temperature)
    if args.task:
        env["PROPERTY_AGENT_WEB_TASK"] = args.task

    cmd = [
        chainlit_exe,
        "run",
        module_path(),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.headless:
        cmd.append("--headless")

    sys.stderr.write(
        f"[property-agent] Launching Chainlit on http://{args.host}:{args.port} "
        f"(provider={spec.slug}, temperature={spec.temperature})\n"
        f"[property-agent] Runtime dir: {runtime_dir}\n"
    )
    sys.stderr.flush()
    result = subprocess.run(cmd, env=env, check=False, cwd=str(runtime_dir))
    return result.returncode


def _prepare_chainlit_runtime_dir() -> Path:
    """Seed ``~/.uk-property-agent/web/`` with our bundled Chainlit config.

    Chainlit reads ``.chainlit/config.toml`` and ``chainlit.md`` from
    the current working directory, and serves static assets from a
    sibling ``public/`` directory (``/public/<file>``). We want:

    * Consistent branding (app name, description, dark theme, custom
      CSS, logo, avatar) every time the user runs
      ``property-agent serve``, regardless of where they ran it from.
    * Idempotent behaviour — seeding must not stomp on config the
      user has edited, but must create missing files on first run.
    * No pollution of the repo / temp dirs that stick around.

    Seeding policy:

    * ``.chainlit/config.toml`` and ``chainlit.md`` — **seed if
      missing**. These are user-editable branding / copy; we don't
      overwrite once the user has a copy on disk.
    * ``public/*`` (SVG logos / avatar / favicon / ``custom.css``) —
      **always overwritten** from the shipped defaults. These are
      treated as immutable assets that ship with the package; that
      way a ``pip install --upgrade`` picks up the latest design
      tokens and CSS bug-fixes automatically.

    Users who want a fully fresh runtime can delete
    ``~/.uk-property-agent/web`` — seeding will re-create everything
    on the next launch.
    """

    from importlib import resources

    home_runtime = Path.home() / ".uk-property-agent" / "web"
    (home_runtime / ".chainlit").mkdir(parents=True, exist_ok=True)
    (home_runtime / "public").mkdir(parents=True, exist_ok=True)

    defaults = resources.files("uk_property_agent").joinpath("_web_defaults")

    config_dst = home_runtime / ".chainlit" / "config.toml"
    md_dst = home_runtime / "chainlit.md"
    config_src = defaults.joinpath("chainlit_config.toml")
    md_src = defaults.joinpath("chainlit.md")

    if not config_dst.exists():
        config_dst.write_text(config_src.read_text(encoding="utf-8"), encoding="utf-8")
    if not md_dst.exists():
        md_dst.write_text(md_src.read_text(encoding="utf-8"), encoding="utf-8")

    public_src = defaults.joinpath("public")
    public_dst = home_runtime / "public"
    for asset in public_src.iterdir():
        if asset.is_file():
            text = asset.read_text(encoding="utf-8")
            (public_dst / asset.name).write_text(text, encoding="utf-8")

    return home_runtime


def _run_env(args: argparse.Namespace) -> int:
    import os

    llm = describe_env()
    langsmith_vars = {
        "LANGSMITH_API_KEY": bool(os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")),
        "LANGSMITH_PROJECT": bool(
            os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT")
        ),
        "LANGSMITH_TRACING": bool(
            os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2")
        ),
    }
    if args.json:
        print(
            json.dumps(
                {
                    "llm_providers": llm,
                    "apify": {
                        "APIFY_API_TOKEN": bool(os.getenv("APIFY_API_TOKEN")),
                        "APIFY_PROXY_URL": bool(os.getenv("APIFY_PROXY_URL")),
                    },
                    "data_apis": {
                        "EPC_AUTH_EMAIL": bool(os.getenv("EPC_AUTH_EMAIL")),
                        "EPC_AUTH_TOKEN": bool(os.getenv("EPC_AUTH_TOKEN")),
                        "COMPANIES_HOUSE_API_KEY": bool(os.getenv("COMPANIES_HOUSE_API_KEY")),
                    },
                    "notifications": {
                        "DISCORD_WEBHOOK_URL": bool(os.getenv("DISCORD_WEBHOOK_URL")),
                    },
                    "tracing": langsmith_vars,
                },
                indent=2,
            )
        )
        return 0

    print("UK Property Agent - LLM providers:")
    chain = llm["chain"]
    selected = llm["selected"]
    for provider_slug in chain:
        info = llm["providers"][provider_slug]
        mark = "OK " if info["available"] else "   "
        extra = (
            f"via {', '.join(info['env_vars_set'])}"
            if info["env_vars_set"]
            else f"(needs {', '.join(_required_env(provider_slug))})"
        )
        tag = " [selected]" if selected == provider_slug else ""
        print(
            f"  [{mark}] {provider_slug:9s} default={info['default_model']}  {extra}{tag}"
        )
    print()
    print("UK Property Agent - data / infra:")
    infra = {
        "EPC_AUTH_EMAIL": bool(os.getenv("EPC_AUTH_EMAIL")),
        "EPC_AUTH_TOKEN": bool(os.getenv("EPC_AUTH_TOKEN")),
        "COMPANIES_HOUSE_API_KEY": bool(os.getenv("COMPANIES_HOUSE_API_KEY")),
        "APIFY_API_TOKEN": bool(os.getenv("APIFY_API_TOKEN")),
        "APIFY_PROXY_URL": bool(os.getenv("APIFY_PROXY_URL")),
        "DISCORD_WEBHOOK_URL": bool(os.getenv("DISCORD_WEBHOOK_URL")),
    }
    width = max(len(k) for k in infra)
    for key, present in infra.items():
        mark = "OK " if present else "   "
        print(f"  [{mark}] {key.ljust(width)}  {'set' if present else 'missing'}")
    print()
    print("UK Property Agent - tracing (LangSmith):")
    for key, present in langsmith_vars.items():
        mark = "OK " if present else "   "
        print(f"  [{mark}] {key.ljust(width)}  {'set' if present else 'missing'}")
    print()
    print("UK Property Agent - task overrides recognised:")
    for var in llm["task_env_vars"]:
        present = bool(os.getenv(var))
        mark = "OK " if present else "   "
        print(f"  [{mark}] {var}")
    return 0


def _required_env(provider_slug: str) -> list[str]:
    """Env vars the agent looks up for ``provider_slug`` (for help text)."""

    return {
        "anthropic": ["ANTHROPIC_API_KEY"],
        "openai": ["OPENAI_API_KEY"],
        "gemini": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    }.get(provider_slug, [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="property-agent",
        description="Natural-language UK property intelligence agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask the agent a property question.")
    ask.add_argument("question", help="Natural-language question to ask.")
    ask.add_argument(
        "--provider",
        default=None,
        help=(
            "Override provider: 'anthropic', 'openai', 'gemini', or a "
            "'provider/model' slug like 'openai/gpt-4o'. Overrides "
            "AGENT_PROVIDER and per-task env. Defaults to the first "
            "provider in AGENT_PROVIDER_CHAIN with credentials."
        ),
    )
    ask.add_argument(
        "--model",
        default=None,
        help=(
            "Override the model name only (provider is inferred from "
            "--provider or defaults). Eg. --model claude-sonnet-4-5-20250929."
        ),
    )
    ask.add_argument(
        "--task",
        default=None,
        choices=[t.value for t in TaskKind],
        help=(
            "Route via task-specific env override "
            "(AGENT_MODEL_<TASK>). Default: 'default'."
        ),
    )
    ask.add_argument("--temperature", type=float, default=0.2)
    ask.add_argument(
        "--stream",
        action="store_true",
        help=(
            "Stream the narrative answer to stdout (token-by-token when the "
            "provider supports it) and intermediate tool calls / results to "
            "stderr. Without this flag the answer is printed in one chunk "
            "when the agent finishes."
        ),
    )
    ask.add_argument(
        "--show-provider",
        action="store_true",
        help="Print the resolved provider/model to stderr before asking.",
    )
    ask.set_defaults(func=_run_ask)

    chat = sub.add_parser(
        "chat",
        help=(
            "Interactive multi-turn REPL with LangGraph memory. Slash "
            "commands: /help, /clear, /provider SLUG, /tools, /stream on|off, /exit."
        ),
    )
    chat.add_argument(
        "--provider",
        default=None,
        help=(
            "Starting provider slug; swap mid-session with '/provider SLUG'. "
            "Same format as `ask --provider`."
        ),
    )
    chat.add_argument(
        "--model",
        default=None,
        help="Override the model name only (provider inferred).",
    )
    chat.add_argument(
        "--task",
        default=None,
        choices=[t.value for t in TaskKind],
        help="Task routing (AGENT_MODEL_<TASK>). Default: 'default'.",
    )
    chat.add_argument("--temperature", type=float, default=0.2)
    chat.add_argument(
        "--no-stream",
        action="store_true",
        help=(
            "Disable inline tool-call rendering; answers print as a single "
            "block per turn. Memory is still retained across turns."
        ),
    )
    chat.set_defaults(func=_run_chat)

    serve = sub.add_parser(
        "serve",
        help=(
            "Launch the Chainlit web UI for local demo use. Requires the "
            "`web` install extra: `uv pip install 'uk-property-agent[web]'`."
        ),
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Bind host. Use 0.0.0.0 to expose externally (demo-only — there's "
            "no auth in front of this)."
        ),
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port. Defaults to Chainlit's default (8000).",
    )
    serve.add_argument(
        "--headless",
        action="store_true",
        help="Don't auto-open the browser.",
    )
    serve.add_argument(
        "--provider",
        default=None,
        help="Starting provider slug, same format as `ask --provider`.",
    )
    serve.add_argument("--model", default=None)
    serve.add_argument(
        "--task",
        default=None,
        choices=[t.value for t in TaskKind],
    )
    serve.add_argument("--temperature", type=float, default=0.2)
    serve.set_defaults(func=_run_serve)

    tools = sub.add_parser("tools", help="List configured tools.")
    tools.set_defaults(func=_run_tools)

    env = sub.add_parser("env", help="Show detected env / credentials per provider.")
    env.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary instead of the table view.",
    )
    env.set_defaults(func=_run_env)

    args = parser.parse_args(argv)
    result = args.func(args)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
