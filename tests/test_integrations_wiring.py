"""Tests for cross-workflow integration wiring.

Verifies that:
1. Zoom notes ingestion auto-extracts tasks to the task board
2. Risk creation auto-creates mitigation tasks
3. Delegation auto-creates board tasks
4. Dashboard task board endpoints work
5. CLI task board commands are registered
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.task_board import TaskBoard, TaskSource, TaskStatus
from tpllm.workflows.task_board_engine import TaskBoardEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


# ── Zoom → Task Board auto-extraction ───────────────────────────────


class TestZoomTaskBoardIntegration:
    """Verify ingest_zoom_notes auto-extracts tasks onto the board."""

    def test_ingest_auto_creates_tasks_from_action_items(self):
        store = _make_store()
        from tpllm.tools.zoom_tools import get_zoom_tools

        tools = {t["name"]: t["executor"] for t in get_zoom_tools(store)}

        result_str = tools["ingest_zoom_notes"](
            program_id="proj-1",
            meeting_id="ZM-001",
            provider="zoom_ai",
            raw_notes="Sprint planning meeting.",
            summary="Sprint planning",
            action_items=[
                {"owner": "Alice", "description": "Review API spec by Friday"},
                {"owner": "Bob", "description": "Set up CI pipeline"},
            ],
            key_decisions=["Use PostgreSQL for the data layer"],
            follow_ups=[
                {"person": "Charlie", "action": "Check licensing requirements"},
            ],
        )
        result = json.loads(result_str)

        assert result["tasks_extracted"] > 0
        assert len(result["task_ids"]) == result["tasks_extracted"]
        assert result["action_items_count"] == 2
        assert result["decisions_count"] == 1

        # Verify tasks are on the board
        engine = TaskBoardEngine(store)
        board = engine._load("proj-1")
        assert len(board.tasks) > 0

        # Check task sources
        zoom_tasks = [t for t in board.tasks if t.source == TaskSource.ZOOM_AI_NOTES]
        meeting_tasks = [t for t in board.tasks if t.source == TaskSource.MEETING_ACTION]
        assert len(zoom_tasks) >= 2  # 2 action items
        assert len(meeting_tasks) >= 1  # follow-ups + decisions

    def test_ingest_with_no_items_extracts_nothing(self):
        store = _make_store()
        from tpllm.tools.zoom_tools import get_zoom_tools

        tools = {t["name"]: t["executor"] for t in get_zoom_tools(store)}

        result_str = tools["ingest_zoom_notes"](
            program_id="proj-1",
            meeting_id="ZM-002",
            provider="manual",
            raw_notes="",
        )
        result = json.loads(result_str)
        assert result["tasks_extracted"] == 0

    def test_ingest_message_mentions_auto_extraction(self):
        store = _make_store()
        from tpllm.tools.zoom_tools import get_zoom_tools

        tools = {t["name"]: t["executor"] for t in get_zoom_tools(store)}

        result_str = tools["ingest_zoom_notes"](
            program_id="proj-1",
            meeting_id="ZM-003",
            action_items=[{"owner": "Dan", "description": "Write unit tests"}],
        )
        result = json.loads(result_str)
        assert "auto-extracted" in result["message"]
        assert "task board" in result["message"]


# ── Risk → Task Board auto-creation ─────────────────────────────────


class TestRiskTaskBoardIntegration:
    """Verify add_risk with mitigation auto-creates a board task."""

    def test_risk_with_mitigation_creates_task(self):
        store = _make_store()
        from tpllm.tools.project_tools import get_project_tools

        tools = {t["name"]: t["executor"] for t in get_project_tools(store)}

        # First create a program
        tools["create_program"](
            program_id="proj-risk",
            name="Risk Test Program",
            goal="Test risk-task integration",
        )

        # Add a risk with mitigation
        tools["add_risk"](
            program_id="proj-risk",
            title="Database migration may fail",
            description="Complex schema changes",
            severity="HIGH",
            mitigation="Run migration in staging first",
            owner="Alice",
        )

        # Check that a mitigation task was created
        engine = TaskBoardEngine(store)
        board = engine._load("proj-risk")
        assert len(board.tasks) == 1

        task = board.tasks[0]
        assert "Mitigate risk" in task.title
        assert task.source == TaskSource.RISK_MITIGATION
        assert task.assignee == "Alice"
        assert task.priority.value == "HIGH"

    def test_risk_without_mitigation_skips_task(self):
        store = _make_store()
        from tpllm.tools.project_tools import get_project_tools

        tools = {t["name"]: t["executor"] for t in get_project_tools(store)}

        tools["create_program"](
            program_id="proj-risk2",
            name="Risk Test 2",
            goal="Test no mitigation",
        )

        # Add a risk without mitigation
        tools["add_risk"](
            program_id="proj-risk2",
            title="Minor latency issue",
            severity="LOW",
        )

        # No board task should be created
        engine = TaskBoardEngine(store)
        board = engine._load("proj-risk2")
        assert len(board.tasks) == 0


# ── Delegation → Task Board auto-creation ────────────────────────────


class TestDelegationTaskBoardIntegration:
    """Verify delegate_task auto-creates a board task."""

    def test_delegation_creates_board_task(self):
        store = _make_store()
        from tpllm.tools.delegation_tools import get_delegation_tools

        tools = {t["name"]: t["executor"] for t in get_delegation_tools(store)}

        result_str = tools["delegate_task"](
            program_id="proj-del",
            title="Review security audit findings",
            description="Analyze the latest penetration test results",
            category="REVIEW",
            priority="P1",
            assigned_to="Security Team Lead",
            due_date="2026-03-15",
        )

        # Verify board task was created
        engine = TaskBoardEngine(store)
        board = engine._load("proj-del")
        assert len(board.tasks) == 1

        task = board.tasks[0]
        assert task.title == "Review security audit findings"
        assert task.assignee == "Security Team Lead"
        assert task.priority.value == "HIGH"  # P1 → HIGH
        assert task.due_date is not None

    def test_delegation_priority_mapping(self):
        store = _make_store()
        from tpllm.tools.delegation_tools import get_delegation_tools

        tools = {t["name"]: t["executor"] for t in get_delegation_tools(store)}

        # P0 → CRITICAL
        tools["delegate_task"](
            program_id="proj-p0",
            title="Emergency fix",
            priority="P0",
            assigned_to="Eng",
        )
        engine = TaskBoardEngine(store)
        board = engine._load("proj-p0")
        assert board.tasks[0].priority.value == "CRITICAL"

    def test_risk_mitigation_delegation_creates_task(self):
        store = _make_store()
        from tpllm.tools.delegation_tools import get_delegation_tools

        tools = {t["name"]: t["executor"] for t in get_delegation_tools(store)}

        tools["delegate_task"](
            program_id="proj-rmit",
            title="Mitigate vendor lock-in risk",
            category="RISK_MITIGATION",
            priority="P2",
            assigned_to="Platform team",
        )
        engine = TaskBoardEngine(store)
        board = engine._load("proj-rmit")
        assert board.tasks[0].source == TaskSource.RISK_MITIGATION


# ── Dashboard Task Board Endpoints ──────────────────────────────────


class TestDashboardTaskBoardEndpoints:
    """Verify task board API endpoints exist and work."""

    @pytest.fixture()
    def client(self, tmp_path):
        from tpllm.config import TPLLMConfig
        from tpllm.dashboard import create_app

        cfg = TPLLMConfig(data_dir=tmp_path / "data")
        cfg.ensure_data_dir()
        app = create_app(cfg)

        from fastapi.testclient import TestClient
        return TestClient(app), cfg

    def test_get_tasks_empty(self, client):
        tc, cfg = client
        resp = tc.get("/api/programs/test-prog/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["tasks"] == []

    def test_get_tasks_with_data(self, client):
        tc, cfg = client
        store = ArtifactStore(cfg.data_dir)
        engine = TaskBoardEngine(store)
        engine.create_task("test-prog", title="Build feature X", assignee="Alice", priority="HIGH")
        engine.create_task("test-prog", title="Fix bug Y", assignee="Bob", priority="CRITICAL")

        resp = tc.get("/api/programs/test-prog/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_get_tasks_filter_by_assignee(self, client):
        tc, cfg = client
        store = ArtifactStore(cfg.data_dir)
        engine = TaskBoardEngine(store)
        engine.create_task("test-prog", title="Task A", assignee="Alice")
        engine.create_task("test-prog", title="Task B", assignee="Bob")

        resp = tc.get("/api/programs/test-prog/tasks?assignee=Alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["tasks"][0]["assignee"] == "Alice"

    def test_get_tasks_filter_by_status(self, client):
        tc, cfg = client
        store = ArtifactStore(cfg.data_dir)
        engine = TaskBoardEngine(store)
        engine.create_task("test-prog", title="Backlog task")
        engine.create_task("test-prog", title="Sprint task", sprint_id="spr-1")

        resp = tc.get("/api/programs/test-prog/tasks?status=TODO")
        data = resp.json()
        assert data["total"] == 1
        assert data["tasks"][0]["status"] == "TODO"

    def test_get_task_board_view(self, client):
        tc, cfg = client
        store = ArtifactStore(cfg.data_dir)
        engine = TaskBoardEngine(store)
        engine.create_task("test-prog", title="Something")

        resp = tc.get("/api/programs/test-prog/tasks/board")
        assert resp.status_code == 200
        assert "board" in resp.json()

    def test_get_task_workload(self, client):
        tc, cfg = client
        resp = tc.get("/api/programs/test-prog/tasks/workload")
        assert resp.status_code == 200

    def test_get_sprints_empty(self, client):
        tc, cfg = client
        resp = tc.get("/api/programs/test-prog/sprints")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sprints"] == []
        assert data["active_sprint"] is None

    def test_get_sprints_with_data(self, client):
        tc, cfg = client
        store = ArtifactStore(cfg.data_dir)
        engine = TaskBoardEngine(store)
        engine.create_sprint("test-prog", "Sprint 1", "2026-03-01", "2026-03-14", goal="Ship v1")

        resp = tc.get("/api/programs/test-prog/sprints")
        data = resp.json()
        assert len(data["sprints"]) == 1
        assert data["sprints"][0]["name"] == "Sprint 1"
        assert data["sprints"][0]["goal"] == "Ship v1"

    def test_get_sprint_metrics(self, client):
        tc, cfg = client
        store = ArtifactStore(cfg.data_dir)
        engine = TaskBoardEngine(store)
        sprint = engine.create_sprint("test-prog", "S1", "2026-03-01", "2026-03-14")
        engine.start_sprint("test-prog", sprint.id)

        resp = tc.get(f"/api/programs/test-prog/sprints/{sprint.id}/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sprint_name"] == "S1"


# ── CLI Command Registration ────────────────────────────────────────


class TestCLITaskBoardCommands:
    """Verify task board CLI commands are registered."""

    def test_tasks_command_exists(self):
        from tpllm.main import cli
        assert "tasks" in cli.commands

    def test_sprints_command_exists(self):
        from tpllm.main import cli
        assert "sprints" in cli.commands

    def test_workload_command_exists(self):
        from tpllm.main import cli
        assert "workload" in cli.commands

    def test_tasks_command_runs(self, tmp_path):
        from click.testing import CliRunner
        from tpllm.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["tasks", "test-prog"], env={
            "TPLLM_DATA_DIR": str(tmp_path / "data"),
        })
        assert result.exit_code == 0

    def test_sprints_command_runs(self, tmp_path):
        from click.testing import CliRunner
        from tpllm.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["sprints", "test-prog"], env={
            "TPLLM_DATA_DIR": str(tmp_path / "data"),
        })
        assert result.exit_code == 0
