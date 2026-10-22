"""Vindication — override → pending → outcome-gated resolution → evidence.

Records the question an override opens, resolves it when the outcome arrives,
and writes directional evidence (for-rule or for-human) to the governance
journal. **Never mutates governance state.** It writes evidence entries;
re-review decides.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from tpllm.structural.outcomes import DecisionOutcomeLog, DecisionOutcomeType
from tpllm.advisory.surfacing import AdvisoryOverlay, EpistemicDelta


# ── Enums ──────────────────────────────────────────────────────────────

class HumanResponse(str, Enum):
    CONCURRED = "concurred"
    OVERRIDDEN = "overridden"
    NO_RESPONSE = "no_response"


class VindicationState(str, Enum):
    PENDING_VINDICATION = "pending_vindication"
    RULE_VINDICATED = "rule_vindicated"
    OVERRIDE_VINDICATED = "override_vindicated"
    UNRESOLVED_EXPIRED = "unresolved_expired"


class EvidenceDirection(str, Enum):
    FOR_RULE = "for_rule"
    FOR_HUMAN = "for_human"
    NO_SIGNAL = "no_signal"


# ── Models ─────────────────────────────────────────────────────────────

class OverrideEvent(BaseModel):
    """Recorded when a human rejects a surfaced overlay."""
    proposal_id: str = ""
    decision_id: str = ""
    at: datetime = Field(default_factory=datetime.utcnow)
    baseline_choice: str = ""
    calibrated_choice: str = ""
    human_choice: str = ""
    epistemic_delta: EpistemicDelta = Field(default_factory=EpistemicDelta)


class VindicationRecord(BaseModel):
    """The open question and its resolution."""
    proposal_id: str = ""
    decision_id: str = ""
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    state: VindicationState = VindicationState.PENDING_VINDICATION
    resolution_deadline: datetime | None = None
    resolved_outcome: DecisionOutcomeType | None = None
    resolved_at: datetime | None = None
    evidence_direction: EvidenceDirection | None = None


class GovernanceEvidenceEntry(BaseModel):
    """Written to the governance journal. Evidence, not authority change."""
    proposal_id: str = ""
    decision_id: str = ""
    direction: EvidenceDirection = EvidenceDirection.NO_SIGNAL
    outcome_label: str = ""
    at: datetime = Field(default_factory=datetime.utcnow)
    note: str = ""


# ── Outcome classification ───────────────────────────────────────────

_RULE_VINDICATED_OUTCOMES: set[DecisionOutcomeType] = {
    DecisionOutcomeType.RISK_MATERIALIZED,
    DecisionOutcomeType.WORSE_THAN_PREDICTED,
}

_OVERRIDE_VINDICATED_OUTCOMES: set[DecisionOutcomeType] = {
    DecisionOutcomeType.RISK_RESOLVED,
    DecisionOutcomeType.BETTER_THAN_PREDICTED,
}


# ── Functions ────────────────────────────────────────────────────────

def record_human_response(
    *,
    overlay: AdvisoryOverlay,
    human_choice: str,
    decision_id: str = "",
) -> tuple[HumanResponse, OverrideEvent | None]:
    """Record whether the human concurred or overrode.

    An override changes nothing about the rule — it opens a question.
    """
    if human_choice == overlay.calibrated_top_option:
        return HumanResponse.CONCURRED, None

    event = OverrideEvent(
        proposal_id=overlay.proposal_id,
        decision_id=decision_id,
        baseline_choice=str(overlay.baseline_rank),
        calibrated_choice=overlay.calibrated_top_option,
        human_choice=human_choice,
        epistemic_delta=overlay.epistemic_delta,
    )
    return HumanResponse.OVERRIDDEN, event


def open_vindication(
    *,
    override_event: OverrideEvent,
    resolution_deadline: datetime,
) -> VindicationRecord:
    """Open a pending vindication question. Does not touch governance state."""
    return VindicationRecord(
        proposal_id=override_event.proposal_id,
        decision_id=override_event.decision_id,
        state=VindicationState.PENDING_VINDICATION,
        resolution_deadline=resolution_deadline,
    )


def resolve_vindication(
    *,
    vindication: VindicationRecord,
    decision_outcome: DecisionOutcomeLog,
    now: datetime | None = None,
) -> tuple[VindicationRecord, GovernanceEvidenceEntry]:
    """Resolve a vindication from the decision outcome log.

    Writes directional evidence; never mutates governance state, status,
    confidence, or authority.
    """
    resolve_time = now or datetime.utcnow()
    outcome_type = decision_outcome.outcome_type

    if outcome_type in _RULE_VINDICATED_OUTCOMES:
        state = VindicationState.RULE_VINDICATED
        direction = EvidenceDirection.FOR_RULE
        note = f"Warned-of risk materialized: {outcome_type.value}"
    elif outcome_type in _OVERRIDE_VINDICATED_OUTCOMES:
        state = VindicationState.OVERRIDE_VINDICATED
        direction = EvidenceDirection.FOR_HUMAN
        note = f"Override was correct: {outcome_type.value}"
    else:
        state = VindicationState.UNRESOLVED_EXPIRED
        direction = EvidenceDirection.NO_SIGNAL
        note = f"No directional signal: {outcome_type.value}"

    resolved = vindication.model_copy(
        update={
            "state": state,
            "resolved_outcome": outcome_type,
            "resolved_at": resolve_time,
            "evidence_direction": direction,
        }
    )

    entry = GovernanceEvidenceEntry(
        proposal_id=vindication.proposal_id,
        decision_id=vindication.decision_id,
        direction=direction,
        outcome_label=outcome_type.value,
        at=resolve_time,
        note=note,
    )

    return resolved, entry


def expire_vindication(
    *,
    vindication: VindicationRecord,
    now: datetime | None = None,
) -> tuple[VindicationRecord, GovernanceEvidenceEntry]:
    """Deadline passed with no outcome. No directional evidence."""
    expire_time = now or datetime.utcnow()

    expired = vindication.model_copy(
        update={
            "state": VindicationState.UNRESOLVED_EXPIRED,
            "resolved_at": expire_time,
            "evidence_direction": EvidenceDirection.NO_SIGNAL,
        }
    )

    entry = GovernanceEvidenceEntry(
        proposal_id=vindication.proposal_id,
        decision_id=vindication.decision_id,
        direction=EvidenceDirection.NO_SIGNAL,
        outcome_label="deadline_expired",
        at=expire_time,
        note="Deadline passed with no outcome.",
    )

    return expired, entry
