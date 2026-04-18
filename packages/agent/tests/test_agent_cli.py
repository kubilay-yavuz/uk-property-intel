"""Unit tests for ``uk_property_agent.cli``.

We exercise the ``tools`` and ``env`` subcommands directly (no model needed)
and patch ``_build_model`` / ``PropertyAgent`` for ``ask`` so we don't hit
any real LLM.
"""

from __future__ import annotations

from typing import Any

import pytest
from uk_property_agent import cli


class TestEnvCommand:
    def test_env_runs_and_lists_flags(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.main(["env"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "ANTHROPIC_API_KEY" in out
        assert "EPC_AUTH_EMAIL" in out


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


class TestAskCommand:
    def test_ask_prints_final_answer(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "_build_model", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "PropertyAgent", _FakeAgent)

        rc = cli.main(["ask", "Hello Cambridge?"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "echo: Hello Cambridge?" in out

    def test_ask_streams_and_prints_final(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "_build_model", lambda *a, **kw: object())
        monkeypatch.setattr(cli, "PropertyAgent", _FakeAgent)

        rc = cli.main(["ask", "any?", "--stream"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "echo: any?" in out

    def test_ask_fails_without_anthropic_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(SystemExit):
            cli._build_model(None, 0.2)
