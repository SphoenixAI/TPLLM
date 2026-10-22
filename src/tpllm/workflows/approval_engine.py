"""Approval Workflow Engine — formal sign-off chains with routing and escalation.

Manages:
- Creating approval requests with multi-step chains
- Routing to the next approver in sequence
- Tracking approval/rejection with timestamps and comments
- Escalating stale approvals
- Recording full audit trail
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.approval import (
    ApprovalChain,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStep,
    ApprovalType,
)


class ApprovalEngine:
    """Manage approval workflows for programs."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def create_request(
        self,
        program_id: str,
        title: str,
        approval_type: ApprovalType = ApprovalType.SCOPE_CHANGE,
        description: str = "",
        justification: str = "",
        impact_summary: str = "",
        requester: str = "",
        approvers: list[dict[str, str]] | None = None,
        deadline_hours: int = 48,
        evidence_refs: list[str] | None = None,
    ) -> ApprovalRequest:
        """Create a new approval request with an approval chain."""
        steps = []
        for i, approver_info in enumerate(approvers or []):
            steps.append(ApprovalStep(
                order=i,
                approver=approver_info.get("name", ""),
                role=approver_info.get("role", ""),
            ))

        request = ApprovalRequest(
            id=f"APR-{secrets.token_hex(4)}",
            program_id=program_id,
            title=title,
            approval_type=approval_type,
            description=description,
            justification=justification,
            impact_summary=impact_summary,
            requester=requester,
            steps=steps,
            deadline=datetime.utcnow() + timedelta(hours=deadline_hours),
            evidence_refs=evidence_refs or [],
        )

        chain = self._load_chain(program_id)
        chain.requests.append(request)
        self._save_chain(program_id, chain)

        return request

    def approve_step(
        self,
        program_id: str,
        request_id: str,
        approver: str,
        comment: str = "",
    ) -> ApprovalRequest | None:
        """Approve the current step in an approval chain."""
        chain = self._load_chain(program_id)
        request = self._find_request(chain, request_id)
        if not request or request.is_complete:
            return None

        if request.current_step >= len(request.steps):
            return None

        step = request.steps[request.current_step]
        if step.approver != approver and step.delegated_to != approver:
            return None  # not authorized

        step.status = ApprovalStatus.APPROVED
        step.decided_at = datetime.utcnow()
        step.comment = comment

        # Advance to next step or complete
        request.current_step += 1
        if request.current_step >= len(request.steps):
            request.status = ApprovalStatus.APPROVED
            request.completed_at = datetime.utcnow()
            request.final_decision = "Approved"
            request.final_comment = comment

        self._save_chain(program_id, chain)
        return request

    def reject_step(
        self,
        program_id: str,
        request_id: str,
        approver: str,
        reason: str = "",
    ) -> ApprovalRequest | None:
        """Reject at the current approval step."""
        chain = self._load_chain(program_id)
        request = self._find_request(chain, request_id)
        if not request or request.is_complete:
            return None

        if request.current_step >= len(request.steps):
            return None

        step = request.steps[request.current_step]
        if step.approver != approver and step.delegated_to != approver:
            return None

        step.status = ApprovalStatus.REJECTED
        step.decided_at = datetime.utcnow()
        step.comment = reason

        request.status = ApprovalStatus.REJECTED
        request.completed_at = datetime.utcnow()
        request.final_decision = "Rejected"
        request.final_comment = reason

        self._save_chain(program_id, chain)
        return request

    def delegate_step(
        self,
        program_id: str,
        request_id: str,
        approver: str,
        delegate_to: str,
    ) -> ApprovalRequest | None:
        """Delegate approval authority to someone else."""
        chain = self._load_chain(program_id)
        request = self._find_request(chain, request_id)
        if not request or request.is_complete:
            return None

        if request.current_step >= len(request.steps):
            return None

        step = request.steps[request.current_step]
        if step.approver != approver:
            return None

        step.delegated_to = delegate_to
        self._save_chain(program_id, chain)
        return request

    def escalate_stale(self, program_id: str, hours_threshold: int = 48) -> list[ApprovalRequest]:
        """Escalate approval requests that have been pending too long."""
        chain = self._load_chain(program_id)
        cutoff = datetime.utcnow() - timedelta(hours=hours_threshold)
        escalated = []

        for request in chain.requests:
            if request.status != ApprovalStatus.PENDING:
                continue
            if request.created_at < cutoff:
                request.status = ApprovalStatus.ESCALATED
                request.escalated_at = datetime.utcnow()
                escalated.append(request)

        if escalated:
            self._save_chain(program_id, chain)

        return escalated

    def withdraw(
        self,
        program_id: str,
        request_id: str,
        requester: str,
    ) -> ApprovalRequest | None:
        """Withdraw an approval request."""
        chain = self._load_chain(program_id)
        request = self._find_request(chain, request_id)
        if not request or request.is_complete:
            return None
        if request.requester != requester:
            return None

        request.status = ApprovalStatus.WITHDRAWN
        request.completed_at = datetime.utcnow()
        request.final_decision = "Withdrawn"

        self._save_chain(program_id, chain)
        return request

    def get_pending(self, program_id: str) -> list[ApprovalRequest]:
        """Get all pending approval requests."""
        chain = self._load_chain(program_id)
        return chain.pending

    def get_stale(self, program_id: str) -> list[ApprovalRequest]:
        """Get approval requests that are stale (pending > 48h)."""
        chain = self._load_chain(program_id)
        return chain.stale

    def get_request(self, program_id: str, request_id: str) -> ApprovalRequest | None:
        """Get a specific approval request."""
        chain = self._load_chain(program_id)
        return self._find_request(chain, request_id)

    def get_summary(self, program_id: str) -> str:
        """Get a human-readable summary of all approvals."""
        chain = self._load_chain(program_id)
        return chain.summary()

    # ── Helpers ───────────────────────────────────────────────

    def _find_request(
        self, chain: ApprovalChain, request_id: str
    ) -> ApprovalRequest | None:
        for r in chain.requests:
            if r.id == request_id:
                return r
        return None

    def _load_chain(self, program_id: str) -> ApprovalChain:
        chain = self._store.load(program_id, "approval_chain", ApprovalChain)
        if not chain:
            chain = ApprovalChain(program_id=program_id)
        return chain

    def _save_chain(self, program_id: str, chain: ApprovalChain) -> None:
        self._store.save(program_id, "approval_chain", chain)
