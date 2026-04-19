"""Multi-provider chat-model routing for the UK Property agent.

The agent historically hard-coded Anthropic; this module generalises the
plumbing so a single :class:`PropertyAgent` can be spun up against any of
the three supported model families, with per-task model pinning and an
explicit fallback chain for missing credentials.

Design goals
------------

1. **One-call build.** :func:`build_chat_model` takes a
   :class:`ProviderSpec` and returns a configured LangChain
   ``BaseChatModel``; callers never touch ``langchain_anthropic`` /
   ``langchain_openai`` / ``langchain_google_genai`` directly.

2. **Per-task pinning.** :func:`resolve_provider` turns a
   :class:`TaskKind` into a :class:`ProviderSpec`, honouring
   ``AGENT_MODEL_<TASK>`` env overrides (e.g.
   ``AGENT_MODEL_DOSSIER=gemini/gemini-2.5-pro``). Callers that don't
   care about per-task differences can just pass ``task="default"``
   (or :data:`TaskKind.DEFAULT`).

3. **Explicit fallback.** :func:`select_available_provider` walks a
   preference list (``AGENT_PROVIDER_CHAIN``, defaults
   ``anthropic,openai,gemini``) and returns the first provider whose
   credentials are available, so a missing ``ANTHROPIC_API_KEY``
   gracefully degrades to OpenAI or Gemini rather than crashing at
   import time.

4. **Provider-aware cache shape.** Anthropic needs explicit
   ``cache_control`` content blocks, OpenAI caches automatically on any
   prefix ≥1024 tokens, Gemini offers implicit + explicit context
   caches. :func:`cache_shape_for` encodes the right choice per
   provider so :func:`uk_property_agent.prompts.build_system_message`
   stays a thin formatter.

No ``langchain-*`` package is imported at module level — they're all
optional dependencies pulled lazily inside :func:`build_chat_model` so
a consumer that only wants the tool primitives doesn't get forced into
installing three LLM SDKs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


class Provider(StrEnum):
    """Supported LLM providers.

    The string values double as the canonical slugs used in env vars
    (``AGENT_PROVIDER=anthropic``) and the ``provider/model`` shorthand
    (``anthropic/claude-sonnet-4-5``).
    """

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"


class TaskKind(StrEnum):
    """Logical tasks the agent performs, each potentially pinned to a
    different provider/model.

    Defined here (rather than inlined as arbitrary strings) so env
    resolution can enumerate the full set when validating overrides.
    """

    DEFAULT = "default"
    """Catch-all — used when no specific task is supplied."""

    DOSSIER = "dossier"
    """Structured property-dossier synthesis (Pydantic output, long context)."""

    ANALYSIS = "analysis"
    """Open-ended comparative analysis ("top 3 in X for commuters")."""

    TOOL_PLAN = "tool_plan"
    """Tool-call planning / routing; cheap-and-fast models are fine here."""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Fully-resolved ``provider + model + kwargs`` triple.

    Immutability means a spec can safely be used as a cache key or
    logged without leaking mutation concerns.
    """

    provider: Provider
    model: str
    temperature: float = 0.2
    max_tokens: int | None = None
    kwargs: dict[str, Any] | None = None

    @property
    def slug(self) -> str:
        """``provider/model`` shorthand, matches the env-var format."""

        return f"{self.provider.value}/{self.model}"


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


_DEFAULT_MODELS: dict[Provider, str] = {
    # Sonnet 4.5 is the default agent brain; balanced price/quality.
    Provider.ANTHROPIC: "claude-sonnet-4-5-20250929",
    # GPT-4o is the "modern stable" OpenAI pick — newer reasoning models
    # churn names too fast to bake in.
    Provider.OPENAI: "gpt-4o",
    # Gemini 2.5 Pro is the Google analogue of Sonnet.
    Provider.GEMINI: "gemini-2.5-pro",
}


# Per-task baseline provider pinning. Consumers override via
# ``AGENT_MODEL_<TASK>`` env vars (see :func:`resolve_provider`).
# We keep all tasks on Anthropic by default so the cache-control and
# prompt-caching story is consistent; task-level overrides are for
# power users who want to mix providers per call.
_DEFAULT_TASK_PROVIDER: dict[TaskKind, Provider] = {
    TaskKind.DEFAULT: Provider.ANTHROPIC,
    TaskKind.DOSSIER: Provider.ANTHROPIC,
    TaskKind.ANALYSIS: Provider.ANTHROPIC,
    TaskKind.TOOL_PLAN: Provider.ANTHROPIC,
}


_API_KEY_ENV: dict[Provider, tuple[str, ...]] = {
    Provider.ANTHROPIC: ("ANTHROPIC_API_KEY",),
    Provider.OPENAI: ("OPENAI_API_KEY",),
    # Gemini accepts either key — try both so we're tolerant of env
    # inherited from older Vertex / AI-Studio setups.
    Provider.GEMINI: ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


def _default_chain(env: dict[str, str] | None = None) -> list[Provider]:
    """Parse ``AGENT_PROVIDER_CHAIN`` (default anthropic,openai,gemini).

    ``env`` is optional so callers in tests can inject a synthetic
    environment without touching the real process env. When omitted we
    fall through to :data:`os.environ`.
    """

    source = env if env is not None else os.environ
    raw = source.get("AGENT_PROVIDER_CHAIN") or "anthropic,openai,gemini"
    out: list[Provider] = []
    seen: set[Provider] = set()
    for part in raw.split(","):
        slug = part.strip().lower()
        if not slug:
            continue
        try:
            provider = Provider(slug)
        except ValueError:
            # Silently skip unknown slugs — better to still return a
            # usable chain than to raise at import time on a typo.
            continue
        if provider in seen:
            continue
        seen.add(provider)
        out.append(provider)
    return out or list(Provider)


# ---------------------------------------------------------------------------
# Environment-driven resolution
# ---------------------------------------------------------------------------


def has_credentials(provider: Provider, env: dict[str, str] | None = None) -> bool:
    """True if any of ``provider``'s accepted API-key env vars is set."""

    source = env if env is not None else os.environ
    return any(bool(source.get(var)) for var in _API_KEY_ENV[provider])


def select_available_provider(
    *,
    preferred: list[Provider] | None = None,
    env: dict[str, str] | None = None,
) -> Provider | None:
    """First provider in ``preferred`` (or the configured chain) with creds.

    Returns ``None`` when no provider has credentials set — callers that
    want a hard fail should raise themselves; this function stays
    neutral so tests can inject an empty env without blowing up.

    When ``preferred`` is omitted, the chain is read from the same
    ``env`` mapping so tests can inject ``AGENT_PROVIDER_CHAIN`` without
    mutating the process environment.
    """

    chain = preferred if preferred is not None else _default_chain(env)
    for provider in chain:
        if has_credentials(provider, env):
            return provider
    return None


def _parse_provider_model_slug(value: str) -> tuple[Provider | None, str]:
    """Split ``"provider/model"`` into parts; when the slug has no ``/``
    we treat the whole string as a model name with an unknown provider
    and let the caller infer it from defaults / env.
    """

    if "/" not in value:
        return (None, value.strip())
    head, _, tail = value.partition("/")
    slug = head.strip().lower()
    try:
        provider = Provider(slug)
    except ValueError:
        return (None, value.strip())
    return (provider, tail.strip())


def _task_env_var(task: TaskKind) -> str:
    return f"AGENT_MODEL_{task.value.upper()}"


def resolve_provider(
    task: TaskKind | str = TaskKind.DEFAULT,
    *,
    explicit: ProviderSpec | Provider | str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    env: dict[str, str] | None = None,
) -> ProviderSpec:
    """Resolve a :class:`TaskKind` (+ optional overrides) to a :class:`ProviderSpec`.

    Resolution precedence, highest first:

    1. ``explicit`` argument — a ready :class:`ProviderSpec`, a
       :class:`Provider` enum, or a ``"provider/model"`` slug.
    2. ``AGENT_MODEL_<TASK>`` env (e.g. ``AGENT_MODEL_DOSSIER=openai/gpt-4o``).
    3. ``AGENT_MODEL_DEFAULT`` env (global fallback).
    4. ``AGENT_PROVIDER`` env + per-provider default model.
    5. Fallback chain (:func:`select_available_provider`) with that
       provider's default model.

    Raises ``RuntimeError`` only if every one of the above yields nothing
    *and* no provider has credentials — i.e. the agent genuinely cannot
    build a model.
    """

    task_enum = TaskKind(task) if isinstance(task, str) else task
    source = env if env is not None else os.environ

    resolved_temp = 0.2 if temperature is None else temperature

    def _finalise(provider: Provider, model: str) -> ProviderSpec:
        return ProviderSpec(
            provider=provider,
            model=model,
            temperature=resolved_temp,
            max_tokens=max_tokens,
        )

    if isinstance(explicit, ProviderSpec):
        return ProviderSpec(
            provider=explicit.provider,
            model=explicit.model,
            temperature=resolved_temp
            if temperature is not None
            else explicit.temperature,
            max_tokens=max_tokens if max_tokens is not None else explicit.max_tokens,
            kwargs=explicit.kwargs,
        )

    if isinstance(explicit, Provider):
        return _finalise(explicit, _DEFAULT_MODELS[explicit])

    if isinstance(explicit, str) and explicit:
        # Bare provider slug (no slash) — e.g. ``"gemini"``. This has to
        # come before :func:`_parse_provider_model_slug` because that
        # helper treats any no-slash value as a model name.
        try:
            bare = Provider(explicit.strip().lower())
        except ValueError:
            bare = None
        if bare is not None:
            return _finalise(bare, _DEFAULT_MODELS[bare])

        provider_from_slug, model_from_slug = _parse_provider_model_slug(explicit)
        if provider_from_slug is not None and model_from_slug:
            return _finalise(provider_from_slug, model_from_slug)
        if provider_from_slug is not None:
            return _finalise(provider_from_slug, _DEFAULT_MODELS[provider_from_slug])

    for env_var in (_task_env_var(task_enum), "AGENT_MODEL_DEFAULT"):
        raw = source.get(env_var, "").strip()
        if not raw:
            continue
        provider_from_slug, model_from_slug = _parse_provider_model_slug(raw)
        if provider_from_slug is not None and model_from_slug:
            return _finalise(provider_from_slug, model_from_slug)
        # No slash: treat as a bare model name — pair with the
        # configured task-default provider (or a chain fallback).
        task_provider = _DEFAULT_TASK_PROVIDER.get(task_enum, Provider.ANTHROPIC)
        if has_credentials(task_provider, env):
            return _finalise(task_provider, model_from_slug or _DEFAULT_MODELS[task_provider])

    chain_override = source.get("AGENT_PROVIDER", "").strip().lower()
    if chain_override:
        try:
            provider = Provider(chain_override)
        except ValueError:
            provider = None
        if provider is not None:
            return _finalise(provider, _DEFAULT_MODELS[provider])

    default_for_task = _DEFAULT_TASK_PROVIDER.get(task_enum, Provider.ANTHROPIC)
    if has_credentials(default_for_task, env):
        return _finalise(default_for_task, _DEFAULT_MODELS[default_for_task])

    available = select_available_provider(env=env)
    if available is not None:
        return _finalise(available, _DEFAULT_MODELS[available])

    raise RuntimeError(
        "No provider credentials available. Set one of "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY/GEMINI_API_KEY, "
        "or pass an explicit model name to PropertyAgent."
    )


# ---------------------------------------------------------------------------
# Cache-shape routing
# ---------------------------------------------------------------------------


CacheShape = Literal["anthropic_block", "plain_string"]


def cache_shape_for(provider: Provider) -> CacheShape:
    """Return the system-prompt shape that enables caching on ``provider``.

    * :attr:`Provider.ANTHROPIC`  — emits an Anthropic content-block
      list tagged ``cache_control={"type": "ephemeral"}``.
    * :attr:`Provider.OPENAI`     — OpenAI caches any prefix ≥1024
      tokens automatically (no schema), so we send a plain string.
    * :attr:`Provider.GEMINI`     — Gemini's implicit cache needs no
      schema either. Explicit caches (``caches.create``) are orthogonal
      and a caller-level concern, not something the SystemMessage
      shape expresses.
    """

    if provider == Provider.ANTHROPIC:
        return "anthropic_block"
    return "plain_string"


# ---------------------------------------------------------------------------
# Model builders (lazy imports keep LLM SDKs optional)
# ---------------------------------------------------------------------------


def build_chat_model(spec: ProviderSpec) -> BaseChatModel:
    """Instantiate the LangChain chat model for ``spec``.

    The concrete wrapper class comes from an optional dependency group:

    * ``langchain-anthropic`` (``uk-property-agent[anthropic]``)
    * ``langchain-openai``    (``uk-property-agent[openai]``)
    * ``langchain-google-genai`` (``uk-property-agent[gemini]``)

    If the backing package isn't installed we raise a concise
    :class:`ImportError` that names the install extra — much easier to
    act on than a bare ``ModuleNotFoundError`` from deep inside
    LangGraph.
    """

    if spec.provider == Provider.ANTHROPIC:
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ImportError(
                "langchain-anthropic not installed. Install with: "
                "uv pip install 'uk-property-agent[anthropic]'"
            ) from exc
        return ChatAnthropic(
            model=spec.model,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens or 4096,
            **(spec.kwargs or {}),
        )

    if spec.provider == Provider.OPENAI:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ImportError(
                "langchain-openai not installed. Install with: "
                "uv pip install 'uk-property-agent[openai]'"
            ) from exc
        return ChatOpenAI(
            model=spec.model,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
            **(spec.kwargs or {}),
        )

    if spec.provider == Provider.GEMINI:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise ImportError(
                "langchain-google-genai not installed. Install with: "
                "uv pip install 'uk-property-agent[gemini]'"
            ) from exc
        return ChatGoogleGenerativeAI(
            model=spec.model,
            temperature=spec.temperature,
            max_output_tokens=spec.max_tokens,
            **(spec.kwargs or {}),
        )

    raise ValueError(f"Unsupported provider: {spec.provider!r}")


def describe_env(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return a JSON-safe summary of what the agent can detect.

    Useful for CLI ``env`` commands and log-time diagnostics — never
    exposes key values, just presence + which env var won.
    """

    source = env if env is not None else os.environ
    providers: dict[str, dict[str, Any]] = {}
    for provider in Provider:
        present_vars = [v for v in _API_KEY_ENV[provider] if source.get(v)]
        providers[provider.value] = {
            "available": bool(present_vars),
            "env_vars_set": present_vars,
            "default_model": _DEFAULT_MODELS[provider],
        }
    chain = _default_chain(env)
    selected = select_available_provider(preferred=chain, env=env)
    return {
        "chain": [p.value for p in chain],
        "selected": selected.value if selected else None,
        "providers": providers,
        "task_env_vars": [_task_env_var(t) for t in TaskKind],
    }


__all__ = [
    "CacheShape",
    "Provider",
    "ProviderSpec",
    "TaskKind",
    "build_chat_model",
    "cache_shape_for",
    "describe_env",
    "has_credentials",
    "resolve_provider",
    "select_available_provider",
]
