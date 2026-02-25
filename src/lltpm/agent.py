"""Core TPM agent — LLM tool-use loop with tool use.

This is the brain of LLTPM. It takes a user request, sends it to Claude
with the full TPM system prompt and tool definitions, then loops until
Claude has finished executing all necessary tools and produced a final
response.
"""

from __future__ import annotations

import json
from typing import Any, Generator

import anthropic

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import LLTPMConfig
from lltpm.system_prompt import SYSTEM_PROMPT
from lltpm.tools.registry import ToolRegistry, build_tool_registry


class AgentTurn:
    """One turn of the agent loop — what the model said and did."""

    def __init__(
        self,
        text: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> None:
        self.text = text
        self.tool_calls = tool_calls or []
        self.tool_results = tool_results or []


class TPMAgent:
    """The AI TPM agent using the configured model."""

    def __init__(self, config: LLTPMConfig) -> None:
        self._config = config
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key or None)
        self._store = ArtifactStore(config.ensure_data_dir())
        self._registry = build_tool_registry(config, self._store)
        self._messages: list[dict[str, Any]] = []
        self._max_turns = 25  # safety limit

    @property
    def tool_count(self) -> int:
        return len(self._registry.definitions)

    def reset(self) -> None:
        """Clear conversation history."""
        self._messages = []

    def run(self, user_message: str) -> Generator[AgentTurn, None, None]:
        """Run the agent loop for a user message.

        Yields AgentTurn objects as the agent works. The final turn
        contains Claude's text response to the user.
        """
        self._messages.append({"role": "user", "content": user_message})

        for _ in range(self._max_turns):
            response = self._call_api()
            turn = self._process_response(response)
            yield turn

            # If Claude is done (no more tool calls), we're finished
            if response.stop_reason == "end_turn":
                break
        else:
            yield AgentTurn(text="[Agent reached maximum turn limit]")

    def run_to_completion(self, user_message: str) -> str:
        """Run the agent and return the final text response."""
        final_text = ""
        for turn in self.run(user_message):
            if turn.text:
                final_text = turn.text
        return final_text

    def _call_api(self) -> anthropic.types.Message:
        """Make a single API call to Claude."""
        return self._client.messages.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            system=SYSTEM_PROMPT,
            tools=self._registry.definitions,
            messages=self._messages,
            thinking={"type": "adaptive"},
        )

    def _process_response(self, response: anthropic.types.Message) -> AgentTurn:
        """Process a Claude response — extract text and execute any tool calls."""
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        # Append assistant message to conversation
        self._messages.append({"role": "assistant", "content": response.content})

        # Extract tool use blocks
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # Extract text
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)

        # Execute tools if any
        if tool_use_blocks:
            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input
                tool_calls.append({
                    "name": tool_name,
                    "input": tool_input,
                    "id": tool_block.id,
                })

                # Execute the tool
                result = self._registry.execute(tool_name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result,
                })

            # Send tool results back to Claude
            self._messages.append({"role": "user", "content": tool_results})

        return AgentTurn(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
