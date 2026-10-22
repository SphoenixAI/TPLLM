"""Tests for 7 new features:

1. Webhook → Task Board auto-pipeline
2. Board alerts (for Slack notifications)
3. Task board → status report/document generation
4. Sprint auto-planning engine
5. Cross-program task board intelligence
6. Linear integration (tools + registry)
7. Dashboard HTML frontend + root endpoint
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.task_board import (
    BoardTask,
    Sprint,
    TaskBoard,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from tpllm.workflows.task_board_engine import TaskBoardEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


# ── 1. Webhook → Task Board Auto-Pipeline ───────────────────────────


class TestWebhookTaskBoardPipeline:
    """Verify webhook events auto-trigger task extraction."""

    def test_zoom_meeting_ended_auto_extracts_tasks(self):
        """When a meeting.ended event arrives and notes exist, tasks are extracted."""
        store = _make_store()
        from tpllm.tools.webhook_tools import get_webhook_tools

        tools = {t["name"]: t["executor"] for t in get_webhook_tools(store)}

        # First ingest some Zoom notes so there's data to extract
        from tpllm.tools.zoom_tools import get_zoom_tools
        zoom_tools = {t["name"]: t["executor"] for t in get_zoom_tools(store)}
        zoom_tools["ingest_zoom_notes"](
            program_id="proj-wh",
            meeting_id="ZM-WH-001",
            provider="zoom_ai",
            raw_notes="Sprint planning meeting notes.",
            summary="Sprint planning",
            action_items=[
                {"owner": "Alice", "description": "Fix the auth bug"},
                {"owner": "Bob", "description": "Write API docs"},
            ],
        )

        # Now send meeting.ended webhook
        result_str = tools["ingest_zoom_event"](
            program_id="proj-wh",
            event_type="meeting.ended",
            meeting_id="ZM-WH-001",
            host="Alice",
        )
        result = json.loads(result_str)

        assert "tasks_auto_extracted" in result
        assert result["tasks_auto_extracted"] >= 2
        assert len(result["task_ids"]) == result["tasks_auto_extracted"]

    def test_zoom_recording_completed_auto_extracts(self):
        """recording.completed also triggers task extraction."""
        store = _make_store()
        from tpllm.tools.webhook_tools import get_webhook_tools
        from tpllm.tools.zoom_tools import get_zoom_tools

        zoom_tools = {t["name"]: t["executor"] for t in get_zoom_tools(store)}
        zoom_tools["ingest_zoom_notes"](
            program_id="proj-rec",
            meeting_id="ZM-REC-001",
            provider="manual",
            raw_notes="Review meeting.",
            action_items=[{"owner": "Dan", "description": "Update the spec"}],
        )

        wh_tools = {t["name"]: t["executor"] for t in get_webhook_tools(store)}
        result = json.loads(wh_tools["ingest_zoom_event"](
            program_id="proj-rec",
            event_type="recording.completed",
            meeting_id="ZM-REC-001",
        ))

        assert result["tasks_auto_extracted"] >= 1

    def test_zoom_meeting_started_no_extraction(self):
        """meeting.started should NOT trigger task extraction."""
        store = _make_store()
        from tpllm.tools.webhook_tools import get_webhook_tools

        tools = {t["name"]: t["executor"] for t in get_webhook_tools(store)}
        result = json.loads(tools["ingest_zoom_event"](
            program_id="proj-start",
            event_type="meeting.started",
            meeting_id="ZM-START-001",
        ))
        assert "tasks_auto_extracted" not in result

    def test_zoom_ended_no_notes_no_extraction(self):
        """meeting.ended with no ingested notes should not extract tasks."""
        store = _make_store()
        from tpllm.tools.webhook_tools import get_webhook_tools

        tools = {t["name"]: t["executor"] for t in get_webhook_tools(store)}
        result = json.loads(tools["ingest_zoom_event"](
            program_id="proj-empty",
            event_type="meeting.ended",
            meeting_id="ZM-NONEXIST",
        ))
        assert "tasks_auto_extracted" not in result


# ── 2. Board Alerts (Slack Notifications) ───────────────────────────


class TestBoardAlerts:
    """Verify get_alerts detects overdue, blocked, sprint-at-risk."""

    def test_overdue_alert(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        # Create an overdue task
        engine.create_task(
            "proj-alerts",
            title="Overdue task",
            assignee="Alice",
            due_date=(date.today() - timedelta(days=2)).isoformat(),
        )

        alerts = engine.get_alerts("proj-alerts")
        overdue_alerts = [a for a in alerts if a["type"] == "overdue"]
        assert len(overdue_alerts) == 1
        assert "Overdue" in overdue_alerts[0]["title"]

    def test_blocked_alert(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        t1 = engine.create_task("proj-bl", title="Blocker task")
        t2 = engine.create_task("proj-bl", title="Blocked task")
        engine.update_task("proj-bl", t2.id, status="BLOCKED", blocked_by=[t1.id])

        alerts = engine.get_alerts("proj-bl")
        blocked_alerts = [a for a in alerts if a["type"] == "blocked"]
        assert len(blocked_alerts) == 1
        assert "Blocked" in blocked_alerts[0]["title"]

    def test_sprint_at_risk_alert(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        # Create sprint ending tomorrow with incomplete tasks
        sprint = engine.create_sprint(
            "proj-sar",
            "Sprint X",
            date.today().isoformat(),
            (date.today() + timedelta(days=1)).isoformat(),
        )
        engine.start_sprint("proj-sar", sprint.id)
        engine.create_task("proj-sar", title="Not done 1", sprint_id=sprint.id)
        engine.create_task("proj-sar", title="Not done 2", sprint_id=sprint.id)
        engine.create_task("proj-sar", title="Not done 3", sprint_id=sprint.id)

        alerts = engine.get_alerts("proj-sar")
        risk_alerts = [a for a in alerts if a["type"] == "sprint_at_risk"]
        assert len(risk_alerts) == 1

    def test_no_alerts_for_healthy_board(self):
        store = _make_store()
        engine = TaskBoardEngine(store)
        engine.create_task("proj-healthy", title="Normal task")

        alerts = engine.get_alerts("proj-healthy")
        assert len(alerts) == 0

    def test_alert_tool_returns_formatted_text(self):
        """get_board_alerts tool returns markdown-formatted alerts."""
        store = _make_store()
        from tpllm.tools.task_board_tools import get_task_board_tools

        tools = {t["name"]: t["executor"] for t in get_task_board_tools(store)}

        engine = TaskBoardEngine(store)
        engine.create_task(
            "proj-fmt",
            title="Very overdue",
            due_date=(date.today() - timedelta(days=5)).isoformat(),
        )

        result = tools["get_board_alerts"](program_id="proj-fmt")
        assert "Task Board Alerts" in result
        assert "CRITICAL" in result or "HIGH" in result

    def test_alert_tool_healthy_returns_json(self):
        store = _make_store()
        from tpllm.tools.task_board_tools import get_task_board_tools

        tools = {t["name"]: t["executor"] for t in get_task_board_tools(store)}
        result = json.loads(tools["get_board_alerts"](program_id="proj-ok"))
        assert "healthy" in result["message"].lower()


# ── 3. Task Board → Document / Status Report Generation ─────────────


class TestTaskBoardDocumentGeneration:
    """Verify task board renders as HTML/markdown documents."""

    def test_render_task_board_html(self):
        store = _make_store()
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(store)
        engine = TaskBoardEngine(store)
        engine.create_task("proj-doc", title="Build feature", assignee="Alice", priority="HIGH")
        engine.create_task("proj-doc", title="Fix bug", assignee="Bob", priority="CRITICAL")

        html = gen.render_task_board("proj-doc", fmt="html")
        assert "<html" in html or "<table" in html
        assert "Build feature" in html
        assert "Fix bug" in html
        assert "Alice" in html
        assert "Bob" in html

    def test_render_task_board_empty(self):
        store = _make_store()
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(store)
        result = gen.render_task_board("proj-empty")
        assert "No tasks" in result

    def test_render_task_board_with_sprint(self):
        store = _make_store()
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(store)
        engine = TaskBoardEngine(store)
        sprint = engine.create_sprint(
            "proj-spr-doc", "Sprint 1",
            date.today().isoformat(),
            (date.today() + timedelta(days=14)).isoformat(),
        )
        engine.start_sprint("proj-spr-doc", sprint.id)
        engine.create_task("proj-spr-doc", title="Sprint task", sprint_id=sprint.id)

        html = gen.render_task_board("proj-spr-doc", fmt="html")
        assert "Sprint 1" in html
        assert "Sprint task" in html

    def test_full_report_includes_task_board(self):
        store = _make_store()
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(store)
        engine = TaskBoardEngine(store)
        engine.create_task("proj-full", title="Report task")

        report = gen.render_full_report("proj-full", fmt="html")
        assert "Task Board" in report

    def test_task_board_in_renderable_list(self):
        store = _make_store()
        from tpllm.workflows.document_generator import DocumentGenerator

        gen = DocumentGenerator(store)
        # Need a task board artifact to exist
        engine = TaskBoardEngine(store)
        engine.create_task("proj-render", title="Exists")

        renderable = gen.list_renderable("proj-render")
        doc_artifacts = [d["artifact"] for d in renderable]
        assert "task_board" in doc_artifacts

    def test_dashboard_document_endpoint_task_board(self, tmp_path):
        from tpllm.config import TPLLMConfig
        from tpllm.dashboard import create_app
        from fastapi.testclient import TestClient

        cfg = TPLLMConfig(data_dir=tmp_path / "data")
        cfg.ensure_data_dir()
        app = create_app(cfg)
        tc = TestClient(app)

        # Create a task so there's something to render
        store = ArtifactStore(cfg.data_dir)
        engine = TaskBoardEngine(store)
        engine.create_task("test-doc", title="Dashboard render test")

        resp = tc.get("/api/programs/test-doc/documents/task_board")
        assert resp.status_code == 200
        assert "Dashboard render test" in resp.text


# ── 4. Sprint Auto-Planning Engine ──────────────────────────────────


class TestSprintAutoPlanning:
    """Verify intelligent sprint auto-planning from backlog."""

    def test_basic_auto_plan(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        # Create backlog tasks
        engine.create_task("proj-ap", title="Critical fix", priority="CRITICAL", effort_hours=4)
        engine.create_task("proj-ap", title="High task", priority="HIGH", effort_hours=8)
        engine.create_task("proj-ap", title="Medium task", priority="MEDIUM", effort_hours=4)

        # Create sprint
        sprint = engine.create_sprint(
            "proj-ap", "Sprint Auto",
            date.today().isoformat(),
            (date.today() + timedelta(days=14)).isoformat(),
            team_capacity_hours=40,
        )

        result = engine.auto_plan_sprint("proj-ap", sprint.id)
        assert result["planned"] == 3
        assert result["committed_hours"] == 16.0
        assert result["utilization"] > 0

    def test_auto_plan_respects_capacity(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        # Create tasks exceeding capacity
        for i in range(10):
            engine.create_task("proj-cap", title=f"Task {i}", effort_hours=10)

        sprint = engine.create_sprint(
            "proj-cap", "Sprint Tight",
            date.today().isoformat(),
            (date.today() + timedelta(days=7)).isoformat(),
            team_capacity_hours=30,
        )

        result = engine.auto_plan_sprint("proj-cap", sprint.id)
        assert result["committed_hours"] <= 30
        assert result["planned"] == 3  # 3 x 10h = 30h

    def test_auto_plan_prioritizes_critical_first(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        engine.create_task("proj-pri", title="Low task", priority="LOW", effort_hours=5)
        engine.create_task("proj-pri", title="Critical task", priority="CRITICAL", effort_hours=5)
        engine.create_task("proj-pri", title="Medium task", priority="MEDIUM", effort_hours=5)

        sprint = engine.create_sprint(
            "proj-pri", "Sprint PRI",
            date.today().isoformat(),
            (date.today() + timedelta(days=7)).isoformat(),
            team_capacity_hours=10,
        )

        result = engine.auto_plan_sprint("proj-pri", sprint.id)
        assert result["planned"] == 2
        # Critical should be first
        assert result["tasks"][0]["priority"] == "CRITICAL"

    def test_auto_plan_overdue_tasks_first(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        engine.create_task(
            "proj-od", title="Overdue task", priority="LOW",
            due_date=(date.today() - timedelta(days=3)).isoformat(),
            effort_hours=5,
        )
        engine.create_task("proj-od", title="High task", priority="HIGH", effort_hours=5)

        sprint = engine.create_sprint(
            "proj-od", "Sprint OD",
            date.today().isoformat(),
            (date.today() + timedelta(days=7)).isoformat(),
            team_capacity_hours=5,
        )

        result = engine.auto_plan_sprint("proj-od", sprint.id)
        assert result["planned"] == 1
        assert result["tasks"][0]["title"] == "Overdue task"

    def test_auto_plan_empty_backlog(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        sprint = engine.create_sprint(
            "proj-empty", "Sprint Empty",
            date.today().isoformat(),
            (date.today() + timedelta(days=7)).isoformat(),
        )

        result = engine.auto_plan_sprint("proj-empty", sprint.id)
        assert result["planned"] == 0
        assert "No backlog" in result["message"]

    def test_auto_plan_invalid_sprint(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        result = engine.auto_plan_sprint("proj-x", "nonexistent-sprint")
        assert "error" in result

    def test_auto_plan_tool(self):
        store = _make_store()
        from tpllm.tools.task_board_tools import get_task_board_tools

        tools = {t["name"]: t["executor"] for t in get_task_board_tools(store)}

        engine = TaskBoardEngine(store)
        engine.create_task("proj-tool", title="Backlog item", effort_hours=4)

        sprint = engine.create_sprint(
            "proj-tool", "Sprint T",
            date.today().isoformat(),
            (date.today() + timedelta(days=14)).isoformat(),
            team_capacity_hours=40,
        )

        result = json.loads(tools["auto_plan_sprint"](
            program_id="proj-tool",
            sprint_id=sprint.id,
        ))
        assert result["planned"] == 1


# ── 5. Cross-Program Task Board Intelligence ────────────────────────


class TestCrossProgramTaskBoardIntel:
    """Verify cross-program intel includes task board signals."""

    def test_snapshot_includes_board_blocked(self):
        store = _make_store()
        from tpllm.workflows.cross_program_intel import CrossProgramIntel

        engine = TaskBoardEngine(store)
        t1 = engine.create_task("prog-xp", title="Blocking")
        t2 = engine.create_task("prog-xp", title="Blocked")
        engine.update_task("prog-xp", t2.id, status="BLOCKED", blocked_by=[t1.id])

        intel = CrossProgramIntel(store)
        snapshot = intel._snapshot_program("prog-xp")
        assert snapshot.open_blockers == 1

    def test_snapshot_overdue_turns_health_yellow(self):
        store = _make_store()
        from tpllm.workflows.cross_program_intel import CrossProgramIntel

        engine = TaskBoardEngine(store)
        engine.create_task(
            "prog-od", title="Overdue",
            due_date=(date.today() - timedelta(days=1)).isoformat(),
        )

        intel = CrossProgramIntel(store)
        snapshot = intel._snapshot_program("prog-od")
        assert snapshot.health in ("YELLOW", "RED")

    def test_many_overdue_turns_red(self):
        store = _make_store()
        from tpllm.workflows.cross_program_intel import CrossProgramIntel

        engine = TaskBoardEngine(store)
        for i in range(5):
            engine.create_task(
                "prog-red", title=f"Overdue {i}",
                due_date=(date.today() - timedelta(days=1)).isoformat(),
            )

        intel = CrossProgramIntel(store)
        snapshot = intel._snapshot_program("prog-red")
        assert snapshot.health == "RED"

    def test_resource_conflicts_include_board_tasks(self):
        store = _make_store()
        from tpllm.workflows.cross_program_intel import CrossProgramIntel

        engine = TaskBoardEngine(store)
        # Same person across two programs
        engine.create_task("prog-a", title="Task A1", assignee="Shared Person")
        engine.create_task("prog-a", title="Task A2", assignee="Shared Person")
        engine.create_task("prog-b", title="Task B1", assignee="Shared Person")
        engine.create_task("prog-b", title="Task B2", assignee="Shared Person")

        intel = CrossProgramIntel(store)
        conflicts = intel._detect_resource_conflicts(["prog-a", "prog-b"])
        shared = [c for c in conflicts if c.person == "Shared Person"]
        assert len(shared) == 1
        assert shared[0].total_active_items >= 4


# ── 6. Linear Integration Tools ─────────────────────────────────────


class TestLinearTools:
    """Verify Linear tools are properly structured and registered."""

    def test_linear_tools_structure(self):
        """All 5 tools have name, description, input_schema, executor."""
        from tpllm.config import TPLLMConfig
        cfg = TPLLMConfig()
        # Need to enable linear
        cfg.linear.enabled = True
        cfg.linear.api_key = "test-key"

        from tpllm.tools.linear_tools import get_linear_tools
        tools = get_linear_tools(cfg)

        assert len(tools) == 5
        tool_names = {t["name"] for t in tools}
        assert tool_names == {
            "linear_search_issues",
            "linear_get_issue",
            "linear_create_issue",
            "linear_update_issue",
            "linear_list_teams",
        }

        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t
            assert "executor" in t
            assert callable(t["executor"])

    def test_linear_tools_registered_when_enabled(self):
        """Linear tools appear in registry when config.linear.enabled."""
        from tpllm.config import TPLLMConfig
        from tpllm.tools.registry import build_tool_registry

        cfg = TPLLMConfig()
        cfg.linear.enabled = True
        cfg.linear.api_key = "test-key"
        store = _make_store()

        registry = build_tool_registry(cfg, store)
        names = {d["name"] for d in registry.definitions}
        assert "linear_search_issues" in names
        assert "linear_get_issue" in names
        assert "linear_create_issue" in names
        assert "linear_update_issue" in names
        assert "linear_list_teams" in names

    def test_linear_tools_not_registered_when_disabled(self):
        """Linear tools absent when config.linear.enabled is False."""
        from tpllm.config import TPLLMConfig
        from tpllm.tools.registry import build_tool_registry

        cfg = TPLLMConfig()
        cfg.linear.enabled = False
        store = _make_store()

        registry = build_tool_registry(cfg, store)
        names = {d["name"] for d in registry.definitions}
        assert "linear_search_issues" not in names

    def test_linear_integration_parse_issue(self):
        from tpllm.integrations.linear_integration import LinearIntegration, LinearIssue

        class FakeConfig:
            enabled = True
            api_key = "test"

        integration = LinearIntegration(FakeConfig())
        node = {
            "identifier": "ENG-42",
            "title": "Fix login bug",
            "state": {"name": "In Progress"},
            "assignee": {"name": "Alice"},
            "priority": 2,
            "labels": {"nodes": [{"name": "bug"}, {"name": "auth"}]},
            "url": "https://linear.app/team/ENG-42",
        }
        issue = integration._parse_issue(node)
        assert isinstance(issue, LinearIssue)
        assert issue.id == "ENG-42"
        assert issue.title == "Fix login bug"
        assert issue.state == "In Progress"
        assert issue.assignee == "Alice"
        assert issue.priority == 2
        assert issue.labels == ["bug", "auth"]

    def test_linear_search_requires_enabled(self):
        from tpllm.integrations.linear_integration import LinearIntegration
        from tpllm.integrations.base import IntegrationError

        class FakeConfig:
            enabled = False
            api_key = ""

        integration = LinearIntegration(FakeConfig())
        with pytest.raises(IntegrationError, match="not configured"):
            integration.search_issues("ENG")


# ── 7. Dashboard HTML Frontend ──────────────────────────────────────


class TestDashboardHTMLFrontend:
    """Verify the dashboard HTML frontend and root endpoint."""

    @pytest.fixture()
    def client(self, tmp_path):
        from tpllm.config import TPLLMConfig
        from tpllm.dashboard import create_app
        from fastapi.testclient import TestClient

        cfg = TPLLMConfig(data_dir=tmp_path / "data")
        cfg.ensure_data_dir()
        app = create_app(cfg)
        return TestClient(app)

    def test_root_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_root_contains_dashboard_ui(self, client):
        resp = client.get("/")
        html = resp.text
        assert "<!DOCTYPE html>" in html
        assert "TPLLM" in html
        assert "<script>" in html or "<script " in html

    def test_root_has_program_selector(self, client):
        resp = client.get("/")
        assert "program" in resp.text.lower()

    def test_root_has_task_table(self, client):
        resp = client.get("/")
        # The dashboard should reference tasks
        assert "task" in resp.text.lower()

    def test_alerts_endpoint_exists(self, client, tmp_path):
        # Create a task to ensure board exists
        from tpllm.config import TPLLMConfig
        cfg = TPLLMConfig(data_dir=tmp_path / "data")
        store = ArtifactStore(cfg.data_dir)
        engine = TaskBoardEngine(store)
        engine.create_task("test-alerts", title="Test")

        resp = client.get("/api/programs/test-alerts/tasks/board")
        assert resp.status_code == 200


# ── Additional Wiring Tests ─────────────────────────────────────────


class TestAutoplanAndAlertToolWiring:
    """Ensure auto_plan_sprint and get_board_alerts tools exist in registry."""

    def test_auto_plan_sprint_tool_registered(self):
        from tpllm.config import TPLLMConfig
        from tpllm.tools.registry import build_tool_registry

        cfg = TPLLMConfig()
        store = _make_store()
        registry = build_tool_registry(cfg, store)
        names = {d["name"] for d in registry.definitions}
        assert "auto_plan_sprint" in names
        assert "get_board_alerts" in names

    def test_workload_overload_alert(self):
        store = _make_store()
        engine = TaskBoardEngine(store)

        # Assign many overdue tasks to one person to trigger "overloaded" health
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        for i in range(4):
            engine.create_task(
                "proj-load", title=f"Overdue task {i}",
                assignee="Overloaded Alice", effort_hours=8,
                due_date=yesterday,
            )

        alerts = engine.get_alerts("proj-load")
        workload_alerts = [a for a in alerts if a["type"] == "workload"]
        assert len(workload_alerts) >= 1
        assert "Overloaded Alice" in workload_alerts[0]["title"]
