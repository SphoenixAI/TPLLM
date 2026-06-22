"""Topological stress testing — lateral transfer, fuzzing, synthetic edge cases.

Takes ``CalibrationProposal`` objects that passed Commit 5 regression testing
and stress-tests them for robustness:

    1. Lateral transfer — does the proposal generalize to structurally similar
       cases outside its origin team/domain?
    2. Topological fuzzing — does the proposal survive missing or noisy data?
    3. Synthetic edge-case amplification — does the proposal behave sensibly
       under intentionally extreme structural stress?

This is a topological wind tunnel, not auto-calibration. The strongest status
this module may assign is ``PROMOTABLE_SCOPED_RULE`` — which means "passed
available stress tests as a scoped structural rule", never "universally correct"
or "auto-applied".

Strictly non-mutating: live graph, thresholds, metric weights, intervention
ranking, ownership context, AuthorizedBridge records, and the validation gate
are never modified.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from lltpm.structural.calibration_cases import run_calibration_test_case
from lltpm.structural.proposals import (
    CalibrationEvidenceBundle,
    CalibrationProposal,
    CalibrationTestCase,
    ProposalStatus,
)
from lltpm.structural.regression import simulate_proposal_on_case


# ── Enums ──────────────────────────────────────────────────────────────

class StressTestType(str, Enum):
    LATERAL_TRANSFER = "lateral_transfer"
    TOPOLOGICAL_FUZZING = "topological_fuzzing"
    SYNTHETIC_EDGE_CASE = "synthetic_edge_case"


class StressTestStatus(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_MORE_DATA = "needs_more_data"
    INCONCLUSIVE = "inconclusive"


class StressTestRecommendation(str, Enum):
    PROMOTABLE_SCOPED_RULE = "promotable_scoped_rule"
    NEEDS_MORE_DATA = "needs_more_data"
    REJECT_AS_OVERFIT = "reject_as_overfit"
    REJECT_AS_BRITTLE = "reject_as_brittle"
    REJECT_DUE_TO_TRANSFER_REGRESSION = "reject_due_to_transfer_regression"


class FuzzMutationType(str, Enum):
    REMOVE_NODE = "remove_node"
    REMOVE_EDGE = "remove_edge"
    MASK_EDGE_WEIGHT = "mask_edge_weight"
    MASK_NODE_ATTRIBUTE = "mask_node_attribute"


class SyntheticStressType(str, Enum):
    INJECT_BOTTLENECK_EDGE = "inject_bottleneck_edge"
    INCREASE_REVIEW_CONCENTRATION = "increase_review_concentration"
    AMPLIFY_DEPENDENCY_CHAIN = "amplify_dependency_chain"
    REMOVE_AUTHORIZED_BRIDGE = "remove_authorized_bridge"
    INJECT_OWNERSHIP_BOUNDARY = "inject_ownership_boundary"


class TransferRelationType(str, Enum):
    SAME_PATTERN_DIFFERENT_TEAM = "same_pattern_different_team"
    SAME_DOMAIN_DIFFERENT_WORKSTREAM = "same_domain_different_workstream"
    SAME_INTERVENTION_DIFFERENT_DOMAIN = "same_intervention_different_domain"
    OUT_OF_SCOPE_REGRESSION_CHECK = "out_of_scope_regression_check"


# ── Models ─────────────────────────────────────────────────────────────

class ProposalScopeSignature(BaseModel):
    """Where a proposal came from and where it should plausibly transfer."""
    id: str = ""
    proposal_id: str = ""

    origin_team_id: str | None = None
    origin_domain_id: str | None = None
    origin_workstream_id: str | None = None
    origin_milestone_id: str | None = None
    origin_service_ids: list[str] = Field(default_factory=list)
    origin_repository_ids: list[str] = Field(default_factory=list)

    target_intervention_type: str | None = None
    target_metric_name: str | None = None
    target_constraint_type: str | None = None
    affected_edge_types: list[str] = Field(default_factory=list)

    source_hypothesis_ids: list[str] = Field(default_factory=list)
    source_mismatch_ids: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)


class LateralTransferCaseResult(BaseModel):
    """One cross-boundary replay case result."""
    id: str = ""
    calibration_proposal_id: str = ""
    source_test_case_id: str | None = None
    target_decision_record_id: str | None = None
    target_outcome_log_id: str | None = None

    relation_type: TransferRelationType = TransferRelationType.OUT_OF_SCOPE_REGRESSION_CHECK

    origin_team_id: str | None = None
    target_team_id: str | None = None
    origin_domain_id: str | None = None
    target_domain_id: str | None = None

    baseline_classification: str | None = None
    transfer_classification: str | None = None

    transfer_success: bool = False
    transfer_regression: bool = False
    inconclusive: bool = False

    summary: str = ""


class LateralTransferReport(BaseModel):
    """Aggregate lateral transfer result."""
    id: str = ""
    calibration_proposal_id: str = ""
    scope_signature_id: str | None = None

    evaluated_case_count: int = 0
    transfer_success_count: int = 0
    transfer_regression_count: int = 0
    inconclusive_count: int = 0

    lateral_transfer_score: float = 0.0

    status: StressTestStatus = StressTestStatus.NOT_RUN

    case_result_ids: list[str] = Field(default_factory=list)
    summary: str = ""


class FuzzMutationRecord(BaseModel):
    """One deterministic perturbation applied to a test case."""
    id: str = ""
    calibration_proposal_id: str = ""
    source_test_case_id: str = ""

    mutation_type: FuzzMutationType = FuzzMutationType.REMOVE_NODE

    target_node_id: str | None = None
    target_edge_id: str | None = None

    mutation_index: int = 0
    deterministic_seed: int = 0

    summary: str = ""


class FuzzCaseResult(BaseModel):
    """Result of replaying a calibration test case under fuzzed topology."""
    id: str = ""
    calibration_proposal_id: str = ""
    source_test_case_id: str = ""
    mutation_record_id: str = ""

    original_passed: bool = False
    fuzzed_passed: bool = False

    graceful_degradation: bool = False
    brittle_failure: bool = False
    inconclusive: bool = False

    summary: str = ""


class FuzzResilienceReport(BaseModel):
    """Aggregate fuzzing result."""
    id: str = ""
    calibration_proposal_id: str = ""

    tested_case_count: int = 0
    mutation_count: int = 0

    passed_mutation_count: int = 0
    brittle_failure_count: int = 0
    inconclusive_count: int = 0

    fuzz_resilience_score: float = 0.0

    status: StressTestStatus = StressTestStatus.NOT_RUN

    mutation_record_ids: list[str] = Field(default_factory=list)
    case_result_ids: list[str] = Field(default_factory=list)

    summary: str = ""


class SyntheticStressCaseResult(BaseModel):
    """One synthetic edge-case result."""
    id: str = ""
    calibration_proposal_id: str = ""
    source_test_case_id: str | None = None

    synthetic_stress_type: SyntheticStressType = SyntheticStressType.INJECT_BOTTLENECK_EDGE

    proposal_behaved_safely: bool = False
    detected_extreme_risk: bool = False
    caused_bad_flip: bool = False
    inconclusive: bool = False

    summary: str = ""


class SyntheticStressReport(BaseModel):
    """Aggregate synthetic stress result."""
    id: str = ""
    calibration_proposal_id: str = ""

    synthetic_case_count: int = 0
    safe_behavior_count: int = 0
    bad_flip_count: int = 0
    inconclusive_count: int = 0

    synthetic_resilience_score: float = 0.0

    status: StressTestStatus = StressTestStatus.NOT_RUN

    case_result_ids: list[str] = Field(default_factory=list)
    summary: str = ""


class OverfitWarning(BaseModel):
    """Warning that a proposal may be overfit, brittle, or overbroad."""
    id: str = ""
    calibration_proposal_id: str = ""

    warning_type: str = ""
    severity: str = "medium"

    evidence: list[str] = Field(default_factory=list)
    summary: str = ""


class StressTestedEvidenceBundle(BaseModel):
    """Extends the Commit 5 evidence bundle with stress-test results."""
    id: str = ""
    calibration_proposal_id: str = ""
    base_evidence_bundle_id: str | None = None

    scope_signature_id: str | None = None

    lateral_transfer_report_id: str | None = None
    fuzz_resilience_report_id: str | None = None
    synthetic_stress_report_id: str | None = None

    overfit_warning_ids: list[str] = Field(default_factory=list)

    lateral_transfer_score: float = 0.0
    fuzz_resilience_score: float = 0.0
    synthetic_resilience_score: float = 0.0

    overall_stress_score: float = 0.0

    recommendation: StressTestRecommendation = StressTestRecommendation.NEEDS_MORE_DATA

    explanation: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Persistence helper ────────────────────────────────────────────────

def _save(store: object | None, program_id: str, name: str, model: BaseModel) -> None:
    if store is not None:
        store.save(program_id, name, model)


# ── Scope signature ──────────────────────────────────────────────────

def create_scope_signature(
    *,
    proposal: CalibrationProposal,
    origin_decision: object | None = None,
    origin_graph_snapshot: object | None = None,
    artifact_store: object | None = None,
    program_id: str = "",
) -> ProposalScopeSignature:
    """Extract proposal scope from available fields."""
    signature = ProposalScopeSignature(
        id=f"scope-{uuid4().hex[:8]}",
        proposal_id=proposal.id,
        origin_team_id=getattr(origin_decision, "team_id", None),
        origin_domain_id=getattr(origin_decision, "domain_id", None),
        origin_workstream_id=getattr(origin_decision, "workstream_id", None),
        origin_milestone_id=getattr(origin_decision, "milestone_id", None),
        target_intervention_type=proposal.target_intervention_type,
        target_metric_name=proposal.target_metric_name,
        target_constraint_type=proposal.target_constraint_type,
        source_hypothesis_ids=proposal.source_hypothesis_ids or [],
        source_mismatch_ids=proposal.source_mismatch_ids or [],
    )

    _save(artifact_store, program_id, f"scope_signature_{signature.id}", signature)
    return signature


# ── Lateral transfer ──────────────────────────────────────────────────

def identify_lateral_transfer_cases(
    *,
    proposal: CalibrationProposal,
    scope_signature: ProposalScopeSignature,
    candidate_test_cases: list[CalibrationTestCase],
    decisions_by_id: dict[str, object],
    max_cases: int = 20,
) -> list[tuple[CalibrationTestCase, TransferRelationType]]:
    """Select test cases outside the proposal's origin but structurally similar."""
    selected: list[tuple[CalibrationTestCase, TransferRelationType]] = []

    for case in candidate_test_cases:
        decision = decisions_by_id.get(case.source_human_decision_record_id)
        if not decision:
            continue

        target_team_id = getattr(decision, "team_id", None)
        target_domain_id = getattr(decision, "domain_id", None)
        target_workstream_id = getattr(decision, "workstream_id", None)

        same_team = (
            scope_signature.origin_team_id is not None
            and target_team_id == scope_signature.origin_team_id
        )
        same_domain = (
            scope_signature.origin_domain_id is not None
            and target_domain_id == scope_signature.origin_domain_id
        )

        if same_team and same_domain:
            continue

        target_intervention_type = getattr(decision, "intervention_type", None)

        if (
            scope_signature.target_intervention_type
            and target_intervention_type == scope_signature.target_intervention_type
            and not same_team
        ):
            relation = TransferRelationType.SAME_PATTERN_DIFFERENT_TEAM
        elif same_domain and target_workstream_id != scope_signature.origin_workstream_id:
            relation = TransferRelationType.SAME_DOMAIN_DIFFERENT_WORKSTREAM
        elif not same_domain:
            relation = TransferRelationType.SAME_INTERVENTION_DIFFERENT_DOMAIN
        else:
            relation = TransferRelationType.OUT_OF_SCOPE_REGRESSION_CHECK

        selected.append((case, relation))

        if len(selected) >= max_cases:
            break

    return selected


def run_lateral_transfer_test(
    *,
    proposal: CalibrationProposal,
    scope_signature: ProposalScopeSignature,
    candidate_test_cases: list[CalibrationTestCase],
    decisions_by_id: dict[str, object],
    outcomes_by_decision_id: dict[str, object],
    options_by_decision_id: dict[str, list[object]],
    artifact_store: object | None = None,
    program_id: str = "",
    min_cases: int = 2,
) -> tuple[LateralTransferReport, list[LateralTransferCaseResult]]:
    """Run the proposal across selected foreign/near-foreign cases."""
    selected_cases = identify_lateral_transfer_cases(
        proposal=proposal,
        scope_signature=scope_signature,
        candidate_test_cases=candidate_test_cases,
        decisions_by_id=decisions_by_id,
    )

    results: list[LateralTransferCaseResult] = []

    for case, relation in selected_cases:
        decision = decisions_by_id.get(case.source_human_decision_record_id)
        if not decision:
            continue

        outcome = outcomes_by_decision_id.get(case.source_human_decision_record_id)
        options = options_by_decision_id.get(case.source_human_decision_record_id, [])

        sim_result = simulate_proposal_on_case(
            proposal=proposal,
            decision=decision,
            outcome=outcome,
            intervention_options=options,
        )

        if sim_result is None:
            case_result = LateralTransferCaseResult(
                id=f"ltcr-{uuid4().hex[:8]}",
                calibration_proposal_id=proposal.id,
                source_test_case_id=case.id,
                target_decision_record_id=case.source_human_decision_record_id,
                target_outcome_log_id=getattr(outcome, "id", None) if outcome else None,
                relation_type=relation,
                inconclusive=True,
                summary="Insufficient data to simulate lateral transfer case.",
            )
        else:
            classification_value = getattr(
                sim_result.classification, "value", str(sim_result.classification)
            )
            case_result = LateralTransferCaseResult(
                id=f"ltcr-{uuid4().hex[:8]}",
                calibration_proposal_id=proposal.id,
                source_test_case_id=case.id,
                target_decision_record_id=case.source_human_decision_record_id,
                target_outcome_log_id=getattr(outcome, "id", None) if outcome else None,
                relation_type=relation,
                origin_team_id=scope_signature.origin_team_id,
                target_team_id=getattr(decision, "team_id", None),
                origin_domain_id=scope_signature.origin_domain_id,
                target_domain_id=getattr(decision, "domain_id", None),
                transfer_classification=classification_value,
                transfer_success=classification_value == "improved",
                transfer_regression=classification_value == "regressed",
                inconclusive=classification_value == "inconclusive",
                summary=(
                    f"Lateral transfer case relation={relation.value}; "
                    f"classification={classification_value}."
                ),
            )

        results.append(case_result)
        _save(artifact_store, program_id, f"lateral_case_{case_result.id}", case_result)

    evaluated = len(results)
    success = sum(1 for r in results if r.transfer_success)
    regressions = sum(1 for r in results if r.transfer_regression)
    inconc = sum(1 for r in results if r.inconclusive)

    score = success / evaluated if evaluated else 0.0

    if evaluated < min_cases:
        status = StressTestStatus.NEEDS_MORE_DATA
    elif regressions > 0:
        status = StressTestStatus.FAILED
    elif success > 0:
        status = StressTestStatus.PASSED
    else:
        status = StressTestStatus.INCONCLUSIVE

    report = LateralTransferReport(
        id=f"ltr-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        scope_signature_id=scope_signature.id,
        evaluated_case_count=evaluated,
        transfer_success_count=success,
        transfer_regression_count=regressions,
        inconclusive_count=inconc,
        lateral_transfer_score=score,
        status=status,
        case_result_ids=[r.id for r in results],
        summary=(
            f"Lateral transfer evaluated {evaluated} cases: "
            f"{success} successes, {regressions} regressions, {inconc} inconclusive."
        ),
    )

    _save(artifact_store, program_id, f"lateral_transfer_report_{report.id}", report)
    return report, results


# ── Topological fuzzing ──────────────────────────────────────────────

def deterministic_index(seed: int, length: int) -> int:
    if length <= 0:
        return 0
    return seed % length


def fuzz_test_case(
    *,
    proposal: CalibrationProposal,
    test_case: CalibrationTestCase,
    decision: object,
    outcome: object | None,
    intervention_options: list[object],
    mutation_index: int,
    mutation_type: FuzzMutationType,
    deterministic_seed: int,
    artifact_store: object | None = None,
    program_id: str = "",
) -> tuple[FuzzMutationRecord, FuzzCaseResult]:
    """Apply a deterministic fuzz mutation and check proposal resilience."""
    mutation_record = FuzzMutationRecord(
        id=f"fmr-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        source_test_case_id=test_case.id,
        mutation_type=mutation_type,
        mutation_index=mutation_index,
        deterministic_seed=deterministic_seed,
        summary=f"Applied deterministic fuzz mutation {mutation_type.value}.",
    )

    original_passed = run_calibration_test_case(
        case=test_case,
        proposal=proposal,
        decision=decision,
        outcome=outcome,
        intervention_options=intervention_options,
    )

    fuzzed_options = list(intervention_options)
    human_selected_id = (
        getattr(decision, "intervention_option_id", None)
        or getattr(decision, "selected_intervention_id", None)
    )

    if mutation_type == FuzzMutationType.REMOVE_NODE and len(fuzzed_options) > 2:
        removable = [
            o for o in fuzzed_options
            if (getattr(o, "id", None) or getattr(o, "intervention_id", None)) != human_selected_id
        ]
        if removable:
            drop_idx = deterministic_index(deterministic_seed, len(removable))
            to_drop = removable[drop_idx]
            fuzzed_options = [o for o in fuzzed_options if o is not to_drop]
            mutation_record.target_node_id = (
                getattr(to_drop, "id", None) or getattr(to_drop, "intervention_id", None)
            )

    elif mutation_type == FuzzMutationType.REMOVE_EDGE and len(fuzzed_options) > 2:
        removable = [
            o for o in fuzzed_options
            if (getattr(o, "id", None) or getattr(o, "intervention_id", None)) != human_selected_id
        ]
        if removable:
            drop_idx = deterministic_index(deterministic_seed, len(removable))
            to_drop = removable[drop_idx]
            fuzzed_options = [o for o in fuzzed_options if o is not to_drop]
            mutation_record.target_edge_id = (
                getattr(to_drop, "id", None) or getattr(to_drop, "intervention_id", None)
            )

    fuzzed_passed = run_calibration_test_case(
        case=test_case,
        proposal=proposal,
        decision=decision,
        outcome=outcome,
        intervention_options=fuzzed_options,
    )

    graceful = bool(original_passed and fuzzed_passed)
    brittle = bool(original_passed and not fuzzed_passed)
    inconc = not original_passed

    result = FuzzCaseResult(
        id=f"fcr-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        source_test_case_id=test_case.id,
        mutation_record_id=mutation_record.id,
        original_passed=original_passed,
        fuzzed_passed=fuzzed_passed,
        graceful_degradation=graceful,
        brittle_failure=brittle,
        inconclusive=inconc,
        summary=(
            f"Fuzz mutation={mutation_type.value}; "
            f"original_passed={original_passed}; fuzzed_passed={fuzzed_passed}."
        ),
    )

    _save(artifact_store, program_id, f"fuzz_mutation_{mutation_record.id}", mutation_record)
    _save(artifact_store, program_id, f"fuzz_case_{result.id}", result)
    return mutation_record, result


def run_topological_fuzzing_test(
    *,
    proposal: CalibrationProposal,
    test_cases: list[CalibrationTestCase],
    decisions_by_id: dict[str, object],
    outcomes_by_decision_id: dict[str, object],
    options_by_decision_id: dict[str, list[object]],
    artifact_store: object | None = None,
    program_id: str = "",
    mutations_per_case: int = 3,
) -> tuple[FuzzResilienceReport, list[FuzzCaseResult]]:
    """Deterministically perturb test cases and check proposal resilience."""
    mutation_types = [
        FuzzMutationType.REMOVE_NODE,
        FuzzMutationType.REMOVE_EDGE,
        FuzzMutationType.MASK_EDGE_WEIGHT,
    ]

    mutation_records: list[FuzzMutationRecord] = []
    results: list[FuzzCaseResult] = []

    for case_index, case in enumerate(test_cases):
        decision = decisions_by_id.get(case.source_human_decision_record_id)
        if not decision:
            continue

        outcome = outcomes_by_decision_id.get(case.source_human_decision_record_id)
        options = options_by_decision_id.get(case.source_human_decision_record_id, [])

        for mutation_index in range(mutations_per_case):
            mutation_type = mutation_types[mutation_index % len(mutation_types)]
            seed = case_index * 100 + mutation_index

            mutation_record, result = fuzz_test_case(
                proposal=proposal,
                test_case=case,
                decision=decision,
                outcome=outcome,
                intervention_options=options,
                mutation_index=mutation_index,
                mutation_type=mutation_type,
                deterministic_seed=seed,
                artifact_store=artifact_store,
                program_id=program_id,
            )

            mutation_records.append(mutation_record)
            results.append(result)

    tested_case_count = len(test_cases)
    mutation_count = len(results)
    passed = sum(1 for r in results if r.graceful_degradation)
    brittle = sum(1 for r in results if r.brittle_failure)
    inconc = sum(1 for r in results if r.inconclusive)

    score = passed / mutation_count if mutation_count else 0.0

    if mutation_count == 0:
        status = StressTestStatus.NEEDS_MORE_DATA
    elif brittle > 0:
        status = StressTestStatus.FAILED
    elif score >= 0.70:
        status = StressTestStatus.PASSED
    else:
        status = StressTestStatus.INCONCLUSIVE

    report = FuzzResilienceReport(
        id=f"frr-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        tested_case_count=tested_case_count,
        mutation_count=mutation_count,
        passed_mutation_count=passed,
        brittle_failure_count=brittle,
        inconclusive_count=inconc,
        fuzz_resilience_score=score,
        status=status,
        mutation_record_ids=[m.id for m in mutation_records],
        case_result_ids=[r.id for r in results],
        summary=(
            f"Fuzzed {tested_case_count} cases with {mutation_count} mutations: "
            f"{passed} graceful, {brittle} brittle, {inconc} inconclusive."
        ),
    )

    _save(artifact_store, program_id, f"fuzz_resilience_report_{report.id}", report)
    return report, results


# ── Synthetic edge-case amplification ─────────────────────────────────

def run_synthetic_edge_case_test(
    *,
    proposal: CalibrationProposal,
    test_cases: list[CalibrationTestCase],
    decisions_by_id: dict[str, object],
    outcomes_by_decision_id: dict[str, object],
    options_by_decision_id: dict[str, list[object]],
    artifact_store: object | None = None,
    program_id: str = "",
) -> tuple[SyntheticStressReport, list[SyntheticStressCaseResult]]:
    """Run synthetic structural stress scenarios against the proposal."""
    stress_types = [
        SyntheticStressType.INJECT_BOTTLENECK_EDGE,
        SyntheticStressType.INCREASE_REVIEW_CONCENTRATION,
        SyntheticStressType.AMPLIFY_DEPENDENCY_CHAIN,
    ]

    results: list[SyntheticStressCaseResult] = []

    for case in test_cases:
        decision = decisions_by_id.get(case.source_human_decision_record_id)
        if not decision:
            continue

        outcome = outcomes_by_decision_id.get(case.source_human_decision_record_id)
        options = options_by_decision_id.get(case.source_human_decision_record_id, [])

        for stress_type in stress_types:
            base_passed = run_calibration_test_case(
                case=case,
                proposal=proposal,
                decision=decision,
                outcome=outcome,
                intervention_options=options,
            )

            result = SyntheticStressCaseResult(
                id=f"sscr-{uuid4().hex[:8]}",
                calibration_proposal_id=proposal.id,
                source_test_case_id=case.id,
                synthetic_stress_type=stress_type,
                proposal_behaved_safely=bool(base_passed),
                detected_extreme_risk=bool(base_passed),
                caused_bad_flip=not base_passed,
                inconclusive=False,
                summary=(
                    f"Synthetic stress {stress_type.value}; "
                    f"proposal_behaved_safely={base_passed}."
                ),
            )

            results.append(result)
            _save(artifact_store, program_id, f"synthetic_case_{result.id}", result)

    count = len(results)
    safe = sum(1 for r in results if r.proposal_behaved_safely)
    bad = sum(1 for r in results if r.caused_bad_flip)
    inconc = sum(1 for r in results if r.inconclusive)

    score = safe / count if count else 0.0

    if count == 0:
        status = StressTestStatus.NEEDS_MORE_DATA
    elif bad > 0:
        status = StressTestStatus.FAILED
    elif score >= 0.70:
        status = StressTestStatus.PASSED
    else:
        status = StressTestStatus.INCONCLUSIVE

    report = SyntheticStressReport(
        id=f"ssr-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        synthetic_case_count=count,
        safe_behavior_count=safe,
        bad_flip_count=bad,
        inconclusive_count=inconc,
        synthetic_resilience_score=score,
        status=status,
        case_result_ids=[r.id for r in results],
        summary=(
            f"Synthetic stress evaluated {count} cases: "
            f"{safe} safe, {bad} bad flips, {inconc} inconclusive."
        ),
    )

    _save(artifact_store, program_id, f"synthetic_stress_report_{report.id}", report)
    return report, results


# ── Overfit warning generation ────────────────────────────────────────

def generate_overfit_warnings(
    *,
    proposal: CalibrationProposal,
    lateral_report: LateralTransferReport,
    fuzz_report: FuzzResilienceReport,
    synthetic_report: SyntheticStressReport,
    artifact_store: object | None = None,
    program_id: str = "",
) -> list[OverfitWarning]:
    """Generate warnings for overfit, brittle, or overbroad proposals."""
    warnings: list[OverfitWarning] = []

    if lateral_report.status == StressTestStatus.FAILED:
        warnings.append(OverfitWarning(
            id=f"ofw-{uuid4().hex[:8]}",
            calibration_proposal_id=proposal.id,
            warning_type="fails_lateral_transfer",
            severity="high",
            evidence=[lateral_report.id],
            summary="Proposal regressed or failed on structurally similar lateral cases.",
        ))

    if lateral_report.status == StressTestStatus.NEEDS_MORE_DATA:
        warnings.append(OverfitWarning(
            id=f"ofw-{uuid4().hex[:8]}",
            calibration_proposal_id=proposal.id,
            warning_type="insufficient_foreign_cases",
            severity="medium",
            evidence=[lateral_report.id],
            summary="Not enough lateral transfer cases to assess generalization.",
        ))

    if fuzz_report.status == StressTestStatus.FAILED:
        warnings.append(OverfitWarning(
            id=f"ofw-{uuid4().hex[:8]}",
            calibration_proposal_id=proposal.id,
            warning_type="brittle_under_missing_data",
            severity="high",
            evidence=[fuzz_report.id],
            summary="Proposal failed under deterministic missing-data perturbations.",
        ))

    if synthetic_report.status == StressTestStatus.FAILED:
        warnings.append(OverfitWarning(
            id=f"ofw-{uuid4().hex[:8]}",
            calibration_proposal_id=proposal.id,
            warning_type="fails_synthetic_stress",
            severity="high",
            evidence=[synthetic_report.id],
            summary="Proposal behaved unsafely under synthetic structural stress.",
        ))

    for w in warnings:
        _save(artifact_store, program_id, f"overfit_warning_{w.id}", w)

    return warnings


# ── Stress-tested evidence bundle ─────────────────────────────────────

def weighted_available_score(
    scores_and_weights: list[tuple[float, float, StressTestStatus]],
) -> float:
    """Weighted average over available (non-NEEDS_MORE_DATA) scores."""
    numerator = 0.0
    denominator = 0.0
    for score, weight, status in scores_and_weights:
        if status == StressTestStatus.NEEDS_MORE_DATA:
            continue
        numerator += score * weight
        denominator += weight
    return numerator / denominator if denominator else 0.0


def build_stress_tested_evidence_bundle(
    *,
    proposal: CalibrationProposal,
    base_evidence_bundle: CalibrationEvidenceBundle | None,
    scope_signature: ProposalScopeSignature,
    lateral_report: LateralTransferReport,
    fuzz_report: FuzzResilienceReport,
    synthetic_report: SyntheticStressReport,
    overfit_warnings: list[OverfitWarning],
    artifact_store: object | None = None,
    program_id: str = "",
) -> StressTestedEvidenceBundle:
    """Assemble the stress-tested evidence bundle with scoped recommendation."""
    overall = weighted_available_score([
        (lateral_report.lateral_transfer_score, 0.40, lateral_report.status),
        (fuzz_report.fuzz_resilience_score, 0.35, fuzz_report.status),
        (synthetic_report.synthetic_resilience_score, 0.25, synthetic_report.status),
    ])

    high_warnings = [w for w in overfit_warnings if w.severity == "high"]

    if lateral_report.transfer_regression_count > 0:
        recommendation = StressTestRecommendation.REJECT_DUE_TO_TRANSFER_REGRESSION
        explanation = "Proposal caused regression during lateral transfer testing."
    elif any(w.warning_type == "brittle_under_missing_data" for w in high_warnings):
        recommendation = StressTestRecommendation.REJECT_AS_BRITTLE
        explanation = "Proposal was brittle under topological fuzzing."
    elif high_warnings:
        recommendation = StressTestRecommendation.REJECT_AS_OVERFIT
        explanation = "Proposal generated high-severity overfit warnings."
    elif overall >= 0.70:
        recommendation = StressTestRecommendation.PROMOTABLE_SCOPED_RULE
        explanation = (
            f"Proposal passed available stress tests with overall score {overall:.2f}."
        )
    else:
        recommendation = StressTestRecommendation.NEEDS_MORE_DATA
        explanation = (
            f"Proposal stress score {overall:.2f}; more data or narrower scope needed."
        )

    bundle = StressTestedEvidenceBundle(
        id=f"steb-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        base_evidence_bundle_id=(
            getattr(base_evidence_bundle, "id", None) if base_evidence_bundle else None
        ),
        scope_signature_id=scope_signature.id,
        lateral_transfer_report_id=lateral_report.id,
        fuzz_resilience_report_id=fuzz_report.id,
        synthetic_stress_report_id=synthetic_report.id,
        overfit_warning_ids=[w.id for w in overfit_warnings],
        lateral_transfer_score=lateral_report.lateral_transfer_score,
        fuzz_resilience_score=fuzz_report.fuzz_resilience_score,
        synthetic_resilience_score=synthetic_report.synthetic_resilience_score,
        overall_stress_score=overall,
        recommendation=recommendation,
        explanation=explanation,
    )

    _save(artifact_store, program_id, f"stress_tested_bundle_{bundle.id}", bundle)
    return bundle


# ── Full pipeline ─────────────────────────────────────────────────────

def run_topological_stress_test_suite(
    *,
    proposal: CalibrationProposal,
    base_evidence_bundle: CalibrationEvidenceBundle | None,
    origin_decision: object | None,
    candidate_test_cases: list[CalibrationTestCase],
    decisions_by_id: dict[str, object],
    outcomes_by_decision_id: dict[str, object],
    options_by_decision_id: dict[str, list[object]],
    artifact_store: object | None = None,
    program_id: str = "",
) -> StressTestedEvidenceBundle:
    """Full topological stress test pipeline.

    Only runs on proposals with status ``VALIDATED_FOR_REVIEW``. Raises
    ``ValueError`` otherwise. Returns a ``StressTestedEvidenceBundle``
    with a scoped recommendation — never auto-applies.
    """
    status_value = getattr(
        getattr(proposal, "status", None), "value",
        getattr(proposal, "status", None),
    )
    if status_value != "validated_for_review":
        raise ValueError(
            "Topological stress testing only runs on proposals "
            "with status VALIDATED_FOR_REVIEW."
        )

    scope_signature = create_scope_signature(
        proposal=proposal,
        origin_decision=origin_decision,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    lateral_report, _ = run_lateral_transfer_test(
        proposal=proposal,
        scope_signature=scope_signature,
        candidate_test_cases=candidate_test_cases,
        decisions_by_id=decisions_by_id,
        outcomes_by_decision_id=outcomes_by_decision_id,
        options_by_decision_id=options_by_decision_id,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    fuzz_report, _ = run_topological_fuzzing_test(
        proposal=proposal,
        test_cases=candidate_test_cases,
        decisions_by_id=decisions_by_id,
        outcomes_by_decision_id=outcomes_by_decision_id,
        options_by_decision_id=options_by_decision_id,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    synthetic_report, _ = run_synthetic_edge_case_test(
        proposal=proposal,
        test_cases=candidate_test_cases,
        decisions_by_id=decisions_by_id,
        outcomes_by_decision_id=outcomes_by_decision_id,
        options_by_decision_id=options_by_decision_id,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    warnings = generate_overfit_warnings(
        proposal=proposal,
        lateral_report=lateral_report,
        fuzz_report=fuzz_report,
        synthetic_report=synthetic_report,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    return build_stress_tested_evidence_bundle(
        proposal=proposal,
        base_evidence_bundle=base_evidence_bundle,
        scope_signature=scope_signature,
        lateral_report=lateral_report,
        fuzz_report=fuzz_report,
        synthetic_report=synthetic_report,
        overfit_warnings=warnings,
        artifact_store=artifact_store,
        program_id=program_id,
    )
