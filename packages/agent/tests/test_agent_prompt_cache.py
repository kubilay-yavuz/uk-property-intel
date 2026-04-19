"""Tests for the cacheable system-prompt helper and the agent wiring."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, SystemMessage
from uk_property_agent import (
    CACHEABLE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    PropertyAgent,
    ToolContext,
    build_system_message,
)


class _ToolCallingFakeModel(FakeMessagesListChatModel):
    """Fake chat model accepted by ``create_react_agent`` (no-op bind)."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> _ToolCallingFakeModel:  # type: ignore[override]
        return self


def _fake_model() -> _ToolCallingFakeModel:
    return _ToolCallingFakeModel(responses=[AIMessage(content="ok")])


class TestBuildSystemMessage:
    def test_default_is_cacheable(self) -> None:
        msg = build_system_message()
        assert isinstance(msg, SystemMessage)
        assert isinstance(msg.content, list)
        assert len(msg.content) == 1
        block = msg.content[0]
        assert isinstance(block, dict)
        assert block["type"] == "text"
        assert block["text"].strip().startswith("You are a **UK Property Intelligence Agent**")
        assert block["cache_control"] == {"type": "ephemeral", "ttl": "5m"}

    def test_disable_cache_returns_plain_string(self) -> None:
        msg = build_system_message(enable_cache=False)
        assert isinstance(msg.content, str)
        assert msg.content == CACHEABLE_SYSTEM_PROMPT

    def test_custom_prompt_flows_through(self) -> None:
        custom = "Custom system prompt body."
        msg = build_system_message(custom, enable_cache=True)
        assert msg.content[0]["text"] == custom  # type: ignore[index]
        assert msg.content[0]["cache_control"]["ttl"] == "5m"  # type: ignore[index]

    def test_custom_ttl(self) -> None:
        msg = build_system_message(enable_cache=True, ttl="1h")
        assert msg.content[0]["cache_control"]["ttl"] == "1h"  # type: ignore[index]

    def test_system_prompt_alias_matches_cacheable(self) -> None:
        assert SYSTEM_PROMPT == CACHEABLE_SYSTEM_PROMPT

    def test_prompt_mentions_new_tools(self) -> None:
        assert "drive_time_isochrone" in CACHEABLE_SYSTEM_PROMPT
        assert "transit_isochrone" in CACHEABLE_SYSTEM_PROMPT
        assert "build_property_dossier" in CACHEABLE_SYSTEM_PROMPT


class TestPropertyAgentCacheWiring:
    def test_agent_uses_cacheable_system_message_by_default(self) -> None:
        agent = PropertyAgent(
            model=_fake_model(),
            tool_context=ToolContext(),
        )
        msg = agent.system_message
        assert isinstance(msg.content, list)
        assert msg.content[0]["cache_control"]["type"] == "ephemeral"  # type: ignore[index]

    def test_agent_respects_enable_prompt_cache_false(self) -> None:
        agent = PropertyAgent(
            model=_fake_model(),
            tool_context=ToolContext(),
            enable_prompt_cache=False,
        )
        assert isinstance(agent.system_message.content, str)

    def test_agent_accepts_system_message_override(self) -> None:
        custom = SystemMessage(content="hand-written override")
        agent = PropertyAgent(
            model=_fake_model(),
            tool_context=ToolContext(),
            system_message=custom,
        )
        assert agent.system_message is custom

    def test_agent_passes_custom_ttl_through(self) -> None:
        agent = PropertyAgent(
            model=_fake_model(),
            tool_context=ToolContext(),
            cache_ttl="1h",
        )
        assert agent.system_message.content[0]["cache_control"]["ttl"] == "1h"  # type: ignore[index]
