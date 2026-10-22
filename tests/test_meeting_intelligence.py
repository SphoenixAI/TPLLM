"""Tests for meeting intelligence engine."""

import tempfile
from datetime import date
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.meeting_intelligence import MeetingIntelligenceEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


SAMPLE_ANALYSIS = {
    "executive_summary": "Team discussed auth migration timeline and API rate limiting concerns.",
    "overall_urgency": "HIGH",
    "meeting_effectiveness": "Productive — key decision made on auth provider.",
    "async_alternative": "Status portion could have been async.",
    "duration_minutes": 45,
    "topics": [
        {
            "topic": "Auth migration",
            "category": "DECISION",
            "time_spent_minutes": 20,
            "urgency": "HIGH",
            "sentiment": "CONCERNED",
            "key_points": ["Okta vs Auth0 evaluated", "Auth0 selected"],
            "open_questions": ["Migration rollback plan?"],
            "value_signal": "Blocking beta release",
        },
        {
            "topic": "API rate limiting",
            "category": "RISK",
            "time_spent_minutes": 15,
            "urgency": "MEDIUM",
            "sentiment": "NEUTRAL",
            "key_points": ["Current limits too low for launch traffic"],
            "open_questions": [],
            "value_signal": "Could cause outages at scale",
        },
    ],
    "commitments": [
        {
            "owner": "Alice",
            "description": "Complete Auth0 integration by end of sprint",
            "due_date": "2026-03-01",
            "confidence": 0.9,
            "source_quote": "Alice said she'll have Auth0 integrated by March 1st",
        },
        {
            "owner": "Bob",
            "description": "Write rate limiting RFC",
            "due_date": "2026-02-25",
            "confidence": 0.8,
            "source_quote": "Bob volunteered to write the RFC by next Wednesday",
        },
    ],
    "risks": [
        {
            "title": "Auth migration delay",
            "description": "Auth0 SDK has known issues with React Native",
            "severity": "HIGH",
            "mentioned_by": "Alice",
            "source_quote": "There are some known SDK issues we need to work around",
        },
    ],
    "decisions": [
        {
            "title": "Auth provider selection",
            "decision_text": "Use Auth0 over Okta",
            "decided_by": "Alice",
            "is_final": True,
            "options_discussed": ["Okta", "Auth0", "Firebase Auth"],
            "source_quote": "We're going with Auth0",
        },
        {
            "title": "Rate limiting strategy",
            "decision_text": "",
            "decided_by": "",
            "is_final": False,
            "options_discussed": ["Token bucket", "Sliding window"],
            "source_quote": "We need to decide on the approach",
        },
    ],
    "dependencies": [
        {
            "title": "Auth0 enterprise license",
            "upstream": "Procurement",
            "downstream": "Platform",
            "target_date": "2026-02-28",
            "source_quote": "We need procurement to finalize the Auth0 license",
        },
    ],
    "timelines": [
        {
            "milestone": "Beta release",
            "target_date": "2026-03-15",
            "date_text": "mid-March",
            "confidence": 0.7,
            "is_slip": True,
            "original_date": "2026-03-01",
        },
    ],
    "open_questions": [
        "What's the rollback plan for auth migration?",
        "Do we need a load test before launch?",
    ],
}


def test_ingest_parsed_meeting():
    store = _make_store()
    engine = MeetingIntelligenceEngine(store)

    intel = engine.ingest_parsed(
        program_id="test-prog",
        title="Sprint Planning",
        parsed_data=SAMPLE_ANALYSIS,
        attendees=["Alice", "Bob", "Charlie"],
        meeting_date="2026-02-20",
        source="google_meet",
    )

    assert intel.meeting_id.startswith("MI-")
    assert intel.meeting_title == "Sprint Planning"
    assert intel.executive_summary == SAMPLE_ANALYSIS["executive_summary"]
    assert len(intel.commitments) == 2
    assert len(intel.risks) == 1
    assert len(intel.decisions) == 2
    assert len(intel.dependencies) == 1
    assert len(intel.timelines) == 1
    assert len(intel.open_questions) == 2
    assert intel.timelines[0].is_slip is True


def test_analyze_meeting_returns_prompt():
    store = _make_store()
    engine = MeetingIntelligenceEngine(store)

    intel, prompt = engine.analyze_meeting(
        program_id="test-prog",
        title="Standup",
        content="Alice: Auth0 integration is going well. Bob: Rate limiting RFC draft ready.",
        attendees=["Alice", "Bob"],
        meeting_date="2026-02-20",
    )

    assert intel.meeting_id.startswith("MI-")
    assert "MEETING CONTENT:" in prompt
    assert "Auth0 integration" in prompt
    assert "commitments" in prompt


def test_update_from_analysis():
    store = _make_store()
    engine = MeetingIntelligenceEngine(store)

    # Create skeleton
    intel, _ = engine.analyze_meeting(
        program_id="test-prog",
        title="Planning",
        content="Some notes",
        meeting_date="2026-02-20",
    )

    # Update with analysis
    updated = engine.update_from_analysis("test-prog", intel.meeting_id, SAMPLE_ANALYSIS)

    assert updated.executive_summary == SAMPLE_ANALYSIS["executive_summary"]
    assert len(updated.commitments) == 2
    assert updated.commitments[0].owner == "Alice"
    assert updated.commitments[0].due_date == date(2026, 3, 1)


def test_program_report():
    store = _make_store()
    engine = MeetingIntelligenceEngine(store)

    # Ingest two meetings
    engine.ingest_parsed("test-prog", "Meeting 1", SAMPLE_ANALYSIS, meeting_date="2026-02-18")
    engine.ingest_parsed("test-prog", "Meeting 2", {
        "executive_summary": "Quick sync on status.",
        "overall_urgency": "LOW",
        "commitments": [{"owner": "Charlie", "description": "Update docs"}],
        "risks": [],
        "decisions": [],
        "dependencies": [],
        "timelines": [],
        "open_questions": [],
    }, meeting_date="2026-02-19")

    report = engine.get_program_report("test-prog")
    assert report.meetings_analyzed == 2
    assert report.total_commitments == 3  # 2 from meeting 1 + 1 from meeting 2
    assert report.total_risks == 1
    assert report.timeline_slips == 1
    rendered = report.render()
    assert "Meeting Intelligence Report" in rendered


def test_extract_all_commitments():
    store = _make_store()
    engine = MeetingIntelligenceEngine(store)

    engine.ingest_parsed("test-prog", "Meeting 1", SAMPLE_ANALYSIS, meeting_date="2026-02-20")

    commitments = engine.extract_all_commitments("test-prog")
    assert len(commitments) == 2
    assert commitments[0]["owner"] == "Alice"
    assert commitments[1]["owner"] == "Bob"


def test_intelligence_summary_rendering():
    store = _make_store()
    engine = MeetingIntelligenceEngine(store)

    intel = engine.ingest_parsed("test-prog", "Design Review", SAMPLE_ANALYSIS, meeting_date="2026-02-20")
    summary = intel.summary()

    assert "Meeting Intelligence" in summary
    assert "Commitments (2)" in summary
    assert "Decisions (2)" in summary
    assert "Risks (1)" in summary
    assert "Dependencies (1)" in summary
    assert "Timelines (1)" in summary
    assert "SLIPPED" in summary
    assert "Open Questions (2)" in summary
