"""Tests for JWT Authentication — token issuance, validation, refresh, revocation."""

import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.security import (
    AuditAction,
    JWTClaims,
    JWTConfig,
    Permission,
    Role,
    TokenPair,
)
from lltpm.workflows.security_manager import SecurityManager


def _make_manager(**jwt_kwargs):
    store = ArtifactStore(Path(tempfile.mkdtemp()))
    jwt_config = JWTConfig(**jwt_kwargs) if jwt_kwargs else None
    return SecurityManager(store, jwt_config=jwt_config)


# ── JWT Config ─────────────────────────────────────────────


def test_jwt_config_defaults():
    cfg = JWTConfig()
    assert cfg.algorithm == "HS256"
    assert cfg.access_token_ttl_minutes == 30
    assert cfg.refresh_token_ttl_days == 7
    assert cfg.issuer == "lltpm"
    assert cfg.audience == "lltpm-api"
    assert len(cfg.secret_key) > 20  # should be a long random key


def test_jwt_config_custom():
    cfg = JWTConfig(
        secret_key="test-secret-key-long-enough-for-hs256",
        access_token_ttl_minutes=15,
        refresh_token_ttl_days=3,
    )
    assert cfg.secret_key == "test-secret-key-long-enough-for-hs256"
    assert cfg.access_token_ttl_minutes == 15
    assert cfg.refresh_token_ttl_days == 3


# ── JWT Claims ─────────────────────────────────────────────


def test_jwt_claims_to_payload():
    claims = JWTClaims(
        sub="user-123",
        role=Role.ENGINEER,
        scopes=["prog-1"],
    )
    payload = claims.to_payload()
    assert payload["sub"] == "user-123"
    assert payload["role"] == "engineer"
    assert payload["scopes"] == ["prog-1"]
    assert payload["iss"] == "lltpm"
    assert payload["aud"] == "lltpm-api"
    assert payload["token_type"] == "access"
    assert isinstance(payload["iat"], int)
    assert isinstance(payload["exp"], int)
    assert payload["exp"] > payload["iat"]


def test_jwt_claims_is_expired():
    # Not expired
    claims = JWTClaims(
        sub="user",
        role=Role.VIEWER,
        exp=datetime.utcnow() + timedelta(hours=1),
    )
    assert not claims.is_expired

    # Expired
    claims_expired = JWTClaims(
        sub="user",
        role=Role.VIEWER,
        exp=datetime.utcnow() - timedelta(hours=1),
    )
    assert claims_expired.is_expired


def test_jwt_claims_jti_unique():
    claims1 = JWTClaims(sub="user", role=Role.VIEWER)
    claims2 = JWTClaims(sub="user", role=Role.VIEWER)
    assert claims1.jti != claims2.jti


# ── Token Pair Model ──────────────────────────────────────


def test_token_pair_model():
    pair = TokenPair(
        access_token="access-xxx",
        refresh_token="refresh-xxx",
        expires_in=1800,
        role=Role.ADMIN,
        scopes=["prog-1"],
    )
    assert pair.token_type == "Bearer"
    assert pair.expires_in == 1800
    assert pair.role == Role.ADMIN


# ── Token Issuance ─────────────────────────────────────────


def test_issue_token_pair():
    mgr = _make_manager(secret_key="test-secret-key-123-padding-to-32bytes")
    pair = mgr.issue_token_pair("alice", Role.MANAGER, scopes=["prog-1"])

    assert isinstance(pair, TokenPair)
    assert pair.access_token
    assert pair.refresh_token
    assert pair.access_token != pair.refresh_token
    assert pair.role == Role.MANAGER
    assert pair.scopes == ["prog-1"]
    assert pair.token_type == "Bearer"
    assert pair.expires_in == 30 * 60  # default 30 min


def test_issue_token_pair_custom_ttl():
    mgr = _make_manager(
        secret_key="test-secret-key-long-enough-for-hs256",
        access_token_ttl_minutes=15,
    )
    pair = mgr.issue_token_pair("bob", Role.ENGINEER)
    assert pair.expires_in == 15 * 60


def test_issue_token_pair_creates_audit():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    mgr.issue_token_pair("charlie", Role.ADMIN, ip_address="192.168.1.1")

    entries = mgr.get_audit_log(actor="charlie")
    assert any(
        e.action == AuditAction.TOKEN_ISSUED and e.ip_address == "192.168.1.1"
        for e in entries
    )


# ── Token Validation ──────────────────────────────────────


def test_validate_jwt_success():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    pair = mgr.issue_token_pair("alice", Role.MANAGER, scopes=["prog-1"])

    claims = mgr.validate_jwt(pair.access_token)
    assert claims is not None
    assert claims.sub == "alice"
    assert claims.role == Role.MANAGER
    assert claims.scopes == ["prog-1"]
    assert claims.token_type == "access"


def test_validate_jwt_refresh_token():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    pair = mgr.issue_token_pair("bob", Role.ENGINEER)

    claims = mgr.validate_jwt(pair.refresh_token)
    assert claims is not None
    assert claims.token_type == "refresh"
    assert claims.sub == "bob"


def test_validate_jwt_invalid_token():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    assert mgr.validate_jwt("not-a-jwt") is None


def test_validate_jwt_wrong_secret():
    mgr1 = _make_manager(secret_key="secret-key-one-long-enough-for-hs256")
    pair = mgr1.issue_token_pair("alice", Role.ADMIN)

    mgr2 = _make_manager(secret_key="secret-key-two-long-enough-for-hs256")
    assert mgr2.validate_jwt(pair.access_token) is None


def test_validate_jwt_expired():
    import jwt as pyjwt

    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    # Manually create an expired token
    payload = {
        "sub": "alice",
        "role": "admin",
        "scopes": [],
        "iss": "lltpm",
        "aud": "lltpm-api",
        "iat": int((datetime.utcnow() - timedelta(hours=2)).timestamp()),
        "exp": int((datetime.utcnow() - timedelta(hours=1)).timestamp()),
        "jti": "expired-jti",
        "token_type": "access",
    }
    expired_token = pyjwt.encode(payload, "test-secret-key-long-enough-for-hs256", algorithm="HS256")
    assert mgr.validate_jwt(expired_token) is None


def test_validate_jwt_wrong_issuer():
    import jwt as pyjwt

    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    payload = {
        "sub": "alice",
        "role": "admin",
        "scopes": [],
        "iss": "wrong-issuer",
        "aud": "lltpm-api",
        "iat": int(datetime.utcnow().timestamp()),
        "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
        "jti": "test-jti",
        "token_type": "access",
    }
    token = pyjwt.encode(payload, "test-secret-key-long-enough-for-hs256", algorithm="HS256")
    assert mgr.validate_jwt(token) is None


# ── Token Refresh ──────────────────────────────────────────


def test_refresh_access_token():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    original = mgr.issue_token_pair("alice", Role.MANAGER, scopes=["prog-1"])

    new_pair = mgr.refresh_access_token(original.refresh_token)
    assert new_pair is not None
    assert new_pair.access_token != original.access_token
    assert new_pair.refresh_token != original.refresh_token
    assert new_pair.role == Role.MANAGER
    assert new_pair.scopes == ["prog-1"]


def test_refresh_token_rotation_revokes_old():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    original = mgr.issue_token_pair("alice", Role.ADMIN)

    mgr.refresh_access_token(original.refresh_token)

    # Old refresh token should now be revoked
    assert mgr.refresh_access_token(original.refresh_token) is None


def test_refresh_rejects_access_token():
    """Access tokens cannot be used as refresh tokens."""
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    pair = mgr.issue_token_pair("alice", Role.ADMIN)

    # Using access token for refresh should fail
    result = mgr.refresh_access_token(pair.access_token)
    assert result is None


def test_refresh_invalid_token():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    assert mgr.refresh_access_token("invalid-token") is None


# ── Token Revocation ──────────────────────────────────────


def test_revoke_token():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    pair = mgr.issue_token_pair("alice", Role.ADMIN)

    # Validate before revocation
    claims = mgr.validate_jwt(pair.access_token)
    assert claims is not None

    # Revoke
    mgr.revoke_token(claims.jti)

    # Should now fail validation
    assert mgr.validate_jwt(pair.access_token) is None


def test_revoke_preserves_other_tokens():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    pair1 = mgr.issue_token_pair("alice", Role.ADMIN)
    pair2 = mgr.issue_token_pair("bob", Role.ENGINEER)

    claims1 = mgr.validate_jwt(pair1.access_token)
    mgr.revoke_token(claims1.jti)

    # pair1 revoked, pair2 still valid
    assert mgr.validate_jwt(pair1.access_token) is None
    assert mgr.validate_jwt(pair2.access_token) is not None


# ── API Key → JWT Bridge ──────────────────────────────────


def test_authenticate_api_key_to_jwt():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    key, raw = mgr.create_api_key("bridge-test", Role.MANAGER, owner="alice")

    pair = mgr.authenticate_api_key_to_jwt(raw, ip_address="10.0.0.1")
    assert pair is not None
    assert pair.role == Role.MANAGER

    # Validate the issued JWT
    claims = mgr.validate_jwt(pair.access_token)
    assert claims is not None
    assert claims.sub == key.id
    assert claims.role == Role.MANAGER


def test_authenticate_invalid_api_key():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    pair = mgr.authenticate_api_key_to_jwt("lltpm_invalid_key")
    assert pair is None

    # Should log failed login
    entries = mgr.get_audit_log(actor="unknown")
    assert any(
        e.action == AuditAction.LOGIN and not e.success
        for e in entries
    )


def test_authenticate_revoked_api_key():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    key, raw = mgr.create_api_key("revocable", Role.VIEWER)
    mgr.revoke_api_key(key.id)

    pair = mgr.authenticate_api_key_to_jwt(raw)
    assert pair is None


# ── JWT Config Persistence ─────────────────────────────────


def test_jwt_config_persisted():
    store = ArtifactStore(Path(tempfile.mkdtemp()))
    mgr1 = SecurityManager(store)
    secret1 = mgr1._jwt_config.secret_key

    # New manager with same store should load the persisted config
    mgr2 = SecurityManager(store)
    assert mgr2._jwt_config.secret_key == secret1


def test_jwt_config_explicit_overrides_stored():
    store = ArtifactStore(Path(tempfile.mkdtemp()))
    mgr1 = SecurityManager(store)

    # Explicit config overrides stored
    custom = JWTConfig(secret_key="my-custom-secret")
    mgr2 = SecurityManager(store, jwt_config=custom)
    assert mgr2._jwt_config.secret_key == "my-custom-secret"


# ── Edge Cases ──────────────────────────────────────────────


def test_token_pair_scopes_default_empty():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    pair = mgr.issue_token_pair("alice", Role.VIEWER)
    assert pair.scopes == []

    claims = mgr.validate_jwt(pair.access_token)
    assert claims.scopes == []


def test_multiple_token_pairs_independent():
    mgr = _make_manager(secret_key="test-secret-key-long-enough-for-hs256")
    pair1 = mgr.issue_token_pair("alice", Role.ADMIN)
    pair2 = mgr.issue_token_pair("alice", Role.ADMIN)

    # Both should be independently valid
    assert mgr.validate_jwt(pair1.access_token) is not None
    assert mgr.validate_jwt(pair2.access_token) is not None

    # Revoking one shouldn't affect the other
    claims1 = mgr.validate_jwt(pair1.access_token)
    mgr.revoke_token(claims1.jti)
    assert mgr.validate_jwt(pair1.access_token) is None
    assert mgr.validate_jwt(pair2.access_token) is not None
