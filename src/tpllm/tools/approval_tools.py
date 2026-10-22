"""Tool definitions for approval workflows."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.approval import ApprovalType
from tpllm.workflows.approval_engine import ApprovalEngine


def get_approval_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    """Return tool definitions for approval workflows."""
    engine = ApprovalEngine(store)

    def create_approval(
        program_id: str,
        title: str,
        approval_type: str = "SCOPE_CHANGE",
        description: str = "",
        justification: str = "",
        impact_summary: str = "",
        requester: str = "",
        approvers: list[dict[str, str]] | None = None,
        deadline_hours: int = 48,
    ) -> str:
        atype = ApprovalType(approval_type) if approval_type in [t.value for t in ApprovalType] else ApprovalType.SCOPE_CHANGE
        req = engine.create_request(
            program_id, title, atype, description, justification,
            impact_summary, requester, approvers or [], deadline_hours,
        )
        return json.dumps({
            "id": req.id,
            "status": req.status.value,
            "next_approver": req.next_approver,
            "deadline": req.deadline.isoformat() if req.deadline else None,
        })

    def approve(program_id: str, request_id: str, approver: str, comment: str = "") -> str:
        req = engine.approve_step(program_id, request_id, approver, comment)
        if not req:
            return json.dumps({"error": "Cannot approve — not found or not authorized"})
        return json.dumps({
            "id": req.id, "status": req.status.value,
            "progress": req.approval_progress,
            "next_approver": req.next_approver,
        })

    def reject(program_id: str, request_id: str, approver: str, reason: str = "") -> str:
        req = engine.reject_step(program_id, request_id, approver, reason)
        if not req:
            return json.dumps({"error": "Cannot reject — not found or not authorized"})
        return json.dumps({
            "id": req.id, "status": req.status.value,
            "final_decision": req.final_decision,
        })

    def get_pending(program_id: str) -> str:
        pending = engine.get_pending(program_id)
        return json.dumps([{
            "id": r.id, "title": r.title, "type": r.approval_type.value,
            "requester": r.requester, "next_approver": r.next_approver,
            "progress": r.approval_progress,
        } for r in pending])

    def get_approval_summary(program_id: str) -> str:
        return engine.get_summary(program_id)

    def escalate_stale(program_id: str, hours: int = 48) -> str:
        escalated = engine.escalate_stale(program_id, hours)
        return json.dumps({
            "escalated_count": len(escalated),
            "items": [{"id": r.id, "title": r.title} for r in escalated],
        })

    return [
        {
            "name": "create_approval_request",
            "description": "Create a formal approval request with a multi-step approval chain",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "approval_type": {
                        "type": "string",
                        "enum": [t.value for t in ApprovalType],
                    },
                    "description": {"type": "string"},
                    "justification": {"type": "string"},
                    "impact_summary": {"type": "string"},
                    "requester": {"type": "string"},
                    "approvers": {
                        "type": "array",
                        "items": {"type": "object", "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string"},
                        }},
                    },
                    "deadline_hours": {"type": "integer"},
                },
                "required": ["program_id", "title"],
            },
            "executor": create_approval,
        },
        {
            "name": "approve_request",
            "description": "Approve the current step in an approval chain",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "request_id": {"type": "string"},
                    "approver": {"type": "string"},
                    "comment": {"type": "string"},
                },
                "required": ["program_id", "request_id", "approver"],
            },
            "executor": approve,
        },
        {
            "name": "reject_request",
            "description": "Reject an approval request with a reason",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "request_id": {"type": "string"},
                    "approver": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["program_id", "request_id", "approver"],
            },
            "executor": reject,
        },
        {
            "name": "get_pending_approvals",
            "description": "Get all pending approval requests for a program",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_pending,
        },
        {
            "name": "get_approval_summary",
            "description": "Get a human-readable summary of all approval workflows",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_approval_summary,
        },
        {
            "name": "escalate_stale_approvals",
            "description": "Escalate approval requests that have been pending too long",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "hours": {"type": "integer", "description": "Hours threshold (default 48)"},
                },
                "required": ["program_id"],
            },
            "executor": escalate_stale,
        },
    ]
