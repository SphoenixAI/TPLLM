"""Tests for Approval Workflow Engine."""

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.approval import ApprovalStatus, ApprovalType
from lltpm.workflows.approval_engine import ApprovalEngine


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def _create_request(engine, program_id="test-prog"):
    return engine.create_request(
        program_id=program_id,
        title="Add OAuth2 scope",
        approval_type=ApprovalType.SCOPE_CHANGE,
        description="Adding OAuth2 support",
        justification="Required for security compliance",
        requester="alice",
        approvers=[
            {"name": "bob", "role": "Tech Lead"},
            {"name": "carol", "role": "Director"},
        ],
    )


def test_create_request():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)

    assert req.title == "Add OAuth2 scope"
    assert req.status == ApprovalStatus.PENDING
    assert len(req.steps) == 2
    assert req.next_approver == "bob"


def test_approve_first_step():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)
    updated = engine.approve_step("test-prog", req.id, "bob", "LGTM")

    assert updated is not None
    assert updated.current_step == 1
    assert updated.next_approver == "carol"
    assert updated.status == ApprovalStatus.PENDING  # still needs carol


def test_approve_all_steps():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)
    engine.approve_step("test-prog", req.id, "bob", "Approved")
    updated = engine.approve_step("test-prog", req.id, "carol", "Ship it")

    assert updated is not None
    assert updated.status == ApprovalStatus.APPROVED
    assert updated.is_complete


def test_reject():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)
    updated = engine.reject_step("test-prog", req.id, "bob", "Too risky")

    assert updated is not None
    assert updated.status == ApprovalStatus.REJECTED
    assert updated.final_comment == "Too risky"


def test_wrong_approver_rejected():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)
    # carol can't approve before bob
    result = engine.approve_step("test-prog", req.id, "carol", "Approved")

    assert result is None


def test_delegate_step():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)
    engine.delegate_step("test-prog", req.id, "bob", "dave")

    updated = engine.get_request("test-prog", req.id)
    assert updated.steps[0].delegated_to == "dave"

    # dave can now approve
    result = engine.approve_step("test-prog", req.id, "dave", "Approved by delegate")
    assert result is not None
    assert result.current_step == 1


def test_withdraw():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)
    withdrawn = engine.withdraw("test-prog", req.id, "alice")

    assert withdrawn is not None
    assert withdrawn.status == ApprovalStatus.WITHDRAWN


def test_get_pending():
    store = _make_store()
    engine = ApprovalEngine(store)

    _create_request(engine)
    _create_request(engine)

    pending = engine.get_pending("test-prog")
    assert len(pending) == 2


def test_escalate_stale():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)

    # Manually age the request
    from datetime import datetime, timedelta
    chain = engine._load_chain("test-prog")
    chain.requests[0].created_at = datetime.utcnow() - timedelta(hours=72)
    engine._save_chain("test-prog", chain)

    escalated = engine.escalate_stale("test-prog", hours_threshold=48)
    assert len(escalated) == 1
    assert escalated[0].status == ApprovalStatus.ESCALATED


def test_approval_summary():
    store = _make_store()
    engine = ApprovalEngine(store)

    _create_request(engine)
    summary = engine.get_summary("test-prog")

    assert "Approval Chain" in summary
    assert "Pending" in summary


def test_approval_progress():
    store = _make_store()
    engine = ApprovalEngine(store)

    req = _create_request(engine)
    assert req.approval_progress == "0/2"

    engine.approve_step("test-prog", req.id, "bob")
    updated = engine.get_request("test-prog", req.id)
    assert updated.approval_progress == "1/2"
