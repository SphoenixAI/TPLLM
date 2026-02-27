"""Tests for the AI-native Task Board — LLTPM's built-in project tracking."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.task_board import (
    BoardTask,
    Sprint,
    TaskBoard,
    TaskPriority,
    TaskSource,
    TaskStatus,
)
from lltpm.workflows.task_board_engine import TaskBoardEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


# ── Model tests ──────────────────────────────────────────────────

class TestBoardTaskModel:
    def test_default_status(self):
        task = BoardTask(title="Test task")
        assert task.status == TaskStatus.BACKLOG

    def test_is_overdue(self):
        task = BoardTask(title="Late", due_date=date.today() - timedelta(days=1))
        assert task.is_overdue is True

    def test_not_overdue_when_done(self):
        task = BoardTask(
            title="Done",
            due_date=date.today() - timedelta(days=1),
            status=TaskStatus.DONE,
        )
        assert task.is_overdue is False

    def test_not_overdue_no_date(self):
        task = BoardTask(title="No date")
        assert task.is_overdue is False

    def test_is_open(self):
        assert BoardTask(title="Open", status=TaskStatus.TODO).is_open is True
        assert BoardTask(title="Done", status=TaskStatus.DONE).is_open is False
        assert BoardTask(title="Cancelled", status=TaskStatus.CANCELLED).is_open is False

    def test_days_until_due(self):
        task = BoardTask(title="Soon", due_date=date.today() + timedelta(days=3))
        assert task.days_until_due == 3

    def test_days_until_due_none(self):
        task = BoardTask(title="No date")
        assert task.days_until_due is None


class TestSprintModel:
    def test_days_remaining(self):
        sprint = Sprint(
            name="Sprint 1",
            start_date=date.today() - timedelta(days=5),
            end_date=date.today() + timedelta(days=5),
        )
        assert sprint.days_remaining == 5

    def test_over_capacity(self):
        sprint = Sprint(
            name="Sprint 1",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=14),
            team_capacity_hours=40,
            committed_hours=50,
        )
        assert sprint.is_over_capacity is True

    def test_not_over_capacity(self):
        sprint = Sprint(
            name="Sprint 1",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=14),
            team_capacity_hours=40,
            committed_hours=30,
        )
        assert sprint.is_over_capacity is False

    def test_utilization(self):
        sprint = Sprint(
            name="Sprint 1",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=14),
            team_capacity_hours=100,
            committed_hours=75,
        )
        assert sprint.utilization == 0.75


class TestTaskBoardModel:
    def test_tasks_by_status(self):
        board = TaskBoard(tasks=[
            BoardTask(title="A", status=TaskStatus.TODO),
            BoardTask(title="B", status=TaskStatus.TODO),
            BoardTask(title="C", status=TaskStatus.DONE),
        ])
        by_status = board.tasks_by_status()
        assert len(by_status["TODO"]) == 2
        assert len(by_status["DONE"]) == 1

    def test_tasks_by_assignee(self):
        board = TaskBoard(tasks=[
            BoardTask(title="A", assignee="Alice", status=TaskStatus.TODO),
            BoardTask(title="B", assignee="Alice", status=TaskStatus.TODO),
            BoardTask(title="C", assignee="Bob", status=TaskStatus.TODO),
            BoardTask(title="D", status=TaskStatus.DONE),  # excluded
        ])
        by_assignee = board.tasks_by_assignee()
        assert len(by_assignee["Alice"]) == 2
        assert len(by_assignee["Bob"]) == 1
        assert "UNASSIGNED" not in by_assignee  # D is done

    def test_backlog(self):
        board = TaskBoard(tasks=[
            BoardTask(title="A", status=TaskStatus.BACKLOG),
            BoardTask(title="B", status=TaskStatus.TODO),
        ])
        assert len(board.backlog) == 1

    def test_summary_renders(self):
        board = TaskBoard(tasks=[
            BoardTask(title="Fix bug", status=TaskStatus.TODO, priority=TaskPriority.HIGH),
        ])
        summary = board.summary()
        assert "Fix bug" in summary
        assert "Task Board" in summary


# ── Engine tests ─────────────────────────────────────────────────

class TestTaskBoardEngine:
    def setup_method(self):
        self.store = _make_store()
        self.engine = TaskBoardEngine(self.store)

    def test_create_task(self):
        task = self.engine.create_task("prog-1", "Implement auth", priority="HIGH")
        assert task.id.startswith("TSK-")
        assert task.title == "Implement auth"
        assert task.priority == TaskPriority.HIGH
        assert task.status == TaskStatus.BACKLOG

    def test_create_task_with_sprint(self):
        task = self.engine.create_task("prog-1", "In sprint", sprint_id="SPR-001")
        assert task.status == TaskStatus.TODO  # auto-promotes from BACKLOG
        assert task.sprint_id == "SPR-001"

    def test_create_task_with_due_date(self):
        task = self.engine.create_task("prog-1", "Deadline", due_date="2026-04-01")
        assert task.due_date == date(2026, 4, 1)

    def test_update_task_status(self):
        task = self.engine.create_task("prog-1", "Work item")
        updated = self.engine.update_task("prog-1", task.id, status="IN_PROGRESS")
        assert updated.status == TaskStatus.IN_PROGRESS

    def test_update_task_done(self):
        task = self.engine.create_task("prog-1", "Finish this")
        updated = self.engine.update_task("prog-1", task.id, status="DONE")
        assert updated.completed_at is not None

    def test_update_task_blocked(self):
        t1 = self.engine.create_task("prog-1", "Blocker")
        t2 = self.engine.create_task("prog-1", "Blocked task")
        updated = self.engine.update_task("prog-1", t2.id, blocked_by=[t1.id])
        assert updated.status == TaskStatus.BLOCKED
        assert t1.id in updated.blocked_by

    def test_update_nonexistent_task(self):
        result = self.engine.update_task("prog-1", "TSK-NOPE", status="DONE")
        assert result is None

    def test_get_board(self):
        self.engine.create_task("prog-1", "Task A", priority="HIGH")
        self.engine.create_task("prog-1", "Task B", priority="LOW")
        board = self.engine.get_board("prog-1")
        assert "Task A" in board
        assert "Task B" in board
        assert "Task Board" in board


class TestSprintManagement:
    def setup_method(self):
        self.store = _make_store()
        self.engine = TaskBoardEngine(self.store)

    def test_create_sprint(self):
        sprint = self.engine.create_sprint(
            "prog-1", "Sprint 1", "2026-03-01", "2026-03-14",
            goal="Ship auth", team_capacity_hours=80,
        )
        assert sprint.id.startswith("SPR-")
        assert sprint.name == "Sprint 1"
        assert sprint.team_capacity_hours == 80

    def test_start_sprint(self):
        sprint = self.engine.create_sprint("prog-1", "Sprint 1", "2026-03-01", "2026-03-14")
        result = self.engine.start_sprint("prog-1", sprint.id)
        assert result.is_active is True

    def test_start_sprint_deactivates_previous(self):
        s1 = self.engine.create_sprint("prog-1", "Sprint 1", "2026-03-01", "2026-03-14")
        s2 = self.engine.create_sprint("prog-1", "Sprint 2", "2026-03-15", "2026-03-28")
        self.engine.start_sprint("prog-1", s1.id)
        self.engine.start_sprint("prog-1", s2.id)

        board = self.engine._load("prog-1")
        active = [s for s in board.sprints if s.is_active]
        assert len(active) == 1
        assert active[0].id == s2.id

    def test_complete_sprint(self):
        sprint = self.engine.create_sprint("prog-1", "Sprint 1", "2026-03-01", "2026-03-14")
        self.engine.start_sprint("prog-1", sprint.id)

        # Add tasks
        t1 = self.engine.create_task("prog-1", "Done task", sprint_id=sprint.id)
        t2 = self.engine.create_task("prog-1", "Carry-over task", sprint_id=sprint.id)
        self.engine.update_task("prog-1", t1.id, status="DONE")

        result = self.engine.complete_sprint("prog-1", sprint.id)
        assert result["completed"] == 1
        assert result["carry_over"] == 1
        assert "Carry-over task" in result["carry_over_tasks"]

    def test_sprint_metrics(self):
        sprint = self.engine.create_sprint(
            "prog-1", "Sprint 1", "2026-03-01", "2026-03-14",
            team_capacity_hours=80,
        )
        self.engine.start_sprint("prog-1", sprint.id)

        self.engine.create_task("prog-1", "Task A", sprint_id=sprint.id, effort_hours=8)
        t2 = self.engine.create_task("prog-1", "Task B", sprint_id=sprint.id, effort_hours=4)
        self.engine.update_task("prog-1", t2.id, status="DONE")

        metrics = self.engine.get_sprint_metrics("prog-1", sprint.id)
        assert metrics["total_tasks"] == 2
        assert metrics["done"] == 1
        assert metrics["velocity_hours"] == 4


# ── Zoom AI Notes → Task extraction ─────────────────────────────

class TestZoomNotesExtraction:
    def setup_method(self):
        self.store = _make_store()
        self.engine = TaskBoardEngine(self.store)

    def test_extract_from_action_items(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-001",
            raw_notes="",
            action_items=[
                {"owner": "Alice", "description": "Finish auth migration by March 1"},
                {"owner": "Bob", "description": "Review the API spec"},
            ],
            meeting_topic="Sprint Planning",
        )
        assert len(tasks) == 2
        assert tasks[0].assignee == "Alice"
        assert tasks[0].source == TaskSource.ZOOM_AI_NOTES
        assert tasks[0].source_id == "ZM-001"
        assert tasks[0].confidence == 0.85

    def test_extract_from_follow_ups(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-002",
            raw_notes="",
            follow_ups=[
                {"person": "Charlie", "action": "Prototype the API gateway"},
            ],
        )
        assert len(tasks) == 1
        assert tasks[0].assignee == "Charlie"
        assert tasks[0].source == TaskSource.MEETING_ACTION

    def test_extract_from_key_decisions(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-003",
            raw_notes="",
            key_decisions=["Adopt microservice architecture"],
        )
        assert len(tasks) == 1
        assert "microservice" in tasks[0].title.lower()
        assert "decision-follow-up" in tasks[0].tags

    def test_detect_implicit_commitments(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-004",
            raw_notes=(
                "Alice will review the API spec by Friday. "
                "Bob agreed to finish the database migration. "
                "Charlie is going to prototype the new gateway service."
            ),
        )
        assert len(tasks) >= 2  # at least Alice and Bob
        names = [t.assignee for t in tasks]
        assert "Alice" in names
        assert "Bob" in names

    def test_implicit_commitments_lower_confidence(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-005",
            raw_notes="Diana will set up the staging environment.",
        )
        assert len(tasks) >= 1
        implicit = [t for t in tasks if t.confidence < 0.8]
        assert len(implicit) >= 1

    def test_priority_inference_urgent(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-006",
            raw_notes="",
            action_items=[
                {"owner": "Eve", "description": "Fix the production blocker ASAP"},
            ],
        )
        assert tasks[0].priority == TaskPriority.CRITICAL

    def test_priority_inference_high(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-007",
            raw_notes="",
            action_items=[
                {"owner": "Frank", "description": "Must ship the feature this week"},
            ],
        )
        assert tasks[0].priority == TaskPriority.HIGH

    def test_due_date_extraction_by_friday(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-008",
            raw_notes="",
            action_items=[
                {"owner": "Grace", "description": "Finish the docs by Friday"},
            ],
        )
        assert tasks[0].due_date is not None

    def test_due_date_extraction_iso(self):
        tasks = self.engine.extract_tasks_from_zoom_notes(
            "prog-1",
            meeting_id="ZM-009",
            raw_notes="",
            action_items=[
                {"owner": "Hank", "description": "Deploy to prod by 2026-04-15"},
            ],
        )
        assert tasks[0].due_date == date(2026, 4, 15)

    def test_empty_notes_no_crash(self):
        tasks = self.engine.extract_tasks_from_zoom_notes("prog-1", "ZM-010", "")
        assert tasks == []


# ── Intelligent prioritization ───────────────────────────────────

class TestReprioritization:
    def setup_method(self):
        self.store = _make_store()
        self.engine = TaskBoardEngine(self.store)

    def test_overdue_tasks_become_critical(self):
        task = self.engine.create_task(
            "prog-1", "Late task",
            priority="LOW",
            due_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        changes = self.engine.reprioritize("prog-1")
        assert len(changes) == 1
        assert changes[0]["new_priority"] == "CRITICAL"

    def test_due_soon_tasks_become_high(self):
        task = self.engine.create_task(
            "prog-1", "Due soon",
            priority="LOW",
            due_date=(date.today() + timedelta(days=2)).isoformat(),
        )
        changes = self.engine.reprioritize("prog-1")
        assert len(changes) == 1
        assert changes[0]["new_priority"] == "HIGH"

    def test_blocking_tasks_boosted(self):
        t1 = self.engine.create_task("prog-1", "Foundation task", priority="LOW")
        # Simulate blocking 3 tasks
        board = self.engine._load("prog-1")
        blocker = board.get_task(t1.id)
        blocker.blocks = ["TSK-A", "TSK-B", "TSK-C"]
        self.engine._save("prog-1", board)

        changes = self.engine.reprioritize("prog-1")
        assert len(changes) == 1
        assert changes[0]["new_priority"] == "CRITICAL"

    def test_no_changes_when_correct(self):
        self.engine.create_task("prog-1", "OK task", priority="MEDIUM")
        changes = self.engine.reprioritize("prog-1")
        assert len(changes) == 0


# ── Workload analysis ────────────────────────────────────────────

class TestWorkloadAnalysis:
    def setup_method(self):
        self.store = _make_store()
        self.engine = TaskBoardEngine(self.store)

    def test_workload_counts(self):
        self.engine.create_task("prog-1", "Task 1", assignee="Alice", effort_hours=4)
        self.engine.create_task("prog-1", "Task 2", assignee="Alice", effort_hours=8)
        self.engine.create_task("prog-1", "Task 3", assignee="Bob", effort_hours=6)

        workload = self.engine.get_workload("prog-1")
        assert workload["Alice"]["total_tasks"] == 2
        assert workload["Alice"]["total_hours"] == 12
        assert workload["Bob"]["total_tasks"] == 1

    def test_overloaded_detection(self):
        # Create many overdue tasks for one person
        for i in range(4):
            t = self.engine.create_task(
                "prog-1", f"Overdue {i}", assignee="Charlie",
                due_date=(date.today() - timedelta(days=1)).isoformat(),
            )

        workload = self.engine.get_workload("prog-1")
        assert workload["Charlie"]["health"] == "overloaded"

    def test_healthy_detection(self):
        self.engine.create_task("prog-1", "Small task", assignee="Diana", effort_hours=2)
        workload = self.engine.get_workload("prog-1")
        assert workload["Diana"]["health"] == "healthy"

    def test_empty_board(self):
        workload = self.engine.get_workload("prog-1")
        assert workload == {}


# ── Helper method tests ──────────────────────────────────────────

class TestHelperMethods:
    def test_infer_priority_critical(self):
        assert TaskBoardEngine._infer_priority("Fix this ASAP") == TaskPriority.CRITICAL
        assert TaskBoardEngine._infer_priority("This is a blocker") == TaskPriority.CRITICAL

    def test_infer_priority_high(self):
        assert TaskBoardEngine._infer_priority("Must ship this week") == TaskPriority.HIGH
        assert TaskBoardEngine._infer_priority("Finish by EOD") == TaskPriority.HIGH

    def test_infer_priority_low(self):
        assert TaskBoardEngine._infer_priority("Nice to have feature") == TaskPriority.LOW

    def test_infer_priority_medium_default(self):
        assert TaskBoardEngine._infer_priority("Review the design doc") == TaskPriority.MEDIUM

    def test_infer_due_date_today(self):
        result = TaskBoardEngine._infer_due_date("Finish by end of day")
        assert result == date.today()

    def test_infer_due_date_tomorrow(self):
        result = TaskBoardEngine._infer_due_date("Do this tomorrow")
        assert result == date.today() + timedelta(days=1)

    def test_infer_due_date_iso(self):
        result = TaskBoardEngine._infer_due_date("Deploy by 2026-05-01")
        assert result == date(2026, 5, 1)

    def test_infer_due_date_month_name(self):
        result = TaskBoardEngine._infer_due_date("Complete by March 15")
        assert result is not None
        assert result.month == 3
        assert result.day == 15

    def test_infer_due_date_none(self):
        result = TaskBoardEngine._infer_due_date("Just review when you can")
        assert result is None

    def test_clean_title_capitalize(self):
        assert TaskBoardEngine._clean_title("review the spec") == "Review the spec"

    def test_clean_title_strip_to(self):
        assert TaskBoardEngine._clean_title("to finish the auth") == "Finish the auth"

    def test_clean_title_truncate(self):
        long = "x" * 200
        result = TaskBoardEngine._clean_title(long)
        assert len(result) <= 100

    def test_detect_implicit_commitments(self):
        notes = "Alice will review the API spec. Bob agreed to finish the migration."
        result = TaskBoardEngine._detect_implicit_commitments(notes)
        assert len(result) >= 2
        persons = [c["person"] for c in result]
        assert "Alice" in persons
        assert "Bob" in persons

    def test_detect_implicit_skip_pronouns(self):
        notes = "They will discuss it later. It will be fine."
        result = TaskBoardEngine._detect_implicit_commitments(notes)
        assert len(result) == 0

    def test_assess_person_health(self):
        assert TaskBoardEngine._assess_person_health(2, 0, 0) == "healthy"
        assert TaskBoardEngine._assess_person_health(7, 0, 0) == "heavy"
        assert TaskBoardEngine._assess_person_health(3, 1, 0) == "at_risk"
        assert TaskBoardEngine._assess_person_health(8, 3, 0) == "overloaded"
