"""Agent SDK Orchestrator — multi-agent coordination for easy company integration.

Provides a framework for running TPLLM as a multi-agent system where
specialized sub-agents handle specific domains:
- Memory Agent: context management and gap-filling
- Delegation Agent: work routing and follow-up
- Calendar Agent: scheduling optimization
- Communication Agent: channel-optimized outreach
- Analysis Agent: meeting intelligence and transcript processing

Built on explicit worker and tool patterns for
easy integration into any company's infrastructure.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from tpllm.config import DEFAULT_MODEL


class AgentRole(str, Enum):
    """Specialized agent roles in the multi-agent system."""
    ORCHESTRATOR = "orchestrator"   # coordinates all sub-agents
    MEMORY = "memory"               # context management, gap-filling
    DELEGATION = "delegation"       # work routing, assignment, follow-up
    CALENDAR = "calendar"           # scheduling, availability
    COMMUNICATION = "communication" # channel-optimized messaging
    ANALYSIS = "analysis"           # meeting intel, transcripts, risk
    SCOPE = "scope"                 # scope protection, change control


@dataclass
class AgentConfig:
    """Configuration for a specialized sub-agent."""
    role: AgentRole
    name: str
    model: str = DEFAULT_MODEL  # sub-agents default to the project default
    max_tokens: int = 4096
    tools: list[str] = field(default_factory=list)
    system_prompt_addition: str = ""
    enabled: bool = True


@dataclass
class AgentTask:
    """A task assigned to a sub-agent."""
    id: str = ""
    agent_role: AgentRole = AgentRole.ORCHESTRATOR
    task_type: str = ""
    description: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | running | completed | failed
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    error: str = ""


@dataclass
class OrchestratorState:
    """Current state of the multi-agent orchestrator."""
    session_id: str = ""
    active_agents: list[AgentRole] = field(default_factory=list)
    task_queue: list[AgentTask] = field(default_factory=list)
    completed_tasks: list[AgentTask] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


class AgentOrchestrator:
    """Multi-agent orchestrator for TPLLM.

    Routes tasks to specialized sub-agents based on the task type.
    Compatible with agent-worker patterns for deployment.
    """

    # Default agent configurations
    DEFAULT_AGENTS: list[AgentConfig] = [
        AgentConfig(
            role=AgentRole.MEMORY,
            name="Memory Agent",
            tools=[
                "record_memory_turn", "search_memory", "get_context",
                "add_memory_anchor", "detect_memory_gaps", "reconstruct_context",
            ],
            system_prompt_addition=(
                "You specialize in maintaining persistent context across sessions. "
                "When gaps are detected, proactively query all available data sources "
                "(artifacts, meeting notes, delegation plans, RAID logs, etc.) to "
                "reconstruct accurate context."
            ),
        ),
        AgentConfig(
            role=AgentRole.DELEGATION,
            name="Delegation Agent",
            tools=[
                "delegate_task", "delegate_from_meeting", "delegate_from_risks",
                "update_delegation_status", "get_delegation_summary",
                "create_nudge", "evaluate_nudges", "record_nudge_response",
            ],
            system_prompt_addition=(
                "You specialize in work routing and follow-up. Ensure every "
                "commitment has an owner, deadline, and success criteria. "
                "Use the nudge engine for graduated follow-up."
            ),
        ),
        AgentConfig(
            role=AgentRole.CALENDAR,
            name="Calendar Agent",
            tools=[
                "smart_schedule", "find_optimal_attendees", "block_focus_time",
                "get_meeting_load", "suggest_reschedule",
            ],
            system_prompt_addition=(
                "You specialize in intelligent meeting scheduling. Always consider "
                "attendee availability, engagement patterns, scope relevance, and "
                "meeting load before scheduling. Protect focus time."
            ),
        ),
        AgentConfig(
            role=AgentRole.COMMUNICATION,
            name="Communication Agent",
            tools=[
                "generate_quick_response", "record_engagement",
                "get_outreach_recommendation", "generate_briefing",
                "send_slack_message", "send_gchat_message",
            ],
            system_prompt_addition=(
                "You specialize in channel-optimized communication. Use engagement "
                "profiles to determine the best channel, timing, and format for "
                "each person. Always offer quick-response options."
            ),
        ),
        AgentConfig(
            role=AgentRole.ANALYSIS,
            name="Analysis Agent",
            tools=[
                "analyze_meeting", "analyze_transcript", "ingest_zoom_notes",
                "get_meeting_intel_report", "get_all_commitments",
            ],
            system_prompt_addition=(
                "You specialize in extracting intelligence from meetings, "
                "transcripts, and AI notes. Identify commitments, risks, "
                "decisions, and dependencies. Flag all ambiguity."
            ),
        ),
        AgentConfig(
            role=AgentRole.SCOPE,
            name="Scope Agent",
            tools=[
                "add_scope_item", "lock_scope_item", "freeze_scope",
                "request_scope_change", "approve_scope_change",
                "reject_scope_change", "validate_scope_change",
            ],
            system_prompt_addition=(
                "You specialize in scope protection. Every change request "
                "must have justification, business value, and impact analysis. "
                "Reject changes without proper documentation."
            ),
        ),
    ]

    def __init__(self, agents: list[AgentConfig] | None = None) -> None:
        self._agents = {a.role: a for a in (agents or self.DEFAULT_AGENTS)}
        self._state = OrchestratorState(
            session_id=f"ORCH-{uuid.uuid4().hex[:8]}",
            active_agents=list(self._agents.keys()),
        )

    def route_task(self, task_type: str, description: str, **kwargs: Any) -> AgentTask:
        """Route a task to the appropriate sub-agent.

        Analyzes the task type and delegates to the specialized
        agent best suited to handle it.
        """
        role = self._classify_task(task_type)

        task = AgentTask(
            id=f"TASK-{uuid.uuid4().hex[:8]}",
            agent_role=role,
            task_type=task_type,
            description=description,
            input_data=kwargs,
        )

        self._state.task_queue.append(task)
        return task

    def execute_task(self, task: AgentTask) -> AgentTask:
        """Execute a task using the assigned sub-agent.

        In production with Agent SDK, this would spawn an actual
        sub-agent. Here it records the routing decision.
        """
        task.status = "running"
        agent = self._agents.get(task.agent_role)

        if not agent or not agent.enabled:
            task.status = "failed"
            task.error = f"Agent {task.agent_role.value} not available"
            return task

        task.result = {
            "agent": agent.name,
            "role": agent.role.value,
            "model": agent.model,
            "tools_available": agent.tools,
            "task_type": task.task_type,
            "description": task.description,
            "routing_reason": self._explain_routing(task),
        }
        task.status = "completed"
        task.completed_at = datetime.utcnow()

        self._state.task_queue.remove(task)
        self._state.completed_tasks.append(task)
        return task

    def get_agent_config(self, role: str) -> AgentConfig | None:
        """Get configuration for a specific agent role."""
        try:
            r = AgentRole(role)
            return self._agents.get(r)
        except ValueError:
            return None

    def list_agents(self) -> list[dict[str, Any]]:
        """List all configured agents."""
        return [
            {
                "role": a.role.value,
                "name": a.name,
                "model": a.model,
                "tools": a.tools,
                "enabled": a.enabled,
            }
            for a in self._agents.values()
        ]

    def get_deployment_config(self) -> dict[str, Any]:
        """Generate Agent SDK deployment configuration.

        Returns a configuration dict for a deployment runner to deploy
        the multi-agent system after explicit model configuration.
        """
        return {
            "name": "TPLLM Multi-Agent System",
            "version": "0.1.0",
            "orchestrator": {
                "model": DEFAULT_MODEL,
                "max_tokens": 16384,
            },
            "agents": [
                {
                    "role": a.role.value,
                    "name": a.name,
                    "model": a.model,
                    "max_tokens": a.max_tokens,
                    "tools": a.tools,
                    "system_prompt_addition": a.system_prompt_addition,
                }
                for a in self._agents.values()
            ],
            "routing_rules": {
                "memory_keywords": ["context", "remember", "history", "gap", "snapshot"],
                "delegation_keywords": ["assign", "delegate", "follow up", "nudge", "track"],
                "calendar_keywords": ["schedule", "meeting", "availability", "calendar"],
                "communication_keywords": ["send", "notify", "brief", "update", "message"],
                "analysis_keywords": ["analyze", "transcript", "meeting notes", "extract"],
                "scope_keywords": ["scope", "change request", "freeze", "baseline"],
            },
            "integration_endpoints": {
                "slack_webhook": "/webhooks/slack",
                "gchat_webhook": "/webhooks/gchat",
                "zoom_webhook": "/webhooks/zoom",
                "github_webhook": "/webhooks/github",
            },
        }

    def get_summary(self) -> str:
        """Get orchestrator status summary."""
        lines = [
            "# Agent Orchestrator",
            f"Session: {self._state.session_id}",
            f"Active agents: {len(self._state.active_agents)}",
            f"Pending tasks: {len(self._state.task_queue)}",
            f"Completed tasks: {len(self._state.completed_tasks)}",
            "",
            "## Agents",
        ]
        for agent in self._agents.values():
            status = "active" if agent.enabled else "disabled"
            lines.append(f"  {agent.name} ({agent.role.value}): {status} — {len(agent.tools)} tools")
        return "\n".join(lines)

    # ── Task classification ───────────────────────────────────

    def _classify_task(self, task_type: str) -> AgentRole:
        """Classify a task type to the appropriate agent role."""
        task_lower = task_type.lower()

        routing = {
            AgentRole.MEMORY: ["context", "remember", "memory", "history", "gap", "snapshot", "anchor"],
            AgentRole.DELEGATION: ["delegate", "assign", "nudge", "follow_up", "track", "status"],
            AgentRole.CALENDAR: ["schedule", "calendar", "availability", "meeting_time", "focus_time", "reschedule"],
            AgentRole.COMMUNICATION: ["send", "notify", "brief", "message", "update", "engagement"],
            AgentRole.ANALYSIS: ["analyze", "transcript", "meeting_notes", "extract", "zoom", "intelligence"],
            AgentRole.SCOPE: ["scope", "change_request", "freeze", "baseline", "lock"],
        }

        for role, keywords in routing.items():
            if any(kw in task_lower for kw in keywords):
                return role

        return AgentRole.ORCHESTRATOR

    def _explain_routing(self, task: AgentTask) -> str:
        """Explain why a task was routed to a specific agent."""
        role_descriptions = {
            AgentRole.MEMORY: "Task involves context management or memory operations",
            AgentRole.DELEGATION: "Task involves work assignment or follow-up",
            AgentRole.CALENDAR: "Task involves scheduling or availability",
            AgentRole.COMMUNICATION: "Task involves messaging or notifications",
            AgentRole.ANALYSIS: "Task involves meeting or transcript analysis",
            AgentRole.SCOPE: "Task involves scope management or change control",
            AgentRole.ORCHESTRATOR: "Task requires orchestrator-level coordination",
        }
        return role_descriptions.get(task.agent_role, "General task")
