"""Live outcome monitoring + auto-revert for active calibrations.

Once a calibration is applied (control.py), its real-world outcomes are
monitored. This module hosts the single autonomous action permitted
anywhere in the system: **auto-revert**. It is safe by construction because
it only ever moves in the removal direction — it deactivates an active
calibration on regression. It never grants authority, never promotes, never
hardens, never escalates. Granting always requires a human (Commit 7).

The regression threshold that decides "is this regressing?" is a governed
parameter, bounded and sourced from the approval — the system cannot curate
its own track record by silently choosing when to pull the trigger.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from lltpm.structural.outcomes import DecisionOutcomeLog, DecisionOutcomeType
from lltpm.application.control import (
    ActiveCalibrationRecord,
    ApplicationStatus,
)


# ── Enums ──────────────────────────────────────────────────────────────

class MonitorVerdict(str, Enum):
    HEALTHY = "healthy"
    REGRESSING = "regressing"
    INSUFFICIENT_DATA = "insufficient_data"


class RevertTrigger(str, Enum):
    OUTCOME_REGRESSION = "outcome_regression"
    KILL_SWITCH = "kill_switch"
    MANUAL = "manual"
    APPROVAL_EXPIRED = "approval_expired"


# ── Outcome classification ───────────────────────────────────────────

_REGRESSION_OUTCOMES: set[DecisionOutcomeType] = {
    DecisionOutcomeType.RISK_MATERIALIZED,
    DecisionOutcomeType.WORSE_THAN_PREDICTED,
}

_HEALTHY_OUTCOMES: set[DecisionOutcomeType] = {
    DecisionOutcomeType.RISK_RESOLVED,
    DecisionOutcomeType.BETTER_THAN_PREDICTED,
}


# ── Models ─────────────────────────────────────────────────────────────

class RegressionThreshold(BaseModel):
    """Governed auto-revert trigger — never a bare float."""
    max_regression_rate: float
    min_bound: float = 0.0
    max_bound: float = 1.0
    min_samples: int = 3
    sourced_from_approval: str = ""

    @model_validator(mode="after")
    def _check(self) -> "RegressionThreshold":
        if not self.sourced_from_approval:
            raise ValueError("sourced_from_approval must not be empty.")
        if not (self.min_bound <= self.max_regression_rate <= self.max_bound):
            raise ValueError(
                f"max_regression_rate {self.max_regression_rate} "
                f"outside bounds [{self.min_bound}, {self.max_bound}]."
            )
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1.")
        return self


class ApplicationOutcomeWindow(BaseModel):
    """Rolling post-application outcomes for one active calibration."""
    active_calibration_id: str = ""
    proposal_id: str = ""
    regression_count: int = 0
    healthy_count: int = 0
    neutral_count: int = 0

    @property
    def sample_count(self) -> int:
        return self.regression_count + self.healthy_count + self.neutral_count

    @property
    def regression_rate(self) -> float:
        decisive = self.regression_count + self.healthy_count
        if decisive == 0:
            return 0.0
        return self.regression_count / decisive


class MonitorReport(BaseModel):
    """Health verdict for an active calibration."""
    active_calibration_id: str = ""
    proposal_id: str = ""
    verdict: MonitorVerdict = MonitorVerdict.INSUFFICIENT_DATA
    regression_rate: float = 0.0
    sample_count: int = 0
    recommend_revert: bool = False
    reason: str = ""


class RevertRecord(BaseModel):
    """An active calibration deactivated. Authority removed, never granted."""
    active_calibration_id: str = ""
    proposal_id: str = ""
    trigger: RevertTrigger = RevertTrigger.OUTCOME_REGRESSION
    reverted_at: datetime = Field(default_factory=datetime.utcnow)
    restored_prior_calibration_id: str | None = None
    regression_rate_at_revert: float = 0.0
    note: str = ""


# ── Outcome recording ─────────────────────────────────────────────────

def record_application_outcome(
    *,
    window: ApplicationOutcomeWindow,
    decision_outcome: DecisionOutcomeLog,
) -> ApplicationOutcomeWindow:
    """Fold one Commit 3 outcome into the monitoring window. Deterministic."""
    outcome_type = decision_outcome.outcome_type

    if outcome_type in _REGRESSION_OUTCOMES:
        return window.model_copy(
            update={"regression_count": window.regression_count + 1}
        )
    if outcome_type in _HEALTHY_OUTCOMES:
        return window.model_copy(
            update={"healthy_count": window.healthy_count + 1}
        )
    return window.model_copy(update={"neutral_count": window.neutral_count + 1})


# ── Health evaluation ─────────────────────────────────────────────────

def evaluate_application_health(
    *,
    window: ApplicationOutcomeWindow,
    threshold: RegressionThreshold,
) -> MonitorReport:
    """Judge an active calibration against the governed regression threshold.

    The threshold arrives only as a ``RegressionThreshold`` object — a bare
    float is rejected at the type level.
    """
    decisive = window.regression_count + window.healthy_count

    if decisive < threshold.min_samples:
        return MonitorReport(
            active_calibration_id=window.active_calibration_id,
            proposal_id=window.proposal_id,
            verdict=MonitorVerdict.INSUFFICIENT_DATA,
            regression_rate=window.regression_rate,
            sample_count=window.sample_count,
            recommend_revert=False,
            reason=(
                f"Only {decisive} decisive samples; "
                f"need {threshold.min_samples}."
            ),
        )

    if window.regression_rate > threshold.max_regression_rate:
        return MonitorReport(
            active_calibration_id=window.active_calibration_id,
            proposal_id=window.proposal_id,
            verdict=MonitorVerdict.REGRESSING,
            regression_rate=window.regression_rate,
            sample_count=window.sample_count,
            recommend_revert=True,
            reason=(
                f"Regression rate {window.regression_rate:.2f} "
                f"> threshold {threshold.max_regression_rate:.2f}."
            ),
        )

    return MonitorReport(
        active_calibration_id=window.active_calibration_id,
        proposal_id=window.proposal_id,
        verdict=MonitorVerdict.HEALTHY,
        regression_rate=window.regression_rate,
        sample_count=window.sample_count,
        recommend_revert=False,
        reason=(
            f"Regression rate {window.regression_rate:.2f} "
            f"within threshold {threshold.max_regression_rate:.2f}."
        ),
    )


# ── Reversal ──────────────────────────────────────────────────────────

def revert_application(
    *,
    active_record: ActiveCalibrationRecord,
    trigger: RevertTrigger,
    now: datetime | None = None,
    regression_rate: float = 0.0,
    note: str = "",
) -> tuple[ActiveCalibrationRecord, RevertRecord]:
    """Deactivate an active calibration. Removal-only — never grants authority.

    Restores the reversibility snapshot captured at application time. The
    governance approval is untouched; only the application-layer active
    record moves to REVERTED.
    """
    revert_time = now or datetime.utcnow()
    snapshot = active_record.reversibility_snapshot

    reverted = active_record.model_copy(
        update={"status": ApplicationStatus.REVERTED}
    )

    revert_record = RevertRecord(
        active_calibration_id=active_record.id,
        proposal_id=active_record.proposal_id,
        trigger=trigger,
        reverted_at=revert_time,
        restored_prior_calibration_id=snapshot.prior_active_calibration_id,
        regression_rate_at_revert=regression_rate,
        note=note or f"Reverted via {trigger.value}.",
    )

    return reverted, revert_record


def auto_revert_if_regressing(
    *,
    active_record: ActiveCalibrationRecord,
    window: ApplicationOutcomeWindow,
    threshold: RegressionThreshold,
    now: datetime | None = None,
) -> tuple[ActiveCalibrationRecord, RevertRecord | None]:
    """The single autonomous action in the system. Removal-only.

    If the monitor verdict is REGRESSING, the active calibration is
    auto-reverted (authority removed). HEALTHY or INSUFFICIENT_DATA leaves
    it active and untouched. This never grants, promotes, or hardens.
    """
    report = evaluate_application_health(window=window, threshold=threshold)

    if not report.recommend_revert:
        return active_record, None

    return revert_application(
        active_record=active_record,
        trigger=RevertTrigger.OUTCOME_REGRESSION,
        now=now,
        regression_rate=report.regression_rate,
        note=report.reason,
    )
