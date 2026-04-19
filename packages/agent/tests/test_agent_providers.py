"""Unit tests for ``uk_property_agent.providers``.

We cover four axes:

1. **Credential detection** — ``has_credentials`` and
   ``select_available_provider`` walk the configured preference chain and
   fall through cleanly when nothing is set.
2. **Resolution precedence** — ``resolve_provider`` honours explicit
   arguments > ``AGENT_MODEL_<TASK>`` > ``AGENT_MODEL_DEFAULT`` >
   ``AGENT_PROVIDER`` > fallback chain, and produces a fully-hydrated
   :class:`ProviderSpec`.
3. **Cache shape routing** — :func:`cache_shape_for` and the downstream
   :func:`build_system_message` both switch payload shape on provider.
4. **Lazy model build** — :func:`build_chat_model` raises a friendly
   ``ImportError`` (not a bare ``ModuleNotFoundError``) when the
   backing ``langchain-*`` package is missing, so users know which
   install extra to run.

We explicitly **don't** exercise live LLM traffic here — the fallback
chain is tested with injected env dicts, which makes these tests
deterministic against any local developer shell state.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import SystemMessage
from uk_property_agent import (
    Provider,
    ProviderSpec,
    TaskKind,
    build_chat_model,
    build_system_message,
    cache_shape_for,
    describe_env,
    has_credentials,
    resolve_provider,
    select_available_provider,
)

_ALL_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AGENT_PROVIDER",
    "AGENT_PROVIDER_CHAIN",
    "AGENT_MODEL_DEFAULT",
    "AGENT_MODEL_DOSSIER",
    "AGENT_MODEL_ANALYSIS",
    "AGENT_MODEL_TOOL_PLAN",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Wipe every env var the provider layer consults.

    Individual tests that want a specific env var flip it back on via
    ``monkeypatch.setenv`` after the fixture runs.
    """

    for var in _ALL_KEYS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestCredentialDetection:
    def test_has_credentials_returns_true_when_any_var_set(self) -> None:
        env = {"ANTHROPIC_API_KEY": "sk-ant"}
        assert has_credentials(Provider.ANTHROPIC, env=env) is True
        assert has_credentials(Provider.OPENAI, env=env) is False

    def test_gemini_accepts_either_google_or_gemini_key(self) -> None:
        assert has_credentials(Provider.GEMINI, env={"GOOGLE_API_KEY": "g"}) is True
        assert has_credentials(Provider.GEMINI, env={"GEMINI_API_KEY": "g"}) is True
        assert has_credentials(Provider.GEMINI, env={}) is False

    def test_select_available_provider_respects_preference(self) -> None:
        env = {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o"}
        assert select_available_provider(env=env) == Provider.ANTHROPIC
        assert (
            select_available_provider(
                preferred=[Provider.OPENAI, Provider.ANTHROPIC], env=env
            )
            == Provider.OPENAI
        )

    def test_select_available_provider_returns_none_on_empty_env(self) -> None:
        assert select_available_provider(env={}) is None


class TestResolveProviderExplicit:
    def test_explicit_provider_enum_uses_default_model(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        spec = resolve_provider(explicit=Provider.OPENAI)
        assert spec.provider == Provider.OPENAI
        assert spec.model == "gpt-4o"
        assert spec.temperature == 0.2

    def test_explicit_provider_slug_string(self, clean_env: pytest.MonkeyPatch) -> None:
        spec = resolve_provider(explicit="openai/gpt-4o-mini")
        assert spec.slug == "openai/gpt-4o-mini"

    def test_explicit_provider_slug_no_model_falls_back_to_default(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        spec = resolve_provider(explicit="gemini")
        assert spec.provider == Provider.GEMINI
        assert spec.model == "gemini-2.5-pro"

    def test_explicit_providerspec_roundtrip(self, clean_env: pytest.MonkeyPatch) -> None:
        original = ProviderSpec(
            provider=Provider.ANTHROPIC,
            model="claude-3-5-haiku-20241022",
            temperature=0.7,
            max_tokens=1024,
        )
        out = resolve_provider(explicit=original)
        assert out.provider == Provider.ANTHROPIC
        assert out.model == "claude-3-5-haiku-20241022"
        assert out.temperature == 0.7
        assert out.max_tokens == 1024

    def test_explicit_providerspec_honours_temperature_override(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        original = ProviderSpec(
            provider=Provider.OPENAI, model="gpt-4o", temperature=0.1
        )
        out = resolve_provider(explicit=original, temperature=0.9)
        assert out.temperature == 0.9

    def test_rejects_unknown_slug_falling_through_to_chain(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        clean_env.setenv("OPENAI_API_KEY", "o")
        spec = resolve_provider(explicit="nonsense/model")
        assert spec.provider == Provider.OPENAI


class TestResolveProviderEnvOverrides:
    def test_task_env_wins_over_default_env(self) -> None:
        env = {
            "ANTHROPIC_API_KEY": "a",
            "AGENT_MODEL_DEFAULT": "anthropic/claude-sonnet-4-5-20250929",
            "AGENT_MODEL_DOSSIER": "openai/gpt-4o",
        }
        spec = resolve_provider(task=TaskKind.DOSSIER, env=env)
        assert spec.slug == "openai/gpt-4o"

    def test_task_env_falls_back_to_default_env(self) -> None:
        env = {
            "ANTHROPIC_API_KEY": "a",
            "AGENT_MODEL_DEFAULT": "gemini/gemini-2.5-pro",
        }
        spec = resolve_provider(task=TaskKind.ANALYSIS, env=env)
        assert spec.slug == "gemini/gemini-2.5-pro"

    def test_agent_provider_env_with_default_model(self) -> None:
        env = {"AGENT_PROVIDER": "openai", "OPENAI_API_KEY": "o"}
        spec = resolve_provider(env=env)
        assert spec.provider == Provider.OPENAI
        assert spec.model == "gpt-4o"

    def test_agent_provider_ignores_unknown_slug(self) -> None:
        env = {
            "AGENT_PROVIDER": "nonsense",
            "OPENAI_API_KEY": "o",
        }
        spec = resolve_provider(env=env)
        assert spec.provider == Provider.OPENAI

    def test_bare_model_name_pairs_with_task_default_provider(self) -> None:
        env = {
            "ANTHROPIC_API_KEY": "a",
            "AGENT_MODEL_DEFAULT": "claude-3-5-haiku-20241022",
        }
        spec = resolve_provider(env=env)
        assert spec.provider == Provider.ANTHROPIC
        assert spec.model == "claude-3-5-haiku-20241022"


class TestResolveProviderFallbackChain:
    def test_falls_through_to_first_available(self) -> None:
        env = {"OPENAI_API_KEY": "o", "GOOGLE_API_KEY": "g"}
        spec = resolve_provider(env=env)
        assert spec.provider == Provider.OPENAI

    def test_custom_chain_reorders(self) -> None:
        env = {
            "AGENT_PROVIDER_CHAIN": "gemini,openai,anthropic",
            "OPENAI_API_KEY": "o",
            "GOOGLE_API_KEY": "g",
        }
        spec = resolve_provider(env=env)
        assert spec.provider == Provider.GEMINI

    def test_raises_when_no_credentials(self) -> None:
        with pytest.raises(RuntimeError, match="No provider credentials"):
            resolve_provider(env={})


class TestCacheShapeRouting:
    def test_anthropic_uses_content_blocks(self) -> None:
        assert cache_shape_for(Provider.ANTHROPIC) == "anthropic_block"

    def test_openai_and_gemini_use_plain_string(self) -> None:
        assert cache_shape_for(Provider.OPENAI) == "plain_string"
        assert cache_shape_for(Provider.GEMINI) == "plain_string"

    def test_build_system_message_shape_matches_provider(self) -> None:
        anthropic_msg = build_system_message(provider=Provider.ANTHROPIC)
        openai_msg = build_system_message(provider=Provider.OPENAI)
        gemini_msg = build_system_message(provider=Provider.GEMINI)

        assert isinstance(anthropic_msg, SystemMessage)
        assert isinstance(anthropic_msg.content, list)
        assert anthropic_msg.content[0]["cache_control"]["type"] == "ephemeral"  # type: ignore[index]

        assert isinstance(openai_msg.content, str)
        assert isinstance(gemini_msg.content, str)

    def test_disable_cache_always_returns_string(self) -> None:
        for provider in Provider:
            msg = build_system_message(enable_cache=False, provider=provider)
            assert isinstance(msg.content, str)


class TestDescribeEnv:
    def test_describe_env_reports_selected_provider(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant")
        snapshot = describe_env()
        assert snapshot["selected"] == "anthropic"
        assert snapshot["providers"]["anthropic"]["available"] is True
        assert snapshot["providers"]["openai"]["available"] is False

    def test_describe_env_task_env_vars_enumerated(self) -> None:
        snapshot = describe_env(env={})
        assert "AGENT_MODEL_DOSSIER" in snapshot["task_env_vars"]
        assert "AGENT_MODEL_DEFAULT" in snapshot["task_env_vars"]
        assert snapshot["selected"] is None


class TestBuildChatModelErrors:
    def test_unsupported_provider_raises(self) -> None:
        class _FakeProvider:
            value = "ollama"

        bogus = ProviderSpec.__new__(ProviderSpec)  # bypass frozen
        object.__setattr__(bogus, "provider", _FakeProvider())
        object.__setattr__(bogus, "model", "llama3")
        object.__setattr__(bogus, "temperature", 0.2)
        object.__setattr__(bogus, "max_tokens", None)
        object.__setattr__(bogus, "kwargs", None)

        with pytest.raises(ValueError, match="Unsupported provider"):
            build_chat_model(bogus)

    def test_missing_openai_install_raises_friendly_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "langchain_openai":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match=r"uk-property-agent\[openai\]"):
            build_chat_model(ProviderSpec(provider=Provider.OPENAI, model="gpt-4o"))

    def test_missing_gemini_install_raises_friendly_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "langchain_google_genai":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match=r"uk-property-agent\[gemini\]"):
            build_chat_model(
                ProviderSpec(provider=Provider.GEMINI, model="gemini-2.5-pro")
            )


class TestPropertyAgentProviderIntegration:
    """PropertyAgent uses the resolved provider to shape the system message."""

    def test_agent_uses_plain_string_for_openai(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from langchain_core.language_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from uk_property_agent import PropertyAgent, ToolContext
        from uk_property_agent.providers import Provider, ProviderSpec

        class _Tool(FakeMessagesListChatModel):
            def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
                return self

        fake_spec = ProviderSpec(provider=Provider.OPENAI, model="gpt-4o")
        fake_model = _Tool(responses=[AIMessage(content="hi")])

        captured: dict[str, Any] = {}

        def _fake_resolve(**kw: Any) -> ProviderSpec:
            captured["kw"] = kw
            return fake_spec

        def _fake_build(spec: ProviderSpec) -> Any:
            captured["spec"] = spec
            return fake_model

        monkeypatch.setattr("uk_property_agent.agent.resolve_provider", _fake_resolve)
        monkeypatch.setattr("uk_property_agent.agent.build_chat_model", _fake_build)

        agent = PropertyAgent(provider=Provider.OPENAI, tool_context=ToolContext())
        assert agent.provider_spec == fake_spec
        assert isinstance(agent.system_message.content, str)

    def test_agent_rejects_model_and_provider_together(self) -> None:
        from langchain_core.language_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from uk_property_agent import PropertyAgent, ToolContext

        class _Tool(FakeMessagesListChatModel):
            def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
                return self

        with pytest.raises(ValueError, match="either an explicit 'model' or"):
            PropertyAgent(
                model=_Tool(responses=[AIMessage(content="x")]),
                provider=Provider.ANTHROPIC,
                tool_context=ToolContext(),
            )
