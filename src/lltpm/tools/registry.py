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
from lltpm.tools.nudge_tools import get_nudge_tools
from lltpm.tools.engagement_tools import get_engagement_tools
from lltpm.tools.scope_tools import get_scope_tools
from lltpm.tools.memory_tools import get_memory_tools
from lltpm.tools.smart_calendar_tools import get_smart_calendar_tools
from lltpm.tools.zoom_tools import get_zoom_tools
from lltpm.tools.webhook_tools import get_webhook_tools
from lltpm.tools.orchestrator_tools import get_orchestrator_tools
from lltpm.tools.document_tools import get_document_tools
from lltpm.tools.prediction_tools import get_prediction_tools
from lltpm.tools.security_tools import get_security_tools
from lltpm.tools.portfolio_tools import get_portfolio_tools
from lltpm.tools.trend_tools import get_trend_tools
from lltpm.tools.approval_tools import get_approval_tools
from lltpm.tools.notification_tools import get_notification_tools
from lltpm.tools.email_tools import get_email_tools
from lltpm.tools.slack_bot_tools import get_slack_bot_tools
from lltpm.tools.voice_tools import get_voice_tools
from lltpm.tools.gdrive_tools import get_gdrive_tools
from lltpm.tools.pdf_tools import get_pdf_tools


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

    return registry
