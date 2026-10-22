"""Tests for Scope Lock engine."""

import tempfile
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.scope_lock import ScopeLock
from tpllm.models.scope import ScopeChangeStatus, ImpactLevel


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_add_scope_item():
    store = _make_store()
    engine = ScopeLock(store)

    item = engine.add_scope_item(
        "test-prog", "Auth system", description="OAuth2 implementation",
        owner="Alice", target_date="2026-03-01",
    )
    assert item.id.startswith("SC-")
    assert item.is_locked is False


def test_lock_item():
    store = _make_store()
    engine = ScopeLock(store)

    item = engine.add_scope_item("test-prog", "Core API")
    assert engine.lock_item("test-prog", item.id)

    baseline = engine.get_baseline("test-prog")
    locked = [i for i in baseline.items if i.is_locked]
    assert len(locked) == 1


def test_freeze_scope():
    store = _make_store()
    engine = ScopeLock(store)

    engine.add_scope_item("test-prog", "Feature A")
    engine.add_scope_item("test-prog", "Feature B")

    assert engine.freeze_scope("test-prog")
    assert engine.is_frozen("test-prog")

    baseline = engine.get_baseline("test-prog")
    assert all(i.is_locked for i in baseline.items)


def test_request_change():
    store = _make_store()
    engine = ScopeLock(store)

    scr = engine.request_change(
        "test-prog",
        title="Add dark mode",
        change_type="ADD_FEATURE",
        requested_by="Bob",
        justification="Customer demand is high",
        business_value="20% of support tickets mention dark mode",
        approver="Alice",
    )

    assert scr.id.startswith("SCR-")
    assert scr.status == ScopeChangeStatus.PROPOSED
    assert scr.impact.overall_impact != ImpactLevel.NONE  # Should auto-analyze


def test_approve_change():
    store = _make_store()
    engine = ScopeLock(store)

    scr = engine.request_change(
        "test-prog", "Extend deadline",
        change_type="TIMELINE_CHANGE",
        requested_by="Bob",
        justification="Auth0 SDK issues",
    )

    assert engine.approve_change("test-prog", scr.id, "Alice", "Timeline extension justified")

    pending = engine.get_pending_changes("test-prog")
    assert len(pending) == 0


def test_reject_change():
    store = _make_store()
    engine = ScopeLock(store)

    scr = engine.request_change(
        "test-prog", "Add blockchain",
        change_type="ADD_FEATURE",
        requested_by="Charlie",
        justification="It would be cool",
    )

    assert engine.reject_change("test-prog", scr.id, "Alice", "Not aligned with program goals")

    baseline = engine.get_baseline("test-prog")
    rejected = [cr for cr in baseline.change_requests if cr.status == ScopeChangeStatus.REJECTED]
    assert len(rejected) == 1


def test_validate_change_locked():
    store = _make_store()
    engine = ScopeLock(store)

    item = engine.add_scope_item("test-prog", "Core API", locked=True)
    result = engine.validate_change("test-prog", item.id)
    assert result["requires_scr"] is True


def test_validate_change_unlocked():
    store = _make_store()
    engine = ScopeLock(store)

    item = engine.add_scope_item("test-prog", "Nice-to-have feature")
    result = engine.validate_change("test-prog", item.id)
    assert result["requires_scr"] is False


def test_scope_summary():
    store = _make_store()
    engine = ScopeLock(store)

    engine.add_scope_item("test-prog", "Feature A", locked=True)
    engine.request_change("test-prog", "Add Feature B", requested_by="Bob", justification="Needed")

    summary = engine.get_summary("test-prog")
    assert "Scope Baseline" in summary
    assert "Pending Scope Changes" in summary
