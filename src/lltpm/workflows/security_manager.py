"""Security Manager — RBAC, API key management, JWT auth, audit logging.

Provides:
- API key generation, validation, and revocation
- JWT token issuance, validation, and refresh (PyJWT 2.11+)
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
    JWTClaims,
    JWTConfig,
    OAuthToken,
    Permission,
    Role,
    ROLE_PERMISSIONS,
    TokenPair,
)


class SecurityManager:
    """Manages authentication, authorization, and audit logging."""

    def __init__(self, store: ArtifactStore, jwt_config: JWTConfig | None = None) -> None:
        self._store = store
        self._jwt_config = jwt_config or self._load_jwt_config()

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

    # ── JWT Token Management ─────────────────────────────────

    def issue_token_pair(
        self,
        subject: str,
        role: Role,
        scopes: list[str] | None = None,
        ip_address: str = "",
    ) -> TokenPair:
        """Issue a JWT access + refresh token pair.

        Access tokens are short-lived (default 30min).
        Refresh tokens are longer-lived (default 7 days).
        Both carry role and scope claims for RBAC enforcement.
        """
        try:
            import jwt
        except ImportError:
            raise ImportError(
                "PyJWT is required for JWT auth. Install with: pip install PyJWT>=2.11.0"
            )

        now = datetime.utcnow()
        access_exp = now + timedelta(minutes=self._jwt_config.access_token_ttl_minutes)
        refresh_exp = now + timedelta(days=self._jwt_config.refresh_token_ttl_days)
        token_scopes = scopes or []

        # Access token
        access_claims = JWTClaims(
            sub=subject,
            role=role,
            scopes=token_scopes,
            iss=self._jwt_config.issuer,
            aud=self._jwt_config.audience,
            iat=now,
            exp=access_exp,
            token_type="access",
        )
        access_token = jwt.encode(
            access_claims.to_payload(),
            self._jwt_config.secret_key,
            algorithm=self._jwt_config.algorithm,
        )

        # Refresh token — longer TTL, separate jti
        refresh_claims = JWTClaims(
            sub=subject,
            role=role,
            scopes=token_scopes,
            iss=self._jwt_config.issuer,
            aud=self._jwt_config.audience,
            iat=now,
            exp=refresh_exp,
            token_type="refresh",
        )
        refresh_token = jwt.encode(
            refresh_claims.to_payload(),
            self._jwt_config.secret_key,
            algorithm=self._jwt_config.algorithm,
        )

        self.log_action(
            AuditAction.TOKEN_ISSUED,
            actor=subject,
            resource_type="jwt",
            resource_id=access_claims.jti,
            detail=f"Issued token pair for role {role.value}",
            ip_address=ip_address,
        )

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._jwt_config.access_token_ttl_minutes * 60,
            role=role,
            scopes=token_scopes,
        )

    def validate_jwt(
        self, token: str, *, expect_type: str | None = "access",
    ) -> JWTClaims | None:
        """Validate a JWT and return its claims, or None if invalid.

        Checks signature, expiration, issuer, audience, required claims,
        revocation — and the token's type.

        **``expect_type`` defaults to ``"access"`` deliberately.** A
        refresh token is a valid JWT with a seven-day lifetime, so
        anything that validated a bearer credential without checking the
        type accepted one as an access token and extended a thirty-minute
        window to a week. The dashboard's auth dependency did exactly
        that until it was fixed. Making the restrictive answer the default
        means a future caller is safe without having to know this, which
        a second method named `validate_access_token` would not achieve —
        nothing would stop the next caller reaching for the old name.

        Pass ``expect_type=None`` to accept either, which only the
        refresh path and token-introspection tests should want.
        """
        try:
            import jwt
        except ImportError:
            return None

        try:
            payload = jwt.decode(
                token,
                self._jwt_config.secret_key,
                algorithms=[self._jwt_config.algorithm],
                issuer=self._jwt_config.issuer,
                audience=self._jwt_config.audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )

            # Check if token has been revoked
            jti = payload.get("jti", "")
            if jti and self._is_token_revoked(jti):
                return None

            token_type = payload.get("token_type", "access")
            if expect_type is not None and token_type != expect_type:
                return None

            return JWTClaims(
                sub=payload["sub"],
                role=Role(payload["role"]),
                scopes=payload.get("scopes", []),
                iss=payload["iss"],
                aud=payload["aud"],
                iat=datetime.utcfromtimestamp(payload["iat"]),
                exp=datetime.utcfromtimestamp(payload["exp"]),
                jti=jti,
                token_type=token_type,
            )
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError, ValueError):
            return None

    def refresh_access_token(self, refresh_token: str) -> TokenPair | None:
        """Exchange a valid refresh token for a new token pair.

        The old refresh token's jti is revoked after use (rotation).
        """
        claims = self.validate_jwt(refresh_token, expect_type="refresh")
        if not claims:
            return None

        # Revoke the old refresh token (rotation prevents replay)
        self.revoke_token(claims.jti)

        self.log_action(
            AuditAction.TOKEN_REFRESHED,
            actor=claims.sub,
            resource_type="jwt",
            resource_id=claims.jti,
            detail="Refreshed token pair",
        )

        return self.issue_token_pair(
            subject=claims.sub,
            role=claims.role,
            scopes=claims.scopes,
        )

    def revoke_token(self, jti: str) -> None:
        """Add a JWT ID to the revocation list."""
        revoked = self._load_revoked_tokens()
        revoked[jti] = datetime.utcnow().isoformat()
        self._save_revoked_tokens(revoked)

    def authenticate_api_key_to_jwt(
        self,
        raw_key: str,
        ip_address: str = "",
    ) -> TokenPair | None:
        """Authenticate with an API key, return a JWT token pair.

        This bridges API key auth → JWT auth, letting clients use short-lived
        JWTs after initial API key validation.
        """
        key = self.validate_api_key(raw_key)
        if not key:
            self.log_action(
                AuditAction.LOGIN,
                actor="unknown",
                detail="API key authentication failed",
                ip_address=ip_address,
                success=False,
                error="Invalid API key",
            )
            return None

        return self.issue_token_pair(
            subject=key.id,
            role=key.role,
            scopes=key.scopes,
            ip_address=ip_address,
        )

    def _is_token_revoked(self, jti: str) -> bool:
        """Check if a JWT has been revoked."""
        revoked = self._load_revoked_tokens()
        return jti in revoked

    def _load_revoked_tokens(self) -> dict[str, str]:
        raw = self._store.load_raw("_system", "revoked_tokens")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _save_revoked_tokens(self, tokens: dict[str, str]) -> None:
        self._store.save_raw("_system", "revoked_tokens", json.dumps(tokens))

    def _load_jwt_config(self) -> JWTConfig:
        """Load or create JWT configuration."""
        raw = self._store.load_raw("_system", "jwt_config")
        if raw:
            try:
                return JWTConfig.model_validate(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
        # Generate and persist a new config
        config = JWTConfig()
        self._store.save_raw(
            "_system", "jwt_config",
            json.dumps(config.model_dump(), default=str),
        )
        return config

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
