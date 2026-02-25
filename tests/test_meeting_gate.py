"""Tests for the meeting gate workflow."""

from lltpm.workflows.meeting_gate import MeetingGate, MeetingProposal, MeetingVerdict


def test_approved_when_all_criteria_met():
    gate = MeetingGate()
    proposal = MeetingProposal(
        topic="Architecture decision for auth service",
        reason="Teams disagree on approach",
        decision_owner="Alice",
        options_documented=True,
        pre_read_available=True,
        decide_by_deadline="2025-03-20",
    )
    result = gate.evaluate(proposal)
    assert result.verdict == MeetingVerdict.APPROVED
    assert result.failures == []
    assert result.async_alternative == ""


def test_rejected_when_no_decision_owner():
    gate = MeetingGate()
    proposal = MeetingProposal(
        topic="Status sync",
        reason="Weekly check-in",
        options_documented=True,
        pre_read_available=True,
        decide_by_deadline="2025-03-20",
    )
    result = gate.evaluate(proposal)
    assert result.verdict == MeetingVerdict.REJECTED_ASYNC
    assert any("decision owner" in f.lower() for f in result.failures)
    assert result.async_alternative  # should suggest an alternative


def test_rejected_when_no_pre_read():
    gate = MeetingGate()
    proposal = MeetingProposal(
        topic="API design review",
        reason="Need alignment",
        decision_owner="Bob",
        options_documented=True,
        pre_read_available=False,
        decide_by_deadline="2025-03-20",
    )
    result = gate.evaluate(proposal)
    assert result.verdict == MeetingVerdict.REJECTED_ASYNC
    assert any("pre-read" in f.lower() for f in result.failures)


def test_rejected_when_multiple_failures():
    gate = MeetingGate()
    proposal = MeetingProposal(
        topic="General discussion",
        reason="Let's talk",
    )
    result = gate.evaluate(proposal)
    assert result.verdict == MeetingVerdict.REJECTED_ASYNC
    assert len(result.failures) == 4  # all four gates fail
