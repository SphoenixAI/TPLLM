"""Scope Lock tools — change control and scope management."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.scope_lock import ScopeLock


def get_scope_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = ScopeLock(store)

    def add_scope_item(
        program_id: str, title: str, description: str = "",
        owner: str = "", target_date: str | None = None, locked: bool = False,
    ) -> str:
        item = engine.add_scope_item(program_id, title, description, owner, target_date, locked)
        return json.dumps({"id": item.id, "title": item.title, "locked": item.is_locked})

    def lock_scope_item(program_id: str, item_id: str) -> str:
        return json.dumps({"success": engine.lock_item(program_id, item_id)})

    def freeze_scope(program_id: str) -> str:
        return json.dumps({"success": engine.freeze_scope(program_id), "message": "All scope items are now locked."})

    def unfreeze_scope(program_id: str) -> str:
        return json.dumps({"success": engine.unfreeze_scope(program_id)})

    def request_scope_change(
        program_id: str, title: str, description: str = "",
        change_type: str = "MODIFY_REQUIREMENT", requested_by: str = "",
        justification: str = "", business_value: str = "",
        affected_items: list[str] | None = None, new_timeline: str | None = None,
        approver: str = "",
    ) -> str:
        scr = engine.request_change(
            program_id, title, description, change_type, requested_by,
            justification, business_value, affected_items, new_timeline, "", approver,
        )
        return json.dumps({
            "id": scr.id, "title": scr.title, "status": scr.status.value,
            "impact": scr.impact.overall_impact.value,
            "summary": scr.summary(),
        })

    def approve_scope_change(program_id: str, scr_id: str, approved_by: str, rationale: str = "") -> str:
        return json.dumps({"success": engine.approve_change(program_id, scr_id, approved_by, rationale)})

    def reject_scope_change(program_id: str, scr_id: str, rejected_by: str, reason: str) -> str:
        return json.dumps({"success": engine.reject_change(program_id, scr_id, rejected_by, reason)})

    def get_scope_summary(program_id: str) -> str:
        return engine.get_summary(program_id)

    def validate_scope_change(program_id: str, item_id: str) -> str:
        return json.dumps(engine.validate_change(program_id, item_id))

    return [
        {
            "name": "add_scope_item",
            "description": "Add an item to the scope baseline. Lock it to require formal change requests.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "owner": {"type": "string"},
                    "target_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "locked": {"type": "boolean", "description": "Lock this item (changes require SCR)"},
                },
                "required": ["program_id", "title"],
            },
            "executor": add_scope_item,
        },
        {
            "name": "lock_scope_item",
            "description": "Lock a scope item — any changes now require a formal Scope Change Request.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "item_id": {"type": "string"},
                },
                "required": ["program_id", "item_id"],
            },
            "executor": lock_scope_item,
        },
        {
            "name": "freeze_scope",
            "description": "Freeze ALL scope for a program. Every change requires a formal SCR. Use for code freeze / launch prep.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": freeze_scope,
        },
        {
            "name": "unfreeze_scope",
            "description": "Unfreeze scope — normal change process resumes.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": unfreeze_scope,
        },
        {
            "name": "request_scope_change",
            "description": (
                "File a Scope Change Request. Auto-generates impact analysis. "
                "Requires justification and business value. Routed to approver."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "change_type": {
                        "type": "string",
                        "enum": ["ADD_FEATURE", "REMOVE_FEATURE", "MODIFY_REQUIREMENT",
                                 "TIMELINE_CHANGE", "RESOURCE_CHANGE", "DEPENDENCY_CHANGE",
                                 "OWNERSHIP_CHANGE"],
                    },
                    "requested_by": {"type": "string"},
                    "justification": {"type": "string", "description": "Why this change is needed"},
                    "business_value": {"type": "string", "description": "What value this adds"},
                    "affected_items": {"type": "array", "items": {"type": "string"}},
                    "new_timeline": {"type": "string", "description": "YYYY-MM-DD if timeline change"},
                    "approver": {"type": "string", "description": "Who needs to approve"},
                },
                "required": ["program_id", "title", "requested_by", "justification"],
            },
            "executor": request_scope_change,
        },
        {
            "name": "approve_scope_change",
            "description": "Approve a scope change request.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "scr_id": {"type": "string"},
                    "approved_by": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["program_id", "scr_id", "approved_by"],
            },
            "executor": approve_scope_change,
        },
        {
            "name": "reject_scope_change",
            "description": "Reject a scope change request with a reason.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "scr_id": {"type": "string"},
                    "rejected_by": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["program_id", "scr_id", "rejected_by", "reason"],
            },
            "executor": reject_scope_change,
        },
        {
            "name": "get_scope_summary",
            "description": "Get scope baseline summary with pending changes.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_scope_summary,
        },
        {
            "name": "validate_scope_change",
            "description": "Check if changing a scope item requires a formal change request.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "item_id": {"type": "string"},
                },
                "required": ["program_id", "item_id"],
            },
            "executor": validate_scope_change,
        },
    ]
