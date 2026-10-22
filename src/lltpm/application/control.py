"""Bounded autonomous application — the control gate (rung three).

This is the ONLY layer in the system permitted to mutate: it registers a
governed-approved calibration as *active*, so it actually changes which
intervention is chosen, not merely decorates the payload. With
mutation comes the heaviest safety machinery in the whole arc:

    1. The single green light. Application requires governance's
       ``is_application_eligible()`` — never a looser copy.
    2. Kill switch. A manual halt stops all autonomous application
       immediately; manual override always wins.
    3. Bounded autonomy. Applications are capped per window and in total;
       the rule can never apply unboundedly.
    4. Reversibility. Every application captures a restore snapshot BEFORE
       mutating, so it can always be reverted.

What this layer must NOT do: it never edits the live ranker's source
(``tradeoff_score``), and it never grants governance authority — it does
not call ``transition_status``, ``issue_approval``, or ``renew_approval``.
Authority is granted only at human re-review. The single
autonomous authority change allowed anywhere — auto-revert — only ever
*removes* an active calibration, never grants one (monitor.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from lltpm.structural.governance import (
    CalibrationApprovalRecord,
    GovernanceJournalEntry,
    GovernanceStatus,
    fingerprint_evidence_bundle,
    is_application_eligible,
)
from lltpm.structural.stress_test import (
    ProposalScopeSignature,
    StressTestedEvidenceBundle,
)


# ── Enums ──────────────────────────────────────────────────────────────

class ApplicationDecision(str, Enum):
    APPLIED = "applied"
    BLOCKED = "blocked"


class ApplicationStatus(str, Enum):
    ACTIVE = "active"
    REVERTED = "reverted"
    SUPERSEDED = "superseded"


class KillSwitchState(str, Enum):
    ARMED = "armed"
    ENGAGED = "engaged"


class BlockReason(str, Enum):
    NOT_APPLICATION_ELIGIBLE = "not_application_eligible"
    KILL_SWITCH_ENGAGED = "kill_switch_engaged"
    AUTONOMY_BUDGET_EXHAUSTED = "autonomy_budget_exhausted"
    ALREADY_ACTIVE = "already_active"


# ── Models ─────────────────────────────────────────────────────────────

class KillSwitch(BaseModel):
    """Global halt on autonomous application. Manual override always wins."""
    state: KillSwitchState = KillSwitchState.ARMED
    engaged_by: str = ""
    engaged_at: datetime | None = None
    reason: str = ""


class AutonomyBudget(BaseModel):
    """Bounded autonomy — the rule cannot apply unboundedly.

    Governed parameter: max total active applications, and a per-window
    application cap. Construction raises on non-positive bounds.
    """
    max_active_applications: int = 1
    max_applications_per_window: int = 1
    window_decisions: int = 50
    current_active: int = 0
    applied_in_window: int = 0
    sourced_from_approval: str = ""

    def model_post_init(self, _ctx: object) -> None:
        if self.max_active_applications < 1:
            raise ValueError("max_active_applications must be >= 1.")
        if self.max_applications_per_window < 1:
            raise ValueError("max_applications_per_window must be >= 1.")
        if not self.sourced_from_approval:
            raise ValueError("sourced_from_approval must not be empty.")

    @property
    def has_capacity(self) -> bool:
        return (
            self.current_active < self.max_active_applications
            and self.applied_in_window < self.max_applications_per_window
        )


class ReversibilitySnapshot(BaseModel):
    """Captured BEFORE mutation so an application can always be reverted."""
    proposal_id: str = ""
    prior_active_calibration_id: str | None = None
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    restore_note: str = ""


class ActiveCalibrationRecord(BaseModel):
    """A calibration that is currently live and influencing decisions.

    This is the mutation. Its authority is bound to the approval fingerprint
    and expires with the approval — it is never permanent.
    """
    id: str = ""
    proposal_id: str = ""
    approval_fingerprint: str = ""
    scope_signature_id: str | None = None
    applied_at: datetime = Field(default_factory=datetime.utcnow)
    applied_by: str = ""
    status: ApplicationStatus = ApplicationStatus.ACTIVE
    reversibility_snapshot: ReversibilitySnapshot = Field(
        default_factory=ReversibilitySnapshot
    )
    decisions_at_application: int = 0


class ApplicationResult(BaseModel):
    """The outcome of attempting to apply a calibration."""
    proposal_id: str = ""
    decision: ApplicationDecision = ApplicationDecision.BLOCKED
    active_record: ActiveCalibrationRecord | None = None
    block_reason: BlockReason | None = None
    reason: str = ""
    journal_entry: GovernanceJournalEntry | None = None


# ── Kill switch ──────────────────────────────────────────────────────

def engage_kill_switch(
    *,
    kill_switch: KillSwitch,
    engaged_by: str,
    reason: str,
    now: datetime | None = None,
) -> KillSwitch:
    """Engage the global halt. Manual, immediate, always wins."""
    return kill_switch.model_copy(
        update={
            "state": KillSwitchState.ENGAGED,
            "engaged_by": engaged_by,
            "engaged_at": now or datetime.utcnow(),
            "reason": reason,
        }
    )


def arm_kill_switch(*, kill_switch: KillSwitch) -> KillSwitch:
    """Re-arm (release) the halt. A deliberate human action."""
    return kill_switch.model_copy(
        update={
            "state": KillSwitchState.ARMED,
            "engaged_by": "",
            "engaged_at": None,
            "reason": "",
        }
    )


# ── Eligibility gate ──────────────────────────────────────────────────

def can_apply(
    *,
    record: CalibrationApprovalRecord,
    bundle: StressTestedEvidenceBundle,
    kill_switch: KillSwitch,
    budget: AutonomyBudget,
    now: datetime,
    current_decision_count: int,
    already_active: bool = False,
) -> tuple[bool, BlockReason | None, str]:
    """Compose every gate. Returns (allowed, block_reason, explanation).

    Order: kill switch (manual override wins) → single green light →
    autonomy budget → already-active. The single green light is governance's
    ``is_application_eligible`` reused verbatim, never reimplemented looser.
    """
    if kill_switch.state == KillSwitchState.ENGAGED:
        return False, BlockReason.KILL_SWITCH_ENGAGED, (
            f"Kill switch engaged by {kill_switch.engaged_by}: {kill_switch.reason}"
        )

    if not is_application_eligible(
        record=record,
        bundle=bundle,
        now=now,
        current_decision_count=current_decision_count,
    ):
        return False, BlockReason.NOT_APPLICATION_ELIGIBLE, (
            "Approval is not application-eligible (no single green light)."
        )

    if already_active:
        return False, BlockReason.ALREADY_ACTIVE, (
            "A calibration for this proposal is already active."
        )

    if not budget.has_capacity:
        return False, BlockReason.AUTONOMY_BUDGET_EXHAUSTED, (
            f"Autonomy budget exhausted "
            f"(active {budget.current_active}/{budget.max_active_applications}, "
            f"window {budget.applied_in_window}/{budget.max_applications_per_window})."
        )

    return True, None, "All application gates passed."


# ── Application ───────────────────────────────────────────────────────

def apply_calibration(
    *,
    record: CalibrationApprovalRecord,
    bundle: StressTestedEvidenceBundle,
    kill_switch: KillSwitch,
    budget: AutonomyBudget,
    now: datetime,
    current_decision_count: int,
    applied_by: str = "application_engine",
    scope_signature: ProposalScopeSignature | None = None,
    already_active: bool = False,
    prior_active_calibration_id: str | None = None,
) -> tuple[ApplicationResult, AutonomyBudget]:
    """Gate, snapshot, then register the calibration as active.

    The reversibility snapshot is captured BEFORE the active record is
    created. On any block, no mutation occurs and the budget is returned
    unchanged. ``already_active`` is the registry's view of whether this
    proposal already has a live calibration; ``prior_active_calibration_id``
    is whatever the snapshot must restore to (a superseded calibration, if
    any). This function never grants governance authority and never edits
    the live ranker.
    """
    allowed, block_reason, explanation = can_apply(
        record=record,
        bundle=bundle,
        kill_switch=kill_switch,
        budget=budget,
        now=now,
        current_decision_count=current_decision_count,
        already_active=already_active,
    )

    if not allowed:
        result = ApplicationResult(
            proposal_id=record.proposal_id,
            decision=ApplicationDecision.BLOCKED,
            block_reason=block_reason,
            reason=explanation,
            journal_entry=GovernanceJournalEntry(
                proposal_id=record.proposal_id,
                from_status=GovernanceStatus.APPROVED,
                to_status=GovernanceStatus.APPROVED,
                at=now,
                actor=applied_by,
                reason=f"Application blocked: {explanation}",
            ),
        )
        return result, budget

    snapshot = ReversibilitySnapshot(
        proposal_id=record.proposal_id,
        prior_active_calibration_id=prior_active_calibration_id,
        captured_at=now,
        restore_note=(
            "Restore by deactivating this active calibration; no prior "
            "calibration to reinstate."
            if prior_active_calibration_id is None
            else f"Restore by reinstating {prior_active_calibration_id}."
        ),
    )

    active = ActiveCalibrationRecord(
        id=f"active-{uuid4().hex[:8]}",
        proposal_id=record.proposal_id,
        approval_fingerprint=fingerprint_evidence_bundle(bundle),
        scope_signature_id=(
            scope_signature.id if scope_signature is not None else record.proposal_id
        ),
        applied_at=now,
        applied_by=applied_by,
        status=ApplicationStatus.ACTIVE,
        reversibility_snapshot=snapshot,
        decisions_at_application=current_decision_count,
    )

    new_budget = budget.model_copy(
        update={
            "current_active": budget.current_active + 1,
            "applied_in_window": budget.applied_in_window + 1,
        }
    )

    result = ApplicationResult(
        proposal_id=record.proposal_id,
        decision=ApplicationDecision.APPLIED,
        active_record=active,
        reason="Calibration applied; reversibility snapshot captured.",
        journal_entry=GovernanceJournalEntry(
            proposal_id=record.proposal_id,
            from_status=GovernanceStatus.APPROVED,
            to_status=GovernanceStatus.APPROVED,
            at=now,
            actor=applied_by,
            reason=f"Calibration applied as active record {active.id}.",
        ),
    )
    return result, new_budget
