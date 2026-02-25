"""Tests for Security Manager — RBAC, API keys, audit logging."""

import tempfile
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.security import AuditAction, Permission, Role
from lltpm.workflows.security_manager import SecurityManager


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def test_create_api_key():
    store = _make_store()
    mgr = SecurityManager(store)

    key, raw = mgr.create_api_key("test-key", Role.ENGINEER, owner="alice")

    assert key.name == "test-key"
    assert key.role == Role.ENGINEER
    assert key.is_valid
    assert raw.startswith("lltpm_")
    assert key.key_prefix == raw[:12]


def test_validate_api_key():
    store = _make_store()
    mgr = SecurityManager(store)

    key, raw = mgr.create_api_key("test-key", Role.ADMIN)
    validated = mgr.validate_api_key(raw)

    assert validated is not None
    assert validated.id == key.id
    assert validated.role == Role.ADMIN


def test_invalid_key_rejected():
    store = _make_store()
    mgr = SecurityManager(store)

    mgr.create_api_key("test-key", Role.VIEWER)
    result = mgr.validate_api_key("lltpm_invalid_key_here")

    assert result is None


def test_revoke_api_key():
    store = _make_store()
    mgr = SecurityManager(store)

    key, raw = mgr.create_api_key("revocable", Role.VIEWER)
    mgr.revoke_api_key(key.id, actor="admin")

    result = mgr.validate_api_key(raw)
    assert result is None  # revoked key should fail validation


def test_list_api_keys():
    store = _make_store()
    mgr = SecurityManager(store)

    mgr.create_api_key("key-1", Role.ADMIN)
    mgr.create_api_key("key-2", Role.ENGINEER)

    keys = mgr.list_api_keys()
    assert len(keys) == 2
    names = {k.name for k in keys}
    assert "key-1" in names
    assert "key-2" in names


def test_rbac_admin_has_all():
    store = _make_store()
    mgr = SecurityManager(store)

    assert mgr.check_permission(Role.ADMIN, Permission.PROGRAM_DELETE)
    assert mgr.check_permission(Role.ADMIN, Permission.AGENT_CONFIGURE)


def test_rbac_engineer_limited():
    store = _make_store()
    mgr = SecurityManager(store)

    assert mgr.check_permission(Role.ENGINEER, Permission.PROGRAM_READ)
    assert not mgr.check_permission(Role.ENGINEER, Permission.PROGRAM_DELETE)
    assert not mgr.check_permission(Role.ENGINEER, Permission.EVALUATION_WRITE)


def test_rbac_scope_restriction():
    store = _make_store()
    mgr = SecurityManager(store)

    # Scoped key can only access specific programs
    assert mgr.check_permission(
        Role.ENGINEER, Permission.PROGRAM_READ,
        program_id="prog-1", scopes=["prog-1"]
    )
    assert not mgr.check_permission(
        Role.ENGINEER, Permission.PROGRAM_READ,
        program_id="prog-2", scopes=["prog-1"]
    )


def test_audit_logging():
    store = _make_store()
    mgr = SecurityManager(store)

    mgr.log_action(
        AuditAction.READ,
        actor="alice",
        resource_type="program",
        resource_id="prog-1",
        detail="Viewed program brief",
    )
    mgr.log_action(
        AuditAction.WRITE,
        actor="bob",
        resource_type="decision",
        resource_id="DEC-001",
        detail="Created decision",
    )

    entries = mgr.get_audit_log(actor="alice")
    assert len(entries) == 1
    assert entries[0].action == AuditAction.READ

    all_entries = mgr.get_audit_log()
    assert len(all_entries) >= 2


def test_audit_summary():
    store = _make_store()
    mgr = SecurityManager(store)

    mgr.log_action(AuditAction.TOOL_CALL, actor="agent", detail="Called get_risk_scan")
    summary = mgr.get_audit_summary()

    assert "Audit Log" in summary
    assert "agent" in summary


def test_oauth_token_store():
    store = _make_store()
    mgr = SecurityManager(store)

    token = mgr.store_oauth_token(
        integration="slack",
        access_token="xoxb-test-token",
        refresh_token="xoxr-refresh",
        expires_in=3600,
        scopes=["chat:write", "users:read"],
    )

    assert token.integration == "slack"
    assert not token.is_expired

    status = mgr.get_token_status("slack")
    assert status["status"] == "active"
    assert status["has_refresh_token"] is True


def test_token_status_not_configured():
    store = _make_store()
    mgr = SecurityManager(store)

    status = mgr.get_token_status("nonexistent")
    assert status["status"] == "not_configured"
