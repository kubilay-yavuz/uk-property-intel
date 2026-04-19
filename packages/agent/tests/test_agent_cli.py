"""Unit tests for ``uk_property_agent.cli``.

We exercise the ``tools`` and ``env`` subcommands directly (no model needed)
and patch ``_build_model`` / ``PropertyAgent`` for ``ask`` so we don't hit
any real LLM.
"""

from __future__ import annotations

from pathlib import Path
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


class TestPrepareChainlitRuntimeDir:
    """``_prepare_chainlit_runtime_dir`` seeds ``~/.uk-property-agent/web``.

    The function has two distinct seeding policies we need to lock in:

    * ``chainlit.md`` and ``.chainlit/config.toml`` — **seed if
      missing** (so user edits survive upgrades).
    * ``public/*`` assets (CSS, logos, icons, favicon, avatar) —
      **always overwritten** from the bundled defaults (so a
      ``pip install --upgrade`` picks up CSS/brand fixes).
    """

    @staticmethod
    def _patch_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    def test_first_run_seeds_config_markdown_and_public_assets(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_home(monkeypatch, tmp_path)

        runtime = cli._prepare_chainlit_runtime_dir()

        assert runtime == tmp_path / ".uk-property-agent" / "web"
        assert (runtime / ".chainlit" / "config.toml").is_file()
        assert (runtime / "chainlit.md").is_file()

        public_dir = runtime / "public"
        assert public_dir.is_dir()
        for asset in (
            "custom.css",
            "logo_dark.svg",
            "logo_light.svg",
            "favicon.svg",
            "avatar.svg",
            "icon_home.svg",
            "icon_dossier.svg",
            "icon_route.svg",
            "icon_trend.svg",
        ):
            assert (public_dir / asset).is_file(), f"missing seeded asset: {asset}"

    def test_seeded_config_points_at_custom_css_and_avatar(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_home(monkeypatch, tmp_path)

        runtime = cli._prepare_chainlit_runtime_dir()
        config_text = (runtime / ".chainlit" / "config.toml").read_text("utf-8")

        assert 'custom_css = "/public/custom.css"' in config_text
        assert 'default_avatar_file_url = "/public/avatar.svg"' in config_text
        assert 'default_theme = "dark"' in config_text
        assert 'name = "UK Property Intelligence"' in config_text

    def test_seeded_custom_css_carries_brand_tokens(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_home(monkeypatch, tmp_path)

        runtime = cli._prepare_chainlit_runtime_dir()
        css = (runtime / "public" / "custom.css").read_text("utf-8")

        # Brand amber (36 65% 62%) is the signature primary colour
        # in dark mode — lock that in so future drifts are caught.
        assert "36 65% 62%" in css
        # Charcoal background + warm off-white foreground are the
        # two other tokens the rest of the design leans on.
        assert "220 13% 8%" in css
        assert "40 18% 94%" in css
        # Respecting reduced-motion is a deliberate accessibility
        # contract; make sure the guard stays in.
        assert "prefers-reduced-motion" in css

    def test_user_edits_to_markdown_and_config_are_preserved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Second call must not overwrite user-edited branding files."""

        self._patch_home(monkeypatch, tmp_path)
        runtime = cli._prepare_chainlit_runtime_dir()

        user_md = "# my bespoke welcome page"
        user_config = '[UI]\nname = "My Fork"\n'
        (runtime / "chainlit.md").write_text(user_md, "utf-8")
        (runtime / ".chainlit" / "config.toml").write_text(user_config, "utf-8")

        cli._prepare_chainlit_runtime_dir()

        assert (runtime / "chainlit.md").read_text("utf-8") == user_md
        assert (runtime / ".chainlit" / "config.toml").read_text("utf-8") == user_config

    def test_public_assets_always_overwritten(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``public/*`` is a shipped-asset dir; re-seeding must refresh it.

        Rationale: a user ``pip install --upgrade``s the package to
        pick up a CSS polish fix. Without a refresh they'd keep the
        stale version from their first run indefinitely.
        """

        self._patch_home(monkeypatch, tmp_path)
        runtime = cli._prepare_chainlit_runtime_dir()

        css_path = runtime / "public" / "custom.css"
        css_path.write_text("/* user clobbered it */", "utf-8")

        cli._prepare_chainlit_runtime_dir()

        refreshed = css_path.read_text("utf-8")
        assert "/* user clobbered it */" not in refreshed
        assert "UK Property Intelligence" in refreshed
