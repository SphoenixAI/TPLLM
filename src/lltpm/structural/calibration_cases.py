"""Data-driven calibration test cases — structured records, not generated code.

Generates ``CalibrationTestCase`` records for proposals that passed regression
validation. A stable test harness executes them by loading the structured
records and running ``run_calibration_test_case`` — no arbitrary Python test
files are generated.
"""

from __future__ import annotations

from uuid import uuid4

from lltpm.structural.proposals import (
    CalibrationTestCase,
    CalibrationTestCaseStatus,
    CalibrationTestCaseType,
    ProposalStatus,
    RegressionCaseClassification,
    RegressionCaseResult,
    CalibrationProposal,
)
from lltpm.structural.regression import simulate_proposal_on_case


# ── Persistence helper ────────────────────────────────────────────────

def _save(store: object | None, program_id: str, name: str, model: object) -> None:
    if store is not None:
        store.save(program_id, name, model)


# ── Test case generation ──────────────────────────────────────────────

def generate_calibration_test_cases(
    *,
    proposal: CalibrationProposal,
    case_results: list[RegressionCaseResult],
    artifact_store: object | None = None,
    program_id: str = "",
) -> list[CalibrationTestCase]:
    """Generate structured test case records for validated proposals.

    Only generates active cases when the proposal has reached
    ``VALIDATED_FOR_REVIEW``. Each improved case becomes a regression
    guard: "this proposal should continue ranking the historically
    successful human option higher."
    """
    if proposal.status != ProposalStatus.VALIDATED_FOR_REVIEW:
        return []

    cases: list[CalibrationTestCase] = []

    for result in case_results:
        if result.classification != RegressionCaseClassification.IMPROVED:
            continue

        case = CalibrationTestCase(
            id=f"ctc-{uuid4().hex[:8]}",
            calibration_proposal_id=proposal.id,
            source_human_decision_record_id=result.human_decision_record_id,
            source_outcome_log_id=result.decision_outcome_log_id,
            test_type=CalibrationTestCaseType.RANK_HUMAN_OPTION_HIGHER,
            expected_human_option_rank_at_or_above=1,
            expected_no_regression=True,
            status=CalibrationTestCaseStatus.ACTIVE,
            summary=(
                "Proposal should continue ranking the historically successful "
                "human-selected option higher for this case."
            ),
        )

        cases.append(case)
        _save(artifact_store, program_id, f"calibration_test_case_{case.id}", case)

    return cases


# ── Test case execution ───────────────────────────────────────────────

def run_calibration_test_case(
    *,
    case: CalibrationTestCase,
    proposal: CalibrationProposal,
    decision: object,
    outcome: object | None,
    intervention_options: list[object],
) -> bool:
    """Execute one structured calibration test case.

    Returns True if the proposal still meets the case's expectation,
    False otherwise.
    """
    result = simulate_proposal_on_case(
        proposal=proposal,
        decision=decision,
        outcome=outcome,
        intervention_options=intervention_options,
    )

    if result is None:
        return False

    if case.test_type == CalibrationTestCaseType.RANK_HUMAN_OPTION_HIGHER:
        expected_rank = case.expected_human_option_rank_at_or_above or 1
        if result.proposal_human_option_rank is None:
            return False
        return result.proposal_human_option_rank <= expected_rank

    if case.test_type == CalibrationTestCaseType.NO_REGRESSION_ON_ACCEPTED_TOP:
        return not result.regressed

    if case.test_type == CalibrationTestCaseType.PREDICTION_ERROR_IMPROVES:
        if result.live_prediction_error is None or result.proposal_prediction_error is None:
            return False
        return result.proposal_prediction_error < result.live_prediction_error

    if case.test_type == CalibrationTestCaseType.CONSTRAINT_BLOCKS_BAD_OPTION:
        return not result.regressed

    return False
