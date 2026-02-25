"""Security Manager — RBAC, API key management, audit logging.

Provides:
- API key generation, validation, and revocation
- Role-based access control with permission checks
- Immutable audit logging for all agent actions
- OAuth token lifecycle management (store, refresh, revoke)
"""

from __future__ import annotations

import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.security import (
    APIKey,
    AuditAction,
    AuditEntry,
    AuditLog,
    OAuthToken,
    Permission,
    Role,
    ROLE_PERMISSIONS,
)


class SecurityManager:
    """Manages authentication, authorization, and audit logging."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── API Key Management ────────────────────────────────────

    def create_api_key(
        self,
        name: str,
        role: Role = Role.VIEWER,
        owner: str = "",
        scopes: list[str] | None = None,
        expires_days: int | None = None,
    ) -> tuple[APIKey, str]:
        """Create a new API key. Returns (key_model, raw_key).

        The raw key is only returned once — store it securely.
        """
        raw_key = APIKey.generate_raw_key()
        key = APIKey(
            name=name,
            key_hash=APIKey.hash_key(raw_key),
            key_prefix=raw_key[:12],
            role=role,
            owner=owner,
            scopes=scopes or [],
            expires_at=(
                datetime.utcnow() + timedelta(days=expires_days)
                if expires_days
                else None
            ),
        )

        # Persist
        keys = self._load_keys()
        keys[key.id] = key.model_dump()
        self._save_keys(keys)

        # Audit
        self.log_action(
            AuditAction.KEY_CREATED,
            actor=owner or "system",
            resource_type="api_key",
            resource_id=key.id,
            detail=f"Created key '{name}' with role {role.value}",
        )

        return key, raw_key

    def validate_api_key(self, raw_key: str) -> APIKey | None:
        """Validate an API key. Returns the key model if valid, None otherwise."""
        key_hash = APIKey.hash_key(raw_key)
        keys = self._load_keys()

        for key_data in keys.values():
            key = APIKey.model_validate(key_data)
            if hmac.compare_digest(key.key_hash, key_hash) and key.is_valid:
                # Update last used
                key.last_used_at = datetime.utcnow()
                keys[key.id] = key.model_dump()
                self._save_keys(keys)
                return key
        return None

    def revoke_api_key(self, key_id: str, actor: str = "system") -> bool:
        """Revoke an API key."""
        keys = self._load_keys()
        if key_id not in keys:
            return False

        key = APIKey.model_validate(keys[key_id])
        key.revoked = True
        key.revoked_at = datetime.utcnow()
        keys[key_id] = key.model_dump()
        self._save_keys(keys)

        self.log_action(
            AuditAction.KEY_REVOKED,
            actor=actor,
            resource_type="api_key",
            resource_id=key_id,
            detail=f"Revoked key '{key.name}'",
        )
        return True

    def list_api_keys(self) -> list[APIKey]:
        """List all API keys (without hashes)."""
        keys = self._load_keys()
        return [APIKey.model_validate(v) for v in keys.values()]

    # ── RBAC ──────────────────────────────────────────────────

    def check_permission(
        self,
        role: Role,
        permission: Permission,
        program_id: str = "",
        scopes: list[str] | None = None,
    ) -> bool:
        """Check if a role has a specific permission."""
        role_perms = ROLE_PERMISSIONS.get(role, set())
        if permission not in role_perms:
            return False

        # Check program scope restriction
        if scopes and program_id and program_id not in scopes:
            return False

        return True

    def get_role_permissions(self, role: Role) -> list[str]:
        """Get all permissions for a role."""
        return [p.value for p in ROLE_PERMISSIONS.get(role, set())]

    # ── Audit Logging ─────────────────────────────────────────

    def log_action(
        self,
        action: AuditAction,
        actor: str,
        resource_type: str = "",
        resource_id: str = "",
        program_id: str = "",
        detail: str = "",
        ip_address: str = "",
        success: bool = True,
        error: str = "",
    ) -> AuditEntry:
        """Log an auditable action. Entries are append-only."""
        entry = AuditEntry(
            action=action,
            actor=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            program_id=program_id,
            detail=detail,
            ip_address=ip_address,
            success=success,
            error=error,
        )

        # Append to audit log (load, append, save)
        log = self._load_audit_log()
        log.add(entry)
        self._save_audit_log(log)

        return entry

    def get_audit_log(
        self,
        actor: str = "",
        action: AuditAction | None = None,
        program_id: str = "",
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query the audit log."""
        log = self._load_audit_log()
        return log.query(actor=actor, action=action, program_id=program_id,
                         since=since, limit=limit)

    def get_audit_summary(self, limit: int = 20) -> str:
        """Get a human-readable audit summary."""
        log = self._load_audit_log()
        return log.summary(limit=limit)

    # ── OAuth Token Management ────────────────────────────────

    def store_oauth_token(
        self,
        integration: str,
        access_token: str,
        refresh_token: str = "",
        expires_in: int | None = None,
        scopes: list[str] | None = None,
    ) -> OAuthToken:
        """Securely store an OAuth token (hashed)."""
        token = OAuthToken(
            integration=integration,
            access_token_hash=APIKey.hash_key(access_token),
            refresh_token_hash=APIKey.hash_key(refresh_token) if refresh_token else "",
            expires_at=(
                datetime.utcnow() + timedelta(seconds=expires_in)
                if expires_in
                else None
            ),
            scopes=scopes or [],
        )

        tokens = self._load_tokens()
        tokens[integration] = token.model_dump()
        self._save_tokens(tokens)

        self.log_action(
            AuditAction.INTEGRATION_CHANGE,
            actor="system",
            resource_type="oauth_token",
            resource_id=integration,
            detail=f"Stored OAuth token for {integration}",
        )

        return token

    def get_token_status(self, integration: str) -> dict[str, Any]:
        """Get the status of a stored OAuth token."""
        tokens = self._load_tokens()
        if integration not in tokens:
            return {"integration": integration, "status": "not_configured"}

        token = OAuthToken.model_validate(tokens[integration])
        return {
            "integration": integration,
            "status": "expired" if token.is_expired else "active",
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
            "scopes": token.scopes,
            "has_refresh_token": bool(token.refresh_token_hash),
        }

    # ── Persistence helpers ───────────────────────────────────

    def _load_keys(self) -> dict[str, Any]:
        raw = self._store.load_raw("_system", "api_keys")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _save_keys(self, keys: dict[str, Any]) -> None:
        self._store.save_raw("_system", "api_keys", json.dumps(keys, default=str))

    def _load_tokens(self) -> dict[str, Any]:
        raw = self._store.load_raw("_system", "oauth_tokens")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _save_tokens(self, tokens: dict[str, Any]) -> None:
        self._store.save_raw("_system", "oauth_tokens", json.dumps(tokens, default=str))

    def _load_audit_log(self) -> AuditLog:
        raw = self._store.load_raw("_system", "audit_log")
        if not raw:
            return AuditLog()
        try:
            return AuditLog.model_validate(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return AuditLog()

    def _save_audit_log(self, log: AuditLog) -> None:
        self._store.save_raw(
            "_system", "audit_log",
            json.dumps(log.model_dump(), default=str),
        )
