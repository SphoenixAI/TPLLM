"""Application layer — bounded autonomous control + auto-revert (rung three).

The only layer permitted to mutate. A governed-approved calibration becomes
*active* and actually changes which intervention is chosen — guarded by the
single green light, a kill switch, bounded autonomy, mandatory
reversibility, governed outcome monitoring, and removal-only auto-revert.

It never grants governance authority and never edits the live ranker source.
"""

from tpllm.application.control import (
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
from tpllm.application.monitor import (
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
from tpllm.application.ledger import (
    ApplicationLedger,
    ApplicationLedgerEntry,
    LedgerEntryType,
    append_application,
    append_revert,
    build_application_audit,
)
from tpllm.application.registry import (
    ActiveCalibrationRegistry,
    ActiveRegistryStatus,
    CalibrationAdjustment,
    CalibrationAdjustmentDirection,
    CalibrationApplicationDecision,
    CalibrationConsultContext,
    CalibrationConsultResult,
    RankerCalibrationTrace,
    build_ranker_calibration_trace,
    calculate_bounded_adjustment,
    combine_adjustments,
    consult_active_calibrations,
    deactivate_active_calibration,
    get_active_calibrations_for_context,
    is_record_active,
    record_calibration_trace,
    register_active_calibration,
    scope_matches,
)
from tpllm.application.ranker_adapter import (
    rank_options_with_active_calibrations,
    score_option_with_active_calibrations,
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
    # Registry (consult layer)
    "ActiveCalibrationRegistry",
    "ActiveRegistryStatus",
    "CalibrationAdjustment",
    "CalibrationAdjustmentDirection",
    "CalibrationApplicationDecision",
    "CalibrationConsultContext",
    "CalibrationConsultResult",
    "RankerCalibrationTrace",
    "build_ranker_calibration_trace",
    "calculate_bounded_adjustment",
    "combine_adjustments",
    "consult_active_calibrations",
    "deactivate_active_calibration",
    "get_active_calibrations_for_context",
    "is_record_active",
    "record_calibration_trace",
    "register_active_calibration",
    "scope_matches",
    # Ranker adapter (consult point)
    "rank_options_with_active_calibrations",
    "score_option_with_active_calibrations",
]
