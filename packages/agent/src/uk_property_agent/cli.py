"""Command-line entry point for the UK Property agent.

Usage
-----
.. code-block:: shell

    property-agent ask "Best 3 family homes for sale in Cambridge under £800k?"
    property-agent ask --stream "..."
    property-agent ask --provider openai --model gpt-4o "..."
    property-agent ask --provider gemini/gemini-2.5-pro "..."
    property-agent tools          # list configured tools
    property-agent env            # print detected env / credentials per provider

Default mode runs the graph to completion and prints the final narrative
to stdout.

``--stream`` (Agent v3) splits the transport: intermediate tool calls and
tool results are emitted to **stderr**, and the final narrative streams
to **stdout** token-by-token as the model generates it (falling back to
a single chunk when the underlying provider doesn't implement token
streaming). Pipe stdout to a UI or log collector; redirect stderr to
``/dev/null`` if you only want the answer.

Model selection now routes through
:func:`uk_property_agent.providers.resolve_provider`, so any provider whose
credentials are present (Anthropic / OpenAI / Gemini) will be used
automatically; use ``--provider`` or ``AGENT_PROVIDER`` to pin explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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


def _run_env(args: argparse.Namespace) -> int:
    import os

    llm = describe_env()
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
