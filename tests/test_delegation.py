"""Tests for the delegation engine."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.artifacts.raid_log import RAIDLogManager
from tpllm.artifacts.meeting_notes import MeetingNotesManager
from tpllm.workflows.delegation_engine import DelegationEngine
from tpllm.workflows.meeting_intelligence import MeetingIntelligenceEngine
from tpllm.models.delegation import DelegationStatus, DelegationCategory, DelegationPriority


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


SAMPLE_MEETING_DATA = {
    "executive_summary": "Sprint planning with key commitments.",
    "overall_urgency": "HIGH",
    "commitments": [
        {
            "owner": "Alice",
            "description": "Complete Auth0 integration",
            "due_date": "2026-03-01",
            "confidence": 0.9,
            "source_quote": "I'll have it done by March 1st",
        },
        {
            "owner": "Bob",
            "description": "Write rate limiting RFC",
            "due_date": "2026-02-25",
            "confidence": 0.8,
            "source_quote": "",
        },
    ],
    "risks": [
        {
            "title": "SDK compatibility issue",
            "description": "Auth0 SDK has React Native issues",
            "severity": "HIGH",
            "mentioned_by": "Alice",
            "source_quote": "",
        },
    ],
    "decisions": [
        {
            "title": "Rate limiting approach",
            "decision_text": "",
            "is_final": False,
            "options_discussed": ["Token bucket", "Sliding window"],
            "source_quote": "",
        },
    ],
    "dependencies": [
        {
            "title": "Auth0 license",
            "upstream": "Procurement",
            "downstream": "Platform",
            "target_date": "2026-02-28",
            "source_quote": "",
        },
    ],
    "timelines": [],
    "open_questions": [],
}


def test_register_team_member():
    store = _make_store()
    engine = DelegationEngine(store)

    profile = engine.register_team_member(
        "test-prog", "Alice", role="Backend Engineer", team="Platform",
        expertise=["auth", "api", "database"],
    )
    assert profile.name == "Alice"
    assert profile.has_capacity is True
    assert "auth" in profile.expertise

    team = engine.get_team("test-prog")
    assert len(team) == 1


def test_delegate_task():
    store = _make_store()
    engine = DelegationEngine(store)

    item = engine.delegate(
        program_id="test-prog",
        title="Write migration script",
        description="Migrate user table to new schema",
        category="EXECUTION",
        priority="P1",
        assigned_to="Alice",
        context="Users table needs new columns for Auth0",
        success_criteria="All user records migrated without data loss",
    )

    assert item.id.startswith("DLG-")
    assert item.status == DelegationStatus.ACCEPTED
    assert item.assigned_to == "Alice"
    assert item.due_date is not None

    plan = engine.get_plan("test-prog")
    assert len(plan.items) == 1


def test_delegate_from_meeting():
    store = _make_store()
    engine = DelegationEngine(store)
    intel_engine = MeetingIntelligenceEngine(store)

    # Register team members
    engine.register_team_member("test-prog", "Alice", expertise=["auth", "backend"])
    engine.register_team_member("test-prog", "Bob", expertise=["api", "documentation"])

    # Ingest meeting
    intel = intel_engine.ingest_parsed(
        "test-prog", "Sprint Planning", SAMPLE_MEETING_DATA,
        attendees=["Alice", "Bob"], meeting_date="2026-02-20",
    )

    # Generate delegations
    items = engine.delegate_from_meeting("test-prog", intel)

    # Should create items for: 2 commitments + 1 risk + 1 pending decision + 1 dependency = 5
    assert len(items) == 5

    # Check commitment delegations
    commitment_items = [i for i in items if i.category == DelegationCategory.EXECUTION]
    assert len(commitment_items) == 2
    assert any(i.assigned_to == "Alice" for i in commitment_items)
    assert any(i.assigned_to == "Bob" for i in commitment_items)

    # Check risk mitigation delegation
    risk_items = [i for i in items if i.category == DelegationCategory.RISK_MITIGATION]
    assert len(risk_items) == 1

    # Check decision delegation
    decision_items = [i for i in items if i.category == DelegationCategory.DECISION]
    assert len(decision_items) == 1

    # Check dependency chase delegation
    dep_items = [i for i in items if i.category == DelegationCategory.DEPENDENCY_CHASE]
    assert len(dep_items) == 1


def test_delegate_from_risks():
    store = _make_store()
    engine = DelegationEngine(store)
    raid = RAIDLogManager(store)

    # Add unmitigated risk
    raid.add_risk("test-prog", "Data loss risk", severity="CRITICAL", owner="Alice")
    # Add mitigated risk (should not create delegation)
    raid.add_risk("test-prog", "Minor UI glitch", severity="LOW", owner="Bob", mitigation="Will fix in next sprint")

    items = engine.delegate_from_risks("test-prog")
    assert len(items) == 1
    assert items[0].category == DelegationCategory.RISK_MITIGATION
    assert items[0].priority == DelegationPriority.P0  # CRITICAL → P0
    assert items[0].assigned_to == "Alice"


def test_delegate_from_overdue():
    store = _make_store()
    engine = DelegationEngine(store)
    meetings = MeetingNotesManager(store)

    # Add overdue action item
    meetings.add_action_item(
        "test-prog", "Write the RFC", "Alice",
        due_date=(date.today() - timedelta(days=3)).isoformat(),
        source="Sprint planning",
    )

    items = engine.delegate_from_overdue("test-prog")
    assert len(items) == 1
    assert items[0].category == DelegationCategory.ESCALATION
    assert items[0].priority == DelegationPriority.P1
    assert items[0].follow_up_count == 1


def test_update_and_reassign():
    store = _make_store()
    engine = DelegationEngine(store)

    item = engine.delegate(
        "test-prog", "Task A", assigned_to="Alice", priority="P2",
    )

    # Update status to blocked
    assert engine.update_status("test-prog", item.id, "BLOCKED", blockers=["Waiting on API key"])

    plan = engine.get_plan("test-prog")
    blocked = plan.blocked_items
    assert len(blocked) == 1
    assert blocked[0].blockers == ["Waiting on API key"]

    # Reassign
    assert engine.reassign("test-prog", item.id, "Bob", "Alice is blocked, Bob has capacity")

    bob_items = engine.get_by_assignee("test-prog", "Bob")
    assert len(bob_items) == 1


def test_delegation_summary():
    store = _make_store()
    engine = DelegationEngine(store)

    engine.delegate("test-prog", "Task A", assigned_to="Alice", priority="P1")
    engine.delegate("test-prog", "Task B", assigned_to="Bob", priority="P2")
    engine.delegate("test-prog", "Task C", priority="P3")  # unassigned

    summary = engine.get_summary("test-prog")
    assert "Delegation Plan" in summary
    assert "Open: 3" in summary


def test_team_capacity():
    store = _make_store()
    engine = DelegationEngine(store)

    engine.register_team_member("test-prog", "Alice", max_load=2)
    engine.delegate("test-prog", "Task 1", assigned_to="Alice")
    engine.delegate("test-prog", "Task 2", assigned_to="Alice")

    team = engine.get_team("test-prog")
    alice = [p for p in team if p.name == "Alice"][0]
    assert alice.current_load == 2
    assert alice.has_capacity is False
