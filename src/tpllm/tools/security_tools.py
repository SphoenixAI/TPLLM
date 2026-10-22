"""Tool definitions for security management."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.security_manager import SecurityManager
from tpllm.models.security import AuditAction, Role


def get_security_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    """Return tool definitions for security management."""
    mgr = SecurityManager(store)

    def create_api_key(name: str, role: str = "viewer", owner: str = "", expires_days: int = 0) -> str:
        r = Role(role) if role in [r.value for r in Role] else Role.VIEWER
        exp = expires_days if expires_days > 0 else None
        key, raw = mgr.create_api_key(name, r, owner, expires_days=exp)
        return json.dumps({
            "id": key.id,
            "name": key.name,
            "prefix": key.key_prefix,
            "role": key.role.value,
            "raw_key": raw,
            "note": "Store this key securely — it cannot be retrieved again",
        })

    def list_api_keys() -> str:
        keys = mgr.list_api_keys()
        return json.dumps([{
            "id": k.id, "name": k.name, "prefix": k.key_prefix,
            "role": k.role.value, "valid": k.is_valid, "revoked": k.revoked,
        } for k in keys])

    def revoke_api_key(key_id: str, actor: str = "system") -> str:
        ok = mgr.revoke_api_key(key_id, actor)
        return json.dumps({"revoked": ok, "key_id": key_id})

    def get_audit_log(actor: str = "", program_id: str = "", limit: int = 20) -> str:
        entries = mgr.get_audit_log(actor=actor, program_id=program_id, limit=limit)
        return json.dumps([{
            "timestamp": e.timestamp.isoformat(),
            "action": e.action.value,
            "actor": e.actor,
            "resource": f"{e.resource_type}/{e.resource_id}",
            "detail": e.detail,
            "success": e.success,
        } for e in entries])

    def get_audit_summary() -> str:
        return mgr.get_audit_summary()

    def check_permission(role: str, permission: str) -> str:
        from tpllm.models.security import Permission
        r = Role(role) if role in [r.value for r in Role] else Role.VIEWER
        try:
            p = Permission(permission)
        except ValueError:
            return json.dumps({"error": f"Unknown permission: {permission}"})
        allowed = mgr.check_permission(r, p)
        return json.dumps({"role": role, "permission": permission, "allowed": allowed})

    return [
        {
            "name": "create_api_key",
            "description": "Create a new API key for dashboard/API authentication",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human-readable name for the key"},
                    "role": {"type": "string", "enum": ["admin", "manager", "engineer", "viewer"]},
                    "owner": {"type": "string", "description": "Who owns this key"},
                    "expires_days": {"type": "integer", "description": "Days until expiry (0 = no expiry)"},
                },
                "required": ["name"],
            },
            "executor": create_api_key,
        },
        {
            "name": "list_api_keys",
            "description": "List all API keys with their status",
            "input_schema": {"type": "object", "properties": {}},
            "executor": lambda: list_api_keys(),
        },
        {
            "name": "revoke_api_key",
            "description": "Revoke an API key to disable access",
            "input_schema": {
                "type": "object",
                "properties": {
                    "key_id": {"type": "string"},
                    "actor": {"type": "string"},
                },
                "required": ["key_id"],
            },
            "executor": revoke_api_key,
        },
        {
            "name": "get_audit_log",
            "description": "Query the audit log for security and compliance review",
            "input_schema": {
                "type": "object",
                "properties": {
                    "actor": {"type": "string", "description": "Filter by actor"},
                    "program_id": {"type": "string", "description": "Filter by program"},
                    "limit": {"type": "integer", "description": "Max entries to return"},
                },
            },
            "executor": get_audit_log,
        },
        {
            "name": "get_audit_summary",
            "description": "Get a human-readable summary of recent audit log activity",
            "input_schema": {"type": "object", "properties": {}},
            "executor": lambda: get_audit_summary(),
        },
        {
            "name": "check_permission",
            "description": "Check if a role has a specific permission",
            "input_schema": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["admin", "manager", "engineer", "viewer"]},
                    "permission": {"type": "string", "description": "Permission to check (e.g. 'program:read')"},
                },
                "required": ["role", "permission"],
            },
            "executor": check_permission,
        },
    ]
