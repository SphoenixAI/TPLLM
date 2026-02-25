"""Core TPM agent — LLM agentic loop with tool use.

This is the brain of LLTPM. It takes a user request, sends it to an LLM
with the full TPM system prompt and tool definitions, then loops until
the model has finished executing all necessary tools and produced a final
response.

Supports two provider backends:
  - "anthropic" — native Anthropic SDK (tool_use, extended thinking)
  - "openai"    — OpenAI-compatible chat/completions (DeepSeek, Gemini,
                  Together, Fireworks, Ollama, etc.)
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Generator

import anthropic

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import PROVIDER_ANTHROPIC, PROVIDER_OPENAI, LLTPMConfig
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
    """The AI TPM agent — works with Anthropic or OpenAI-compatible APIs."""

    def __init__(self, config: LLTPMConfig) -> None:
        self._config = config
        self._store = ArtifactStore(config.ensure_data_dir())
        self._registry = build_tool_registry(config, self._store)
        self._messages: list[dict[str, Any]] = []
        self._max_turns = 25  # safety limit

        if config.provider == PROVIDER_OPENAI:
            self._init_openai_client(config)
        else:
            self._client = anthropic.Anthropic(
                api_key=config.anthropic_api_key or None,
            )

    def _init_openai_client(self, config: LLTPMConfig) -> None:
        """Initialise the OpenAI-compatible client (lazy import)."""
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "OpenAI provider requires the openai package.\n"
                "Install with: pip install lltpm[openai]"
            )
        kwargs: dict[str, Any] = {"api_key": config.openai_api_key or None}
        if config.openai_base_url:
            kwargs["base_url"] = config.openai_base_url
        self._openai_client = OpenAI(**kwargs)

    @property
    def tool_count(self) -> int:
        return len(self._registry.definitions)

    def reset(self) -> None:
        """Clear conversation history."""
        self._messages = []

    def run(self, user_message: str) -> Generator[AgentTurn, None, None]:
        """Run the agent loop for a user message.

        Yields AgentTurn objects as the agent works. The final turn
        contains the model's text response to the user.
        """
        self._messages.append({"role": "user", "content": user_message})

        for _ in range(self._max_turns):
            if self._config.provider == PROVIDER_OPENAI:
                turn, done = self._openai_turn()
            else:
                turn, done = self._anthropic_turn()

            yield turn
            if done:
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

    # ── Anthropic backend ─────────────────────────────────────

    def _anthropic_turn(self) -> tuple[AgentTurn, bool]:
        """Execute one Anthropic API round-trip."""
        response = self._client.messages.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            system=SYSTEM_PROMPT,
            tools=self._registry.definitions,
            messages=self._messages,
            thinking={"type": "adaptive"},
        )

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        self._messages.append({"role": "assistant", "content": response.content})

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)

        if tool_use_blocks:
            for tool_block in tool_use_blocks:
                tool_calls.append({
                    "name": tool_block.name,
                    "input": tool_block.input,
                    "id": tool_block.id,
                })
                result = self._registry.execute(tool_block.name, tool_block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result,
                })
            self._messages.append({"role": "user", "content": tool_results})

        turn = AgentTurn(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        done = response.stop_reason == "end_turn"
        return turn, done

    # ── OpenAI-compatible backend ─────────────────────────────

    def _openai_tools(self) -> list[dict[str, Any]]:
        """Convert Anthropic tool definitions to OpenAI function-calling format."""
        tools = []
        for defn in self._registry.definitions:
            tools.append({
                "type": "function",
                "function": {
                    "name": defn["name"],
                    "description": defn.get("description", ""),
                    "parameters": defn.get("input_schema", {}),
                },
            })
        return tools

    def _openai_messages(self) -> list[dict[str, Any]]:
        """Build OpenAI-format message list from internal state."""
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        for m in self._messages:
            msgs.append(m)
        return msgs

    def _openai_turn(self) -> tuple[AgentTurn, bool]:
        """Execute one OpenAI-compatible API round-trip."""
        response = self._openai_client.chat.completions.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            messages=self._openai_messages(),
            tools=self._openai_tools() or None,
        )

        choice = response.choices[0]
        message = choice.message

        text = message.content or ""
        tool_calls_out: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        # Append assistant message (with any tool_calls) to history
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if text:
            assistant_msg["content"] = text
        if message.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        self._messages.append(assistant_msg)

        # Execute tool calls
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_input = json.loads(tc.function.arguments)
                tool_calls_out.append({
                    "name": tc.function.name,
                    "input": tool_input,
                    "id": tc.id,
                })
                result = self._registry.execute(tc.function.name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })
                # OpenAI expects one "tool" message per call
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        turn = AgentTurn(
            text=text,
            tool_calls=tool_calls_out,
            tool_results=tool_results,
        )
        done = choice.finish_reason != "tool_calls"
        return turn, done
