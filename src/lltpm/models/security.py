"""Security models — RBAC, API keys, JWT tokens, audit logging, session management."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Role(str, Enum):
    """Access control roles."""

    ADMIN = "admin"
    MANAGER = "manager"
    ENGINEER = "engineer"
    VIEWER = "viewer"


class Permission(str, Enum):
    """Granular permissions for resource access."""

    # Program management
    PROGRAM_READ = "program:read"
    PROGRAM_WRITE = "program:write"
    PROGRAM_DELETE = "program:delete"

    # Artifacts
    ARTIFACT_READ = "artifact:read"
    ARTIFACT_WRITE = "artifact:write"

    # People / evaluations
    EVALUATION_READ = "evaluation:read"
    EVALUATION_WRITE = "evaluation:write"

    # Dashboard
    DASHBOARD_ACCESS = "dashboard:access"
    DASHBOARD_ADMIN = "dashboard:admin"

    # Integrations
    INTEGRATION_CONFIGURE = "integration:configure"

    # Audit
    AUDIT_READ = "audit:read"

    # Agent
    AGENT_INTERACT = "agent:interact"
    AGENT_CONFIGURE = "agent:configure"


# Default permission sets per role
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),  # all permissions
    Role.MANAGER: {
        Permission.PROGRAM_READ, Permission.PROGRAM_WRITE,
        Permission.ARTIFACT_READ, Permission.ARTIFACT_WRITE,
        Permission.EVALUATION_READ, Permission.EVALUATION_WRITE,
        Permission.DASHBOARD_ACCESS,
        Permission.AUDIT_READ,
        Permission.AGENT_INTERACT,
    },
    Role.ENGINEER: {
        Permission.PROGRAM_READ,
        Permission.ARTIFACT_READ,
        Permission.DASHBOARD_ACCESS,
        Permission.AGENT_INTERACT,
    },
    Role.VIEWER: {
        Permission.PROGRAM_READ,
        Permission.ARTIFACT_READ,
        Permission.DASHBOARD_ACCESS,
    },
}


class APIKey(BaseModel):
    """An API key for authenticating with the dashboard/API."""

    id: str = Field(default_factory=lambda: f"key_{secrets.token_hex(4)}")
    name: str  # human-readable label
    key_hash: str = ""  # SHA-256 hash of the actual key
    key_prefix: str = ""  # first 8 chars for identification
    role: Role = Role.VIEWER
    owner: str = ""
    scopes: list[str] = Field(default_factory=list)  # optional program_id restrictions
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    revoked: bool = False
    revoked_at: Optional[datetime] = None

    @property
    def is_valid(self) -> bool:
        if self.revoked:
            return False
        if self.expires_at and self.expires_at < datetime.utcnow():
            return False
        return True

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @staticmethod
    def generate_raw_key() -> str:
        return f"lltpm_{secrets.token_urlsafe(32)}"


class OAuthToken(BaseModel):
    """Stored OAuth token for an integration."""

    integration: str  # e.g. "slack", "google", "zoom"
    access_token_hash: str = ""  # hashed for storage
    refresh_token_hash: str = ""
    token_type: str = "Bearer"
    expires_at: Optional[datetime] = None
    scopes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    refreshed_at: Optional[datetime] = None

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return self.expires_at < datetime.utcnow()


class JWTConfig(BaseModel):
    """JWT authentication configuration."""

    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30  # short-lived access tokens
    refresh_token_ttl_days: int = 7
    issuer: str = "lltpm"
    audience: str = "lltpm-api"


class JWTClaims(BaseModel):
    """Standard + custom JWT claims."""

    sub: str  # subject (user or key ID)
    role: Role
    scopes: list[str] = Field(default_factory=list)
    iss: str = "lltpm"
    aud: str = "lltpm-api"
    iat: datetime = Field(default_factory=datetime.utcnow)
    exp: datetime = Field(
        default_factory=lambda: datetime.utcnow() + timedelta(minutes=30)
    )
    jti: str = Field(default_factory=lambda: secrets.token_hex(8))  # JWT ID for revocation
    token_type: str = "access"  # "access" or "refresh"

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.exp

    def to_payload(self) -> dict:
        """Convert to JWT-encodable dict."""
        return {
            "sub": self.sub,
            "role": self.role.value,
            "scopes": self.scopes,
            "iss": self.iss,
            "aud": self.aud,
            "iat": int(self.iat.timestamp()),
            "exp": int(self.exp.timestamp()),
            "jti": self.jti,
            "token_type": self.token_type,
        }


class TokenPair(BaseModel):
    """Access + refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # seconds until access token expires
    role: Role
    scopes: list[str] = Field(default_factory=list)


class AuditAction(str, Enum):
    """Types of auditable actions."""

    # Data access
    READ = "read"
    WRITE = "write"
    DELETE = "delete"

    # Agent actions
    TOOL_CALL = "tool_call"
    DECISION = "decision"
    ESCALATION = "escalation"
    NOTIFICATION = "notification"

    # Auth events
    LOGIN = "login"
    LOGOUT = "logout"
    KEY_CREATED = "key_created"
    KEY_REVOKED = "key_revoked"
    TOKEN_ISSUED = "token_issued"
    TOKEN_REVOKED = "token_revoked"
    TOKEN_REFRESHED = "token_refreshed"

    # Configuration
    CONFIG_CHANGE = "config_change"
    INTEGRATION_CHANGE = "integration_change"


class AuditEntry(BaseModel):
    """Immutable audit log entry."""

    id: str = Field(default_factory=lambda: secrets.token_hex(8))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    action: AuditAction
    actor: str  # who performed the action (user, api_key, agent)
    resource_type: str = ""  # e.g. "program", "decision", "evaluation"
    resource_id: str = ""
    program_id: str = ""
    detail: str = ""
    ip_address: str = ""
    success: bool = True
    error: str = ""

    def summary(self) -> str:
        status = "OK" if self.success else f"FAIL: {self.error}"
        return (
            f"[{self.timestamp.isoformat()}] {self.action.value} "
            f"by {self.actor} on {self.resource_type}/{self.resource_id} — {status}"
        )


class AuditLog(BaseModel):
    """Collection of audit entries."""

    entries: list[AuditEntry] = Field(default_factory=list)

    def add(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def query(
        self,
        actor: str = "",
        action: AuditAction | None = None,
        program_id: str = "",
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        results = self.entries
        if actor:
            results = [e for e in results if e.actor == actor]
        if action:
            results = [e for e in results if e.action == action]
        if program_id:
            results = [e for e in results if e.program_id == program_id]
        if since:
            results = [e for e in results if e.timestamp >= since]
        return sorted(results, key=lambda e: e.timestamp, reverse=True)[:limit]

    def summary(self, limit: int = 20) -> str:
        recent = sorted(self.entries, key=lambda e: e.timestamp, reverse=True)[:limit]
        lines = ["# Audit Log (recent)"]
        for e in recent:
            lines.append(f"- {e.summary()}")
        return "\n".join(lines)
