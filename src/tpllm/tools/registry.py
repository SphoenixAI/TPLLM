"""Tool registry — maps tool names to definitions and executors.

Each tool is a Claude API tool definition (JSON schema) paired with a
Python function that executes the tool when Claude calls it.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from tpllm.artifacts.store import ArtifactStore
from tpllm.config import TPLLMConfig
from tpllm.tools.project_tools import get_project_tools
from tpllm.tools.tracking_tools import get_tracking_tools
from tpllm.tools.communication_tools import get_communication_tools
from tpllm.tools.github_tools import get_github_tools
from tpllm.tools.analytics_tools import get_analytics_tools
from tpllm.tools.calendar_tools import get_calendar_tools
from tpllm.tools.gchat_tools import get_gchat_tools
from tpllm.tools.meeting_intel_tools import get_meeting_intel_tools
from tpllm.tools.delegation_tools import get_delegation_tools
from tpllm.tools.nudge_tools import get_nudge_tools
from tpllm.tools.engagement_tools import get_engagement_tools
from tpllm.tools.scope_tools import get_scope_tools
from tpllm.tools.memory_tools import get_memory_tools
from tpllm.tools.smart_calendar_tools import get_smart_calendar_tools
from tpllm.tools.zoom_tools import get_zoom_tools
from tpllm.tools.webhook_tools import get_webhook_tools
from tpllm.tools.orchestrator_tools import get_orchestrator_tools
from tpllm.tools.document_tools import get_document_tools
from tpllm.tools.prediction_tools import get_prediction_tools
from tpllm.tools.security_tools import get_security_tools
from tpllm.tools.portfolio_tools import get_portfolio_tools
from tpllm.tools.trend_tools import get_trend_tools
from tpllm.tools.approval_tools import get_approval_tools
from tpllm.tools.notification_tools import get_notification_tools
from tpllm.tools.email_tools import get_email_tools
from tpllm.tools.slack_bot_tools import get_slack_bot_tools
from tpllm.tools.voice_tools import get_voice_tools
from tpllm.tools.gdrive_tools import get_gdrive_tools
from tpllm.tools.pdf_tools import get_pdf_tools
from tpllm.tools.linear_tools import get_linear_tools
from tpllm.tools.notion_tools import get_notion_tools
from tpllm.tools.task_board_tools import get_task_board_tools


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


def build_tool_registry(config: TPLLMConfig, store: ArtifactStore) -> ToolRegistry:
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

    # Always available — nudge engine tools
    for tool in get_nudge_tools(store):
        registry.register(**tool)

    # Always available — engagement, quick response, briefing, transcript tools
    for tool in get_engagement_tools(store):
        registry.register(**tool)

    # Always available — scope lock tools
    for tool in get_scope_tools(store):
        registry.register(**tool)

    # Always available — persistent memory tools
    for tool in get_memory_tools(store):
        registry.register(**tool)

    # Always available — smart calendar tools
    for tool in get_smart_calendar_tools(store):
        registry.register(**tool)

    # Always available — Zoom meeting tools
    for tool in get_zoom_tools(store):
        registry.register(**tool)

    # Always available — webhook event processing tools
    for tool in get_webhook_tools(store):
        registry.register(**tool)

    # Always available — multi-agent orchestrator tools
    for tool in get_orchestrator_tools():
        registry.register(**tool)

    # Always available — document generation tools
    for tool in get_document_tools(store):
        registry.register(**tool)

    # Always available — risk prediction and evaluation tools
    for tool in get_prediction_tools(store):
        registry.register(**tool)

    # Always available — security and auth tools
    for tool in get_security_tools(store):
        registry.register(**tool)

    # Always available — cross-program intelligence tools
    for tool in get_portfolio_tools(store):
        registry.register(**tool)

    # Always available — historical trend analysis tools
    for tool in get_trend_tools(store):
        registry.register(**tool)

    # Always available — approval workflow tools
    for tool in get_approval_tools(store):
        registry.register(**tool)

    # Always available — notification routing tools
    for tool in get_notification_tools(store):
        registry.register(**tool)

    # Always available — PDF export tools
    for tool in get_pdf_tools(store):
        registry.register(**tool)

    # Conditional — email tools
    if config.email.enabled:
        for tool in get_email_tools(config):
            registry.register(**tool)

    # Conditional — voice/TTS tools
    if config.voice.enabled:
        for tool in get_voice_tools(config):
            registry.register(**tool)

    # Conditional — communication tools (need Slack)
    if config.slack.enabled:
        for tool in get_communication_tools(config):
            registry.register(**tool)

        # Slack bot interactive tools
        for tool in get_slack_bot_tools(config):
            registry.register(**tool)

    # Conditional — Google Chat tools
    if config.gchat.enabled:
        for tool in get_gchat_tools(config):
            registry.register(**tool)

    # Conditional — GitHub tools
    if config.github.enabled:
        for tool in get_github_tools(config):
            registry.register(**tool)

    # Conditional — calendar tools (legacy — use smart_calendar for new scheduling)
    if config.calendar.enabled:
        for tool in get_calendar_tools(config):
            registry.register(**tool)

    # Conditional — Google Drive export tools
    if config.gdrive.enabled:
        for tool in get_gdrive_tools(config, store):
            registry.register(**tool)

    # Always available — intelligent task board tools
    for tool in get_task_board_tools(store):
        registry.register(**tool)

    # Conditional — Notion tools
    if config.notion.enabled:
        for tool in get_notion_tools(config):
            registry.register(**tool)

    # Conditional — Linear tools
    if config.linear.enabled:
        for tool in get_linear_tools(config):
            registry.register(**tool)

    return registry
