"""Advisory layer — asymmetric surfacing, vindication, calibration logging.

Rung two of three: the rule runs live and visible as a recommendation,
never decides anything, and earns trust by accumulating an honest,
two-sided track record that the next re-review reads.

Zero live-ranker mutation. Zero governance-state mutation. Advisory only.
"""

from tpllm.advisory.surfacing import (
    AdvisoryEvaluation,
    AdvisoryOverlay,
    AdvisoryThreshold,
    EpistemicDelta,
    SurfacingDecision,
    compute_epistemic_delta,
    evaluate_advisory_surfacing,
    is_advisory_eligible,
)
from tpllm.advisory.vindication import (
    EvidenceDirection,
    GovernanceEvidenceEntry,
    HumanResponse,
    OverrideEvent,
    VindicationRecord,
    VindicationState,
    expire_vindication,
    open_vindication,
    record_human_response,
    resolve_vindication,
)
from tpllm.advisory.calibration_log import (
    RefactorSignal,
    RefactorSignalStatus,
    RuleCalibrationLog,
    build_rereview_evidence,
    detect_refactor_signal,
    update_calibration_log,
)

__all__ = [
    # Surfacing
    "AdvisoryEvaluation",
    "AdvisoryOverlay",
    "AdvisoryThreshold",
    "EpistemicDelta",
    "SurfacingDecision",
    "compute_epistemic_delta",
    "evaluate_advisory_surfacing",
    "is_advisory_eligible",
    # Vindication
    "EvidenceDirection",
    "GovernanceEvidenceEntry",
    "HumanResponse",
    "OverrideEvent",
    "VindicationRecord",
    "VindicationState",
    "expire_vindication",
    "open_vindication",
    "record_human_response",
    "resolve_vindication",
    # Calibration log
    "RefactorSignal",
    "RefactorSignalStatus",
    "RuleCalibrationLog",
    "build_rereview_evidence",
    "detect_refactor_signal",
    "update_calibration_log",
]
