"""Orchestrator tools — multi-agent coordination via Agent SDK patterns."""

from __future__ import annotations

import json
from typing import Any

from tpllm.workflows.agent_orchestrator import AgentOrchestrator


def get_orchestrator_tools() -> list[dict[str, Any]]:
    orchestrator = AgentOrchestrator()

    def route_to_agent(
        task_type: str,
        description: str,
    ) -> str:
        task = orchestrator.route_task(task_type, description)
        result = orchestrator.execute_task(task)
        return json.dumps({
            "task_id": result.id,
            "agent": result.result.get("agent", ""),
            "role": result.result.get("role", ""),
            "tools_available": result.result.get("tools_available", []),
            "routing_reason": result.result.get("routing_reason", ""),
            "status": result.status,
        })

    def list_agents() -> str:
        agents = orchestrator.list_agents()
        return json.dumps({"agents": agents})

    def get_deployment_config() -> str:
        config = orchestrator.get_deployment_config()
        return json.dumps(config, indent=2)

    def get_orchestrator_summary() -> str:
        return orchestrator.get_summary()

    return [
        {
            "name": "route_to_agent",
            "description": (
                "Route a task to the best specialized sub-agent. "
                "Analyzes task type and delegates to memory, delegation, "
                "calendar, communication, analysis, or scope agent."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_type": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["task_type", "description"],
            },
            "executor": route_to_agent,
        },
        {
            "name": "list_agents",
            "description": "List all configured sub-agents in the multi-agent system.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": list_agents,
        },
        {
            "name": "get_deployment_config",
            "description": "Generate Agent SDK deployment configuration for the multi-agent system.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": get_deployment_config,
        },
        {
            "name": "get_orchestrator_summary",
            "description": "Get status summary of the multi-agent orchestrator.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": get_orchestrator_summary,
        },
    ]
