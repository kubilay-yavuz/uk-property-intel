"""High-level ``PropertyAgent`` wrapping a LangGraph ReAct agent.

The agent wires a chat model + UK property tools into a graph that loops:
``llm -> tool_node -> llm -> ...`` until the model returns a final message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from uk_property_agent.prompts import SYSTEM_PROMPT
from uk_property_agent.tools import ToolContext, build_tools


class PropertyAgent:
    """Natural-language UK property analyst.

    Parameters
    ----------
    model:
        Any ``BaseChatModel`` with tool-calling (e.g.
        ``langchain_anthropic.ChatAnthropic(model="claude-sonnet-4")``).
    tool_context:
        Optional :class:`ToolContext` to inject custom factories. Defaults to
        production factories built from environment variables.
    system_prompt:
        Override the default system prompt.
    """

    def __init__(
        self,
        *,
        model: BaseChatModel,
        tool_context: ToolContext | None = None,
        system_prompt: str | None = None,
    ) -> None:
        from langgraph.prebuilt import create_react_agent

        self._tools = build_tools(tool_context)
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        self._graph = create_react_agent(
            model=model,
            tools=self._tools,
            prompt=SystemMessage(content=self._system_prompt),
        )

    @property
    def graph(self) -> Any:
        """The compiled LangGraph. Exposed for LangGraph dev tools."""
        return self._graph

    def tools(self) -> list[Any]:
        """Return the list of tools bound to this agent."""
        return list(self._tools)

    async def ainvoke(self, question: str) -> str:
        """Run the agent end-to-end and return the final answer string."""
        final = await self._graph.ainvoke(self._initial_state(question))
        return self._last_ai_text(final.get("messages", []))

    async def astream(self, question: str) -> AsyncIterator[dict[str, Any]]:
        """Yield LangGraph event chunks for a question."""
        async for chunk in self._graph.astream(self._initial_state(question)):
            yield chunk

    def _initial_state(self, question: str) -> dict[str, Any]:
        return {"messages": [HumanMessage(content=question)]}

    @staticmethod
    def _last_ai_text(messages: list[BaseMessage]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                content = msg.content
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts: list[str] = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            parts.append(str(part.get("text", "")))
                        elif isinstance(part, str):
                            parts.append(part)
                    return "".join(parts)
        return ""


__all__ = ["PropertyAgent", "ToolContext"]
