"""Tests for Agent Orchestrator (Multi-Agent SDK)."""

from tpllm.workflows.agent_orchestrator import AgentOrchestrator, AgentRole


def test_route_memory_task():
    orch = AgentOrchestrator()
    task = orch.route_task("context_reconstruction", "Rebuild program context")

    assert task.agent_role == AgentRole.MEMORY
    assert task.task_type == "context_reconstruction"


def test_route_delegation_task():
    orch = AgentOrchestrator()
    task = orch.route_task("delegate_from_meeting", "Assign action items from standup")

    assert task.agent_role == AgentRole.DELEGATION


def test_route_calendar_task():
    orch = AgentOrchestrator()
    task = orch.route_task("schedule_review", "Schedule design review meeting")

    assert task.agent_role == AgentRole.CALENDAR


def test_route_communication_task():
    orch = AgentOrchestrator()
    task = orch.route_task("send_briefing", "Send weekly briefing to exec team")

    assert task.agent_role == AgentRole.COMMUNICATION


def test_route_analysis_task():
    orch = AgentOrchestrator()
    task = orch.route_task("analyze_transcript", "Process sprint planning transcript")

    assert task.agent_role == AgentRole.ANALYSIS


def test_route_scope_task():
    orch = AgentOrchestrator()
    task = orch.route_task("scope_change_request", "Review scope change for dark mode")

    assert task.agent_role == AgentRole.SCOPE


def test_route_unknown_to_orchestrator():
    orch = AgentOrchestrator()
    task = orch.route_task("unknown_task", "Something generic")

    assert task.agent_role == AgentRole.ORCHESTRATOR


def test_execute_task():
    orch = AgentOrchestrator()
    task = orch.route_task("memory_gap", "Fill context gaps")
    result = orch.execute_task(task)

    assert result.status == "completed"
    assert result.result["agent"] == "Memory Agent"
    assert "tools_available" in result.result


def test_list_agents():
    orch = AgentOrchestrator()
    agents = orch.list_agents()

    assert len(agents) == 6  # memory, delegation, calendar, communication, analysis, scope
    roles = {a["role"] for a in agents}
    assert "memory" in roles
    assert "delegation" in roles
    assert "calendar" in roles
    assert "communication" in roles
    assert "analysis" in roles
    assert "scope" in roles


def test_deployment_config():
    orch = AgentOrchestrator()
    config = orch.get_deployment_config()

    assert config["name"] == "TPLLM Multi-Agent System"
    assert len(config["agents"]) == 6
    assert "routing_rules" in config
    assert "integration_endpoints" in config
    assert "/webhooks/slack" in config["integration_endpoints"]["slack_webhook"]


def test_get_summary():
    orch = AgentOrchestrator()
    summary = orch.get_summary()

    assert "Agent Orchestrator" in summary
    assert "Active agents: 6" in summary


def test_get_agent_config():
    orch = AgentOrchestrator()

    memory = orch.get_agent_config("memory")
    assert memory is not None
    assert memory.name == "Memory Agent"
    assert len(memory.tools) > 0

    none = orch.get_agent_config("nonexistent")
    assert none is None
