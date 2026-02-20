"""Tool registry — maps tool names to definitions and executors.

Each tool is a Claude API tool definition (JSON schema) paired with a
Python function that executes the tool when Claude calls it.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import LLTPMConfig
from lltpm.tools.project_tools import get_project_tools
from lltpm.tools.tracking_tools import get_tracking_tools
from lltpm.tools.communication_tools import get_communication_tools
from lltpm.tools.github_tools import get_github_tools
from lltpm.tools.analytics_tools import get_analytics_tools
from lltpm.tools.calendar_tools import get_calendar_tools
from lltpm.tools.gchat_tools import get_gchat_tools
from lltpm.tools.meeting_intel_tools import get_meeting_intel_tools
from lltpm.tools.delegation_tools import get_delegation_tools


class ToolRegistry:
    """Registry of all tools available to the TPM agent."""

    def __init__(self) -> None:
        self._definitions: list[dict[str, Any]] = []
        self._executors: dict[str, Callable[..., str]] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        executor: Callable[..., str],
    ) -> None:
        self._definitions.append({
            "name": name,
            "description": description,
            "input_schema": input_schema,
        })
        self._executors[name] = executor

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return self._definitions

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        executor = self._executors.get(tool_name)
        if executor is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            return executor(**tool_input)
        except Exception as e:
            return json.dumps({"error": str(e)})


def build_tool_registry(config: LLTPMConfig, store: ArtifactStore) -> ToolRegistry:
    """Build the full tool registry with all available tools."""
    registry = ToolRegistry()

    # Always available — program management tools
    for tool in get_project_tools(store):
        registry.register(**tool)

    # Always available — tracking and workflow tools
    for tool in get_tracking_tools(store, config):
        registry.register(**tool)

    # Always available — analytics / pulse tools
    for tool in get_analytics_tools(store):
        registry.register(**tool)

    # Always available — meeting intelligence tools
    for tool in get_meeting_intel_tools(store):
        registry.register(**tool)

    # Always available — delegation tools
    for tool in get_delegation_tools(store):
        registry.register(**tool)

    # Conditional — communication tools (need Slack)
    if config.slack.enabled:
        for tool in get_communication_tools(config):
            registry.register(**tool)

    # Conditional — Google Chat tools
    if config.gchat.enabled:
        for tool in get_gchat_tools(config):
            registry.register(**tool)

    # Conditional — GitHub tools
    if config.github.enabled:
        for tool in get_github_tools(config):
            registry.register(**tool)

    # Conditional — calendar tools
    if config.calendar.enabled:
        for tool in get_calendar_tools(config):
            registry.register(**tool)

    return registry
