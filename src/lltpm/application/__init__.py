"""Application layer — bounded autonomous control + auto-revert (rung three).

The only layer permitted to mutate. A governed-approved calibration becomes
*active* and actually changes which intervention is chosen — guarded by the
single green light (Commit 7), a kill switch, bounded autonomy, mandatory
reversibility, governed outcome monitoring, and removal-only auto-revert.

It never grants governance authority and never edits the live ranker source.
"""

from lltpm.application.control import (
    ActiveCalibrationRecord,
    ApplicationDecision,
    ApplicationResult,
    ApplicationStatus,
    AutonomyBudget,
    BlockReason,
    KillSwitch,
    KillSwitchState,
    ReversibilitySnapshot,
    apply_calibration,
    arm_kill_switch,
    can_apply,
    engage_kill_switch,
)
from lltpm.application.monitor import (
    ApplicationOutcomeWindow,
    MonitorReport,
    MonitorVerdict,
    RegressionThreshold,
    RevertRecord,
    RevertTrigger,
    auto_revert_if_regressing,
    evaluate_application_health,
    record_application_outcome,
    revert_application,
)
from lltpm.application.ledger import (
    ApplicationLedger,
    ApplicationLedgerEntry,
    LedgerEntryType,
    append_application,
    append_revert,
    build_application_audit,
)

__all__ = [
    # Control
    "ActiveCalibrationRecord",
    "ApplicationDecision",
    "ApplicationResult",
    "ApplicationStatus",
    "AutonomyBudget",
    "BlockReason",
    "KillSwitch",
    "KillSwitchState",
    "ReversibilitySnapshot",
    "apply_calibration",
    "arm_kill_switch",
    "can_apply",
    "engage_kill_switch",
    # Monitor
    "ApplicationOutcomeWindow",
    "MonitorReport",
    "MonitorVerdict",
    "RegressionThreshold",
    "RevertRecord",
    "RevertTrigger",
    "auto_revert_if_regressing",
    "evaluate_application_health",
    "record_application_outcome",
    "revert_application",
    # Ledger
    "ApplicationLedger",
    "ApplicationLedgerEntry",
    "LedgerEntryType",
    "append_application",
    "append_revert",
    "build_application_audit",
]
