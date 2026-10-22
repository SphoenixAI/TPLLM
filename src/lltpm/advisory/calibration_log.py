"""Symmetric two-sided per-rule calibration record + refactor signal.

Both directions are counted: warnings ignored where the warned-of thing
happened (rule vindicated) AND warnings ignored where the override was
correct (rule cried wolf). No one-sided "cost of ignoring me" metric.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from lltpm.advisory.vindication import (
    EvidenceDirection,
    GovernanceEvidenceEntry,
    HumanResponse,
    VindicationRecord,
    VindicationState,
)


# ── Enums ──────────────────────────────────────────────────────────────

class RefactorSignalStatus(str, Enum):
    PROPOSED = "proposed"


# ── Models ─────────────────────────────────────────────────────────────

class RuleCalibrationLog(BaseModel):
    """Symmetric two-sided record per rule."""
    proposal_id: str = ""
    surfaced_count: int = 0
    concurred_good: int = 0
    concurred_bad: int = 0
    overridden_rule_vindicated: int = 0
    overridden_override_vindicated: int = 0
    pending_count: int = 0
    unresolved_count: int = 0

    @property
    def cry_wolf_rate(self) -> float:
        """Fraction of resolved overrides where override was correct."""
        total = self.overridden_rule_vindicated + self.overridden_override_vindicated
        if total == 0:
            return 0.0
        return self.overridden_override_vindicated / total

    @property
    def vindication_rate(self) -> float:
        """Fraction of resolved overrides where rule was correct."""
        total = self.overridden_rule_vindicated + self.overridden_override_vindicated
        if total == 0:
            return 0.0
        return self.overridden_rule_vindicated / total


class RefactorSignal(BaseModel):
    """Recurring-warning root-cause proposal. Never auto-filed."""
    proposal_id: str = ""
    fire_count: int = 0
    window: int = 0
    status: RefactorSignalStatus = RefactorSignalStatus.PROPOSED
    suggested_root_cause: str = ""
    proposed_ticket_text: str = ""


# ── Functions ────────────────────────────────────────────────────────

def update_calibration_log(
    *,
    log: RuleCalibrationLog,
    response: HumanResponse,
    vindication: VindicationRecord | None = None,
) -> RuleCalibrationLog:
    """Increment the correct quadrant. Symmetric by construction."""
    updated = log.model_copy()

    if response == HumanResponse.CONCURRED:
        if vindication is not None and vindication.evidence_direction is not None:
            if vindication.evidence_direction == EvidenceDirection.FOR_RULE:
                updated.concurred_bad += 1
            elif vindication.evidence_direction == EvidenceDirection.FOR_HUMAN:
                updated.concurred_good += 1
        else:
            updated.concurred_good += 1

    elif response == HumanResponse.OVERRIDDEN:
        if vindication is None or vindication.state == VindicationState.PENDING_VINDICATION:
            updated.pending_count += 1
        elif vindication.state == VindicationState.RULE_VINDICATED:
            updated.overridden_rule_vindicated += 1
        elif vindication.state == VindicationState.OVERRIDE_VINDICATED:
            updated.overridden_override_vindicated += 1
        elif vindication.state == VindicationState.UNRESOLVED_EXPIRED:
            updated.unresolved_count += 1

    return updated


def detect_refactor_signal(
    *,
    log: RuleCalibrationLog,
    window: int,
    fire_threshold: int,
) -> RefactorSignal | None:
    """If surfaced-fire count within window exceeds threshold, propose refactor.

    Returns a proposal object only — never files a ticket.
    """
    if log.surfaced_count < fire_threshold:
        return None

    return RefactorSignal(
        proposal_id=log.proposal_id,
        fire_count=log.surfaced_count,
        window=window,
        status=RefactorSignalStatus.PROPOSED,
        suggested_root_cause=(
            f"Rule {log.proposal_id} has fired {log.surfaced_count} times "
            f"(threshold={fire_threshold}). Recurring pattern suggests a "
            f"structural root cause worth investigating."
        ),
        proposed_ticket_text=(
            f"Investigate structural root cause for recurring advisory "
            f"surfacing of rule {log.proposal_id}. "
            f"Fired {log.surfaced_count} times in window of {window} decisions."
        ),
    )


def build_rereview_evidence(
    *,
    proposal_id: str,
    journal_entries: list[GovernanceEvidenceEntry],
    calibration_log: RuleCalibrationLog,
) -> dict:
    """Assemble two-sided evidence for the next governance re-review.

    This feeds the governance renewal dossier. Contains vindicated count,
    cry-wolf count, cry-wolf rate, pending/unresolved, and the recent
    evidence entries. This is what a human reads to decide renewal.
    """
    for_rule_entries = [
        e for e in journal_entries if e.direction == EvidenceDirection.FOR_RULE
    ]
    for_human_entries = [
        e for e in journal_entries if e.direction == EvidenceDirection.FOR_HUMAN
    ]
    no_signal_entries = [
        e for e in journal_entries if e.direction == EvidenceDirection.NO_SIGNAL
    ]

    return {
        "proposal_id": proposal_id,
        "vindicated_count": calibration_log.overridden_rule_vindicated,
        "cry_wolf_count": calibration_log.overridden_override_vindicated,
        "cry_wolf_rate": calibration_log.cry_wolf_rate,
        "vindication_rate": calibration_log.vindication_rate,
        "concurred_good": calibration_log.concurred_good,
        "concurred_bad": calibration_log.concurred_bad,
        "pending_count": calibration_log.pending_count,
        "unresolved_count": calibration_log.unresolved_count,
        "surfaced_count": calibration_log.surfaced_count,
        "for_rule_entries": [e.model_dump() for e in for_rule_entries],
        "for_human_entries": [e.model_dump() for e in for_human_entries],
        "no_signal_entries": [e.model_dump() for e in no_signal_entries],
        "total_evidence_entries": len(journal_entries),
    }
