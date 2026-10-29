"""Standing coordination authority and durable, non-repeating execution.

This gateway handles prepared actions, not arbitrary model-selected tool calls.
An ambiguous provider result is held for reconciliation, never blindly retried.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from tpllm.product_sources import digest, utc


class Delegation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    action_type: Literal["internal_update"] = "internal_update"
    provider: Literal["slack"] = "slack"
    destinations: list[str] = Field(min_length=1, max_length=20)
    source_scopes: list[str] = Field(min_length=1, max_length=30)
    purpose_code: Literal["project_delivery_status"] = "project_delivery_status"
    purpose: str = Field(min_length=8, max_length=1000)
    max_per_day: int = Field(default=3, ge=1, le=20)
    minimum_interval_minutes: int = Field(default=60, ge=1, le=1440)
    max_evidence_age_hours: int = Field(default=24, ge=1, le=168)
    expires_at: datetime
    enabled: bool = True
    granted_by: str = ""

    @model_validator(mode="after")
    def normalize(self):
        self.expires_at = utc(self.expires_at)
        if any(not d.strip() or len(d) > 200 for d in self.destinations):
            raise ValueError("Name exact approved destinations")
        return self


def eligible(ws, action, at):
    if ws.get("coordination_paused", False):
        return None, "Coordination is paused."
    if action.get("reserved") or action.get("type") != "internal_update":
        return None, "Reserved decision or unsupported execution class; human authority required."
    if action.get("uncertainty"):
        return None, "Unresolved evidence prevents automatic execution."
    records = ws.get("source_records", {})
    for ref in action.get("source_versions", []):
        source = records.get(ref["key"])
        if not source or source["version"] != ref["version"]:
            return None, "Supporting evidence changed; reassessment required."
    if not action.get("source_versions"):
        return None, "No supporting evidence."
    receipts = ws.get("action_receipts", [])
    if any(r.get("project_id") == action["project_id"] and r.get("destination") == action["destination"] and r["status"] in {"attempted", "needs_reconciliation"} for r in receipts):
        return None, "An earlier delivery to this destination needs reconciliation before further communication."
    if any(r.get("body_fingerprint") == digest(action["body"]) and r.get("project_id") == action["project_id"] and r.get("destination") == action["destination"] and r["status"] in {"attempted", "accepted", "simulated", "needs_reconciliation"} for r in receipts):
        return None, "This update was already attempted; unchanged communication suppressed."
    if any(r["action_id"] == action["id"] and r["status"] in {"attempted", "accepted", "simulated", "needs_reconciliation"} for r in receipts):
        return None, "Already attempted; duplicate delivery suppressed."
    reason = "No applicable standing delegation."
    for raw in ws.get("delegations", []):
        grant = Delegation.model_validate(raw)
        if not (grant.enabled and grant.project_id == action["project_id"] and grant.action_type == action["type"]
                and grant.provider == action["provider"] and action["destination"] in grant.destinations):
            continue
        if grant.expires_at <= at:
            reason = "Delegation has expired."
            continue
        if action.get("purpose") != grant.purpose_code:
            reason = "Action purpose is outside this delegation."
            continue
        if any(f"{records[r['key']]['provider']}:{records[r['key']]['scope']}" not in grant.source_scopes for r in action["source_versions"]):
            reason = "Supporting source visibility is outside this delegation."
            continue
        if any((at - utc(records[r["key"]]["observed_at"])).total_seconds() > grant.max_evidence_age_hours * 3600 for r in action["source_versions"]):
            reason = "Supporting evidence is stale for this delegation."
            continue
        attempts = [r for r in receipts if r["grant_id"] == grant.id]
        if sum(utc(r["at"]).date() == at.date() for r in attempts) >= grant.max_per_day:
            reason = "Daily communication limit reached."
            continue
        if attempts and (at - max(utc(r["at"]) for r in attempts)).total_seconds() < grant.minimum_interval_minutes * 60:
            reason = "Minimum communication interval has not elapsed."
            continue
        return grant, "Covered by standing delegation."
    return None, reason


def execute_prepared(ws, action, *, at, sender=None):
    # All callers hold the workspace lock through validation, reservation and
    # the external call. A persisted attempt fences crash/restart recovery.
    grant, reason = eligible(ws, action, at)
    if not grant:
        return {"status": "held", "reason": reason, "action_id": action["id"]}
    receipts = ws.get("action_receipts", [])
    receipt = {"id": str(uuid4()), "action_id": action["id"], "grant_id": grant.id,
        "project_id": action["project_id"], "destination": action["destination"],
        "purpose": grant.purpose, "at": at.isoformat(), "status": "attempted",
        "evidence": action["source_versions"], "body_fingerprint": digest(action["body"])}
    receipt["transitions"] = [{"at": at.isoformat(), "status": "attempted"}]
    receipts.append(receipt)
    ws.put("action_receipts", receipts)
    try:
        if ws.demo:
            receipt.update(status="simulated", provider_reference="demo-only", result="No external message sent.")
        else:
            if not ws.cfg.slack.enabled:
                raise RuntimeError("Provider not configured")
            if sender is None:
                from tpllm.integrations.slack_integration import SlackIntegration
                sender = SlackIntegration(ws.cfg.slack).send_message
            reference = sender(channel=action["destination"], text=action["body"])
            if not reference:
                raise RuntimeError("No provider reference")
            receipt.update(status="accepted", provider_reference=str(reference), result="Provider accepted the message. Reading or comprehension is not confirmed.")
    except Exception as exc:
        receipt.update(status="needs_reconciliation", result=f"Provider result is uncertain ({type(exc).__name__}); automatic retry disabled.")
    receipt["transitions"].append({"at": datetime.utcnow().isoformat(), "status": receipt["status"]})
    ws.put("action_receipts", receipts)
    ws.audit("Coordination action " + receipt["status"], receipt, program_id=action["project_id"], actor=grant.granted_by)
    return receipt
