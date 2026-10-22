"""Tests for artifact management."""

import tempfile
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.artifacts.raid_log import RAIDLogManager
from tpllm.artifacts.decision_log import DecisionLogManager
from tpllm.artifacts.milestone_plan import MilestonePlanManager
from tpllm.artifacts.meeting_notes import MeetingNotesManager
from tpllm.artifacts.status_update import StatusUpdateGenerator
from tpllm.artifacts.program_brief import ProgramBriefGenerator
from tpllm.artifacts.launch_checklist import LaunchChecklistManager


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_program_brief_roundtrip():
    store = _make_store()
    gen = ProgramBriefGenerator(store)
    program = gen.create_program(
        program_id="test-prog",
        name="Test Program",
        goal="Ship the thing",
        non_goals=["Don't ship the other thing"],
        target_date="2025-06-01",
    )
    assert program.name == "Test Program"
    loaded = gen.get_program("test-prog")
    assert loaded is not None
    assert loaded.goal == "Ship the thing"


def test_raid_log_add_and_query():
    store = _make_store()
    mgr = RAIDLogManager(store)
    risk = mgr.add_risk("test-prog", "Server capacity", severity="HIGH", owner="Alice")
    assert risk.id.startswith("R-")

    issue = mgr.add_issue("test-prog", "Flaky test", severity="MEDIUM")
    assert issue.id.startswith("I-")

    dep = mgr.add_dependency("test-prog", "Auth API from Platform team", upstream_team="Platform")
    assert dep.id.startswith("D-")

    log = mgr.get_log("test-prog")
    assert len(log.risks) == 1
    assert len(log.issues) == 1
    assert len(log.dependencies) == 1
    summary = log.summary()
    assert "Open risks: 1" in summary


def test_decision_log_propose_and_record():
    store = _make_store()
    mgr = DecisionLogManager(store)
    dec = mgr.propose_decision(
        "test-prog",
        title="Which database?",
        context="Need to choose between Postgres and MySQL",
        options=[
            {"label": "Postgres", "pros": ["JSONB support"], "cons": ["Complexity"]},
            {"label": "MySQL", "pros": ["Simplicity"], "cons": ["Less features"]},
        ],
    )
    assert dec.id.startswith("DEC-")
    assert len(mgr.get_pending("test-prog")) == 1

    mgr.record_decision("test-prog", dec.id, "Postgres", "Alice", rationale="JSONB is critical")
    assert len(mgr.get_pending("test-prog")) == 0


def test_milestone_plan():
    store = _make_store()
    mgr = MilestonePlanManager(store)
    ms = mgr.add_milestone(
        "test-prog",
        name="Alpha",
        target_date="2025-04-01",
        owner="Bob",
        on_critical_path=True,
    )
    assert ms.id.startswith("MS-")
    assert mgr.update_milestone_status("test-prog", ms.id, "IN_PROGRESS")

    plan = mgr.get_plan("test-prog")
    assert len(plan.milestones) == 1
    assert plan.critical_path[0].name == "Alpha"


def test_action_items():
    store = _make_store()
    mgr = MeetingNotesManager(store)
    item = mgr.add_action_item("test-prog", "Write the RFC", "Alice", due_date="2025-03-15")
    assert item.id.startswith("AI-")

    items = mgr.get_open_items("test-prog")
    assert len(items) == 1

    mgr.complete_action_item("test-prog", item.id)
    assert len(mgr.get_open_items("test-prog")) == 0


def test_launch_checklist():
    store = _make_store()
    mgr = LaunchChecklistManager(store)
    cl = mgr.initialize("test-prog")
    assert len(cl.items) > 0
    assert not cl.ready  # nothing passed yet

    # Pass all items
    for i in range(len(cl.items)):
        mgr.update_item("test-prog", i, "PASSED", owner="QA")

    assert mgr.is_ready("test-prog")


def test_status_update():
    store = _make_store()
    gen = StatusUpdateGenerator(store)
    update = gen.create(
        program_id="test-prog",
        program_name="Test Program",
        overall_health="YELLOW",
        progress=["Completed auth module"],
        top_risks=["Server capacity unclear"],
        asks=["Need DevOps review"],
        upcoming=["Beta release next week"],
    )
    rendered = update.render()
    assert "YELLOW" in rendered
    assert "auth module" in rendered

    latest = gen.get_latest("test-prog")
    assert latest is not None
