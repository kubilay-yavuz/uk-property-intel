"""Unit tests for ``uk_property_agent.cli``.

We exercise the ``tools`` and ``env`` subcommands directly (no model needed)
and patch ``_build_model`` / ``PropertyAgent`` for ``ask`` so we don't hit
any real LLM.
"""

from __future__ import annotations

from typing import Any

import pytest
from uk_property_agent import cli
from uk_property_agent.providers import Provider, ProviderSpec


class TestEnvCommand:
    def test_env_runs_and_lists_providers(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        rc = cli.main(["env"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "anthropic" in out.lower()
        assert "openai" in out.lower()
        assert "gemini" in out.lower()
        assert "EPC_AUTH_EMAIL" in out

    def test_env_json_emits_structured_blob(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        rc = cli.main(["env", "--json"])
        out = capsys.readouterr().out
        assert rc == 0
        import json as _json

        parsed = _json.loads(out)
        assert parsed["llm_providers"]["providers"]["openai"]["available"] is True


class TestToolsCommand:
    def test_tools_lists_default_set(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.main(["tools"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "search_zoopla" in out
        assert "lookup_postcode" in out


class _FakeAgent:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    async def ainvoke(self, question: str) -> str:
        return f"echo: {question}"

    async def astream(self, question: str):
        yield {"stub": {"messages": []}}

    async def astream_events(self, question: str):
        """Mimic the real agent: tool_call → tool_result → narrative → final."""

        yield {
            "type": "tool_call",
            "name": "search_zoopla",
            "args": {"location": "Cambridge"},
            "id": "call_1",
        }
        yield {
            "type": "tool_result",
            "name": "search_zoopla",
            "content": "fake listings payload",
        }
        yield {"type": "narrative", "text": f"echo: {question}"}
        yield {"type": "final", "text": f"echo: {question}"}


def _patch_build_model(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: Provider = Provider.ANTHROPIC,
    model: str = "fake-model",
) -> None:
    spec = ProviderSpec(provider=provider, model=model)
    monkeypatch.setattr(cli, "_build_model", lambda _args: (spec, object()))


class TestAskCommand:
    def test_ask_prints_final_answer(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _patch_build_model(monkeypatch)
        monkeypatch.setattr(cli, "PropertyAgent", _FakeAgent)

        rc = cli.main(["ask", "Hello Cambridge?"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "echo: Hello Cambridge?" in out

    def test_ask_streams_narrative_to_stdout_and_tools_to_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--stream`` splits transport: narrative to stdout, tools to stderr."""

        _patch_build_model(monkeypatch)
        monkeypatch.setattr(cli, "PropertyAgent", _FakeAgent)

        rc = cli.main(["ask", "any?", "--stream"])
        captured = capsys.readouterr()
        assert rc == 0
        # Narrative token ('echo: any?') lands on stdout, *not* stderr.
        assert "echo: any?" in captured.out
        assert "echo: any?" not in captured.err
        # Tool call + tool result go to stderr.
        assert "[tool_call] search_zoopla" in captured.err
        assert "[tool_result] search_zoopla" in captured.err

    def test_ask_show_provider_prints_slug(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _patch_build_model(monkeypatch, provider=Provider.OPENAI, model="gpt-4o")
        monkeypatch.setattr(cli, "PropertyAgent", _FakeAgent)

        rc = cli.main(["ask", "any?", "--show-provider"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "openai/gpt-4o" in captured.err
        assert "echo: any?" in captured.out

    def test_ask_fails_when_no_provider_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AGENT_PROVIDER",
            "AGENT_MODEL_DEFAULT",
        ):
            monkeypatch.delenv(var, raising=False)

        class _Args:
            provider = None
            model = None
            task = None
            temperature = 0.2

        with pytest.raises(SystemExit):
            cli._build_model(_Args())
