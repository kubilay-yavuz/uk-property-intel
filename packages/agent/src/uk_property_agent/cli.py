"""Command-line entry point for the UK Property agent.

Usage
-----
.. code-block:: shell

    property-agent ask "Best 3 family homes for sale in Cambridge under £800k?"
    property-agent tools          # list configured tools
    property-agent env            # print detected env / credentials

The ``ask`` subcommand streams intermediate tool-call events to stderr so
you can watch the agent plan, then prints the final natural-language answer
to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from uk_property_agent.agent import PropertyAgent
from uk_property_agent.tools import ToolContext, build_tools


def _build_model(model_name: str | None, temperature: float) -> Any:
    """Construct a ChatAnthropic model from the environment.

    Imported lazily to avoid forcing ``langchain-anthropic`` on users who
    only want the tools + graph primitives.
    """
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        msg = (
            "langchain-anthropic not installed. Install the agent with the "
            "'anthropic' extra:\n\n"
            "    uv pip install 'uk-property-agent[anthropic]'\n"
        )
        raise SystemExit(msg) from exc

    if "ANTHROPIC_API_KEY" not in os.environ:
        raise SystemExit("ANTHROPIC_API_KEY is not set. Export it before running the agent.")

    return ChatAnthropic(
        model=model_name or "claude-sonnet-4-5-20250929",
        temperature=temperature,
        max_tokens=4096,
    )


async def _run_ask(args: argparse.Namespace) -> int:
    model = _build_model(args.model, args.temperature)
    ctx = ToolContext.from_env()
    agent = PropertyAgent(model=model, tool_context=ctx)

    if args.stream:
        async for chunk in agent.astream(args.question):
            _render_chunk_to_stderr(chunk)
        final = await agent.ainvoke(args.question)
    else:
        final = await agent.ainvoke(args.question)

    print(final)
    return 0


def _render_chunk_to_stderr(chunk: dict[str, Any]) -> None:
    for node_name, payload in chunk.items():
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not messages:
            continue
        for msg in messages:
            name = type(msg).__name__
            content = getattr(msg, "content", "")
            if name == "AIMessage" and getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    print(
                        f"[{node_name}] tool_call: {tc.get('name')}({tc.get('args')})",
                        file=sys.stderr,
                    )
            elif name == "ToolMessage":
                short = str(content)[:280].replace("\n", " ")
                print(f"[{node_name}] tool_result: {short}", file=sys.stderr)


def _run_tools(_: argparse.Namespace) -> int:
    ctx = ToolContext.from_env()
    tools = build_tools(ctx)
    print(f"Configured tools ({len(tools)}):")
    for tool in tools:
        print(f"  - {tool.name}: {tool.description.strip().splitlines()[0]}")
    return 0


def _run_env(_: argparse.Namespace) -> int:
    checks = {
        "ANTHROPIC_API_KEY": bool(os.getenv("ANTHROPIC_API_KEY")),
        "EPC_AUTH_EMAIL": bool(os.getenv("EPC_AUTH_EMAIL")),
        "EPC_AUTH_TOKEN": bool(os.getenv("EPC_AUTH_TOKEN")),
        "COMPANIES_HOUSE_API_KEY": bool(os.getenv("COMPANIES_HOUSE_API_KEY")),
        "APIFY_PROXY_URL": bool(os.getenv("APIFY_PROXY_URL")),
        "DISCORD_WEBHOOK_URL": bool(os.getenv("DISCORD_WEBHOOK_URL")),
    }
    width = max(len(k) for k in checks)
    print("UK Property Agent - environment:")
    for key, present in checks.items():
        mark = "OK " if present else "   "
        print(f"  [{mark}] {key.ljust(width)}  {'set' if present else 'missing'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="property-agent",
        description="Natural-language UK property intelligence agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask the agent a property question.")
    ask.add_argument("question", help="Natural-language question to ask.")
    ask.add_argument(
        "--model",
        default=None,
        help="Override the Anthropic model name. Defaults to the latest Claude Sonnet.",
    )
    ask.add_argument("--temperature", type=float, default=0.2)
    ask.add_argument(
        "--stream",
        action="store_true",
        help="Stream intermediate tool calls to stderr.",
    )
    ask.set_defaults(func=_run_ask)

    tools = sub.add_parser("tools", help="List configured tools.")
    tools.set_defaults(func=_run_tools)

    env = sub.add_parser("env", help="Show detected env / credentials.")
    env.set_defaults(func=_run_env)

    args = parser.parse_args(argv)
    result = args.func(args)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
