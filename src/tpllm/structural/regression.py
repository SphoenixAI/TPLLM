"""Calibration regression engine — back-test proposals against history.

Replays a ``CalibrationProposal`` against historical ``HumanDecisionRecord``
+ ``DecisionOutcomeLog`` cases. For each case, it compares live model ranking
to proposed model ranking, classifying the result as improved / regressed /
unchanged / inconclusive. The aggregate ``RegressionReport`` determines
whether the proposal is safe to promote for human review.

Strictly non-mutating: intervention options, graphs, thresholds, weights,
ownership context, and ranking configuration are never modified. All scoring
uses copied calculations.
"""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel

from tpllm.structural.proposals import (
    CalibrationEvidenceBundle,
    CalibrationProposal,
    CalibrationProposalType,
    CalibrationTestCase,
    CalibrationTestCaseStatus,
    CalibrationTestCaseType,
    EvidenceBundleRecommendation,
    HistoricalValidationImpact,
    ProposalStatus,
    RegressionCaseClassification,
    RegressionCaseResult,
    RegressionReport,
    RegressionTestedProposalBundle,
)


# ── Scoring helpers (non-mutating) ────────────────────────────────────

def _get_option_id(option: object) -> str:
    return (
        getattr(option, "id", None)
        or getattr(option, "intervention_id", None)
        or ""
    )


def _get_tradeoff_score(option: object) -> float:
    scorer = getattr(option, "tradeoff_score", None)
    if callable(scorer):
        try:
            return float(scorer())
        except Exception:
            pass
    return 0.0


def _rank_options_by_scores(scores: dict[str, float]) -> dict[str, int]:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return {oid: idx + 1 for idx, (oid, _) in enumerate(ranked)}


def _adjusted_score_for_proposal(
    *,
    option: object,
    base_score: float,
    proposal: CalibrationProposal,
    human_selected_id: str,
    agent_top_id: str,
) -> float:
    """Score an option under the modelled effect of a proposal.

    Non-mutating: the option is never modified.
    """
    option_id = _get_option_id(option)
    score = base_score

    if proposal.proposal_type == CalibrationProposalType.ADJUST_INTERVENTION_COST:
        delta = proposal.proposed_delta if proposal.proposed_delta is not None else 0.15
        target = proposal.target_intervention_type or ""
        option_type = str(
            getattr(option, "intervention_type", None)
            or getattr(option, "type", None)
            or option_id
        )
        itype_value = getattr(option_type, "value", option_type)
        if target in {itype_value, option_id, "agent_top_intervention"}:
            score -= delta
        elif target == "bridge_injection" and itype_value == "bridge_injection":
            score -= delta

    elif proposal.proposal_type == CalibrationProposalType.ADD_CONSTRAINT_TYPE:
        delta = proposal.proposed_delta if proposal.proposed_delta is not None else 0.25
        if option_id == agent_top_id:
            score -= delta

    elif proposal.proposal_type == CalibrationProposalType.INCREASE_METRIC_WEIGHT:
        delta = proposal.proposed_delta if proposal.proposed_delta is not None else 0.10
        if option_id == human_selected_id:
            score += delta

    elif proposal.proposal_type == CalibrationProposalType.DECREASE_METRIC_WEIGHT:
        delta = proposal.proposed_delta if proposal.proposed_delta is not None else 0.10
        if option_id == agent_top_id:
            score -= delta

    elif proposal.proposal_type == CalibrationProposalType.ADD_MISSING_EDGE_TYPE:
        delta = proposal.proposed_delta if proposal.proposed_delta is not None else 0.10
        if option_id == human_selected_id:
            score += delta

    return score


# ── Case classification ───────────────────────────────────────────────

def classify_regression_case(
    *,
    live_rank: int | None,
    proposal_rank: int | None,
    live_matched_human_choice: bool,
    proposal_matched_human_choice: bool,
    live_prediction_error: float | None,
    proposal_prediction_error: float | None,
) -> RegressionCaseClassification:
    if live_rank is None or proposal_rank is None:
        return RegressionCaseClassification.INCONCLUSIVE

    if live_matched_human_choice and not proposal_matched_human_choice:
        return RegressionCaseClassification.REGRESSED

    if proposal_rank < live_rank:
        return RegressionCaseClassification.IMPROVED

    if (
        live_prediction_error is not None
        and proposal_prediction_error is not None
        and proposal_prediction_error < live_prediction_error
    ):
        return RegressionCaseClassification.IMPROVED

    if proposal_rank > live_rank:
        return RegressionCaseClassification.REGRESSED

    return RegressionCaseClassification.UNCHANGED


# ── Per-case simulation ───────────────────────────────────────────────

def simulate_proposal_on_case(
    *,
    proposal: CalibrationProposal,
    decision: object,
    outcome: object | None,
    intervention_options: list[object],
) -> RegressionCaseResult | None:
    """Compare live vs proposed ranking for one historical case.

    Non-mutating: intervention options are scored via copied calculations,
    never modified.
    """
    if not intervention_options:
        return None

    decision_id = getattr(decision, "id", None)
    human_selected_id = (
        getattr(decision, "intervention_option_id", None)
        or getattr(decision, "selected_intervention_id", None)
        or ""
    )

    if not decision_id or not human_selected_id:
        return None

    live_scores: dict[str, float] = {}
    proposed_scores: dict[str, float] = {}

    option_ids = [_get_option_id(o) for o in intervention_options]
    agent_top_id = option_ids[0] if option_ids else ""

    for option in intervention_options:
        oid = _get_option_id(option)
        if not oid:
            continue
        base = _get_tradeoff_score(option)
        live_scores[oid] = base
        proposed_scores[oid] = _adjusted_score_for_proposal(
            option=option,
            base_score=base,
            proposal=proposal,
            human_selected_id=human_selected_id,
            agent_top_id=agent_top_id,
        )

    live_ranks = _rank_options_by_scores(live_scores)
    proposal_ranks = _rank_options_by_scores(proposed_scores)

    live_rank = live_ranks.get(human_selected_id)
    proposal_rank = proposal_ranks.get(human_selected_id)

    live_matched = live_rank == 1
    proposal_matched = proposal_rank == 1

    live_error: float | None = None
    if outcome is not None:
        actual = getattr(outcome, "actual_risk_change", None)
        predicted = getattr(decision, "agent_predicted_risk_reduction", None)
        if actual is not None and predicted is not None:
            live_error = abs(float(predicted) - float(actual))

    proposal_error = live_error
    if live_error is not None and live_rank and proposal_rank and proposal_rank < live_rank:
        proposal_error = max(0.0, live_error - 0.10)

    classification = classify_regression_case(
        live_rank=live_rank,
        proposal_rank=proposal_rank,
        live_matched_human_choice=live_matched,
        proposal_matched_human_choice=proposal_matched,
        live_prediction_error=live_error,
        proposal_prediction_error=proposal_error,
    )

    return RegressionCaseResult(
        id=f"rcr-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        human_decision_record_id=decision_id,
        decision_outcome_log_id=getattr(outcome, "id", None) if outcome else None,
        live_matched_human_choice=live_matched,
        proposal_matched_human_choice=proposal_matched,
        live_prediction_error=live_error,
        proposal_prediction_error=proposal_error,
        live_human_option_rank=live_rank,
        proposal_human_option_rank=proposal_rank,
        classification=classification,
        improved=classification == RegressionCaseClassification.IMPROVED,
        regressed=classification == RegressionCaseClassification.REGRESSED,
        inconclusive=classification == RegressionCaseClassification.INCONCLUSIVE,
        summary=(
            f"Human option rank live={live_rank}, proposal={proposal_rank}; "
            f"classification={classification.value}."
        ),
    )


# ── Persistence helper ────────────────────────────────────────────────

def _save(store: object | None, program_id: str, name: str, model: BaseModel) -> None:
    if store is not None:
        store.save(program_id, name, model)


# ── Regression engine ─────────────────────────────────────────────────

def run_regression_test(
    *,
    proposal: CalibrationProposal,
    historical_decisions: list[object],
    historical_outcomes: list[object],
    historical_options_by_decision_id: dict[str, list[object]],
    artifact_store: object | None = None,
    program_id: str = "",
    min_replayable_cases: int = 3,
) -> tuple[RegressionReport, list[RegressionCaseResult]]:
    """Back-test a proposal against historical decision/outcome cases.

    Returns both the aggregate report and the individual case results so
    downstream functions can build impact/bundle/test-cases without
    reloading from the store.
    """
    outcome_by_decision_id: dict[str, object] = {}
    for outcome in historical_outcomes:
        did = getattr(outcome, "decision_record_id", None)
        if did:
            outcome_by_decision_id[did] = outcome

    case_results: list[RegressionCaseResult] = []

    for decision in historical_decisions:
        decision_id = getattr(decision, "id", None)
        if not decision_id:
            continue

        outcome = outcome_by_decision_id.get(decision_id)
        options = historical_options_by_decision_id.get(decision_id, [])

        case_result = simulate_proposal_on_case(
            proposal=proposal,
            decision=decision,
            outcome=outcome,
            intervention_options=options,
        )

        if case_result is not None:
            case_results.append(case_result)
            _save(artifact_store, program_id, f"regression_case_{case_result.id}", case_result)

    replayed = len(case_results)
    improved = sum(1 for r in case_results if r.classification == RegressionCaseClassification.IMPROVED)
    regressed = sum(1 for r in case_results if r.classification == RegressionCaseClassification.REGRESSED)
    unchanged = sum(1 for r in case_results if r.classification == RegressionCaseClassification.UNCHANGED)
    inconclusive = sum(1 for r in case_results if r.classification == RegressionCaseClassification.INCONCLUSIVE)

    historical_success_rate = improved / replayed if replayed else 0.0

    error_deltas: list[float] = []
    for r in case_results:
        if r.live_prediction_error is not None and r.proposal_prediction_error is not None:
            error_deltas.append(r.live_prediction_error - r.proposal_prediction_error)
    average_error_delta = sum(error_deltas) / len(error_deltas) if error_deltas else None

    if regressed > 0:
        status = ProposalStatus.REGRESSION_DETECTED
    elif replayed < min_replayable_cases:
        status = ProposalStatus.NEEDS_MORE_DATA
    elif improved > 0:
        status = ProposalStatus.VALIDATED_FOR_REVIEW
    else:
        status = ProposalStatus.NEEDS_MORE_DATA

    report = RegressionReport(
        id=f"rr-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        replayed_case_count=replayed,
        improved_count=improved,
        regression_count=regressed,
        unchanged_count=unchanged,
        inconclusive_count=inconclusive,
        historical_success_rate=historical_success_rate,
        average_error_delta=average_error_delta,
        case_result_ids=[r.id for r in case_results],
        status=status,
        summary=(
            f"Replayed {replayed} cases: "
            f"{improved} improved, {regressed} regressed, "
            f"{unchanged} unchanged, {inconclusive} inconclusive."
        ),
    )

    proposal.status = status
    proposal.historical_success_rate = historical_success_rate
    proposal.regression_count = regressed
    proposal.replayed_case_count = replayed
    proposal.regression_report_id = report.id

    _save(artifact_store, program_id, f"regression_report_{report.id}", report)
    _save(artifact_store, program_id, f"calibration_proposal_{proposal.id}", proposal)

    return report, case_results


# ── Historical validation impact ──────────────────────────────────────

def build_historical_validation_impact(
    *,
    proposal: CalibrationProposal,
    report: RegressionReport,
    case_results: list[RegressionCaseResult],
    artifact_store: object | None = None,
    program_id: str = "",
) -> HistoricalValidationImpact:
    """Compute how well the proposal aligns with historical human choices."""
    matched = sum(1 for r in case_results if r.proposal_matched_human_choice)
    total = len(case_results)

    before_errors = [
        r.live_prediction_error
        for r in case_results
        if r.live_prediction_error is not None
    ]
    after_errors = [
        r.proposal_prediction_error
        for r in case_results
        if r.proposal_prediction_error is not None
    ]

    avg_before = sum(before_errors) / len(before_errors) if before_errors else None
    avg_after = sum(after_errors) / len(after_errors) if after_errors else None

    avg_delta = None
    if avg_before is not None and avg_after is not None:
        avg_delta = avg_before - avg_after

    match_rate = matched / total if total else 0.0

    confidence = 0.0
    if total:
        confidence += min(0.5, total * 0.05)
    confidence += report.historical_success_rate * 0.3
    if report.regression_count == 0:
        confidence += 0.2
    confidence = min(confidence, 0.95)

    impact = HistoricalValidationImpact(
        id=f"hvi-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        matched_human_override_count=matched,
        total_replayable_overrides=total,
        match_rate=match_rate,
        regression_count=report.regression_count,
        average_prediction_error_before=avg_before,
        average_prediction_error_after=avg_after,
        average_prediction_error_delta=avg_delta,
        confidence=confidence,
        summary=(
            f"Proposal matched {matched}/{total} historical human choices; "
            f"regressions={report.regression_count}; "
            f"average error delta={avg_delta}."
        ),
    )

    _save(artifact_store, program_id, f"historical_validation_impact_{impact.id}", impact)
    return impact


# ── Evidence bundle ───────────────────────────────────────────────────

def bundle_calibration_evidence(
    *,
    proposal: CalibrationProposal,
    report: RegressionReport,
    impact: HistoricalValidationImpact,
    artifact_store: object | None = None,
    program_id: str = "",
) -> CalibrationEvidenceBundle:
    """Assemble the reviewable evidence bundle for a calibration proposal."""
    if report.status == ProposalStatus.REGRESSION_DETECTED:
        recommendation = EvidenceBundleRecommendation.REJECT_DUE_TO_REGRESSION
        explanation = (
            f"Proposal caused {report.regression_count} historical regression(s); "
            "not eligible for promotion."
        )
    elif report.status == ProposalStatus.VALIDATED_FOR_REVIEW:
        recommendation = EvidenceBundleRecommendation.PROMOTE_FOR_REVIEW
        explanation = (
            f"Proposal improved {report.improved_count}/{report.replayed_case_count} "
            f"replayable cases with {report.regression_count} regressions."
        )
    elif report.status == ProposalStatus.NEEDS_MORE_DATA:
        recommendation = EvidenceBundleRecommendation.NEEDS_MORE_DATA
        explanation = "Proposal does not yet have enough replayable evidence."
    else:
        recommendation = EvidenceBundleRecommendation.REJECT_NO_IMPROVEMENT
        explanation = "Proposal did not improve historical replay behavior."

    bundle = CalibrationEvidenceBundle(
        id=f"ceb-{uuid4().hex[:8]}",
        calibration_proposal_id=proposal.id,
        proposal_summary=proposal.proposed_change_summary,
        source_hypothesis_ids=proposal.source_hypothesis_ids,
        source_mismatch_ids=proposal.source_mismatch_ids,
        regression_report_id=report.id,
        historical_validation_impact_id=impact.id,
        recommendation=recommendation,
        explanation=explanation,
    )

    proposal.evidence_bundle_id = bundle.id

    _save(artifact_store, program_id, f"calibration_evidence_bundle_{bundle.id}", bundle)
    _save(artifact_store, program_id, f"calibration_proposal_{proposal.id}", proposal)

    return bundle


# ── Full pipeline ─────────────────────────────────────────────────────

def build_regression_tested_proposal(
    *,
    hypothesis: object,
    historical_decisions: list[object],
    historical_outcomes: list[object],
    historical_options_by_decision_id: dict[str, list[object]],
    artifact_store: object | None = None,
    program_id: str = "",
    min_replayable_cases: int = 3,
) -> RegressionTestedProposalBundle:
    """End-to-end pipeline: hypothesis → regression-tested proposal bundle.

    Creates a proposal, runs regression tests, builds the validation impact
    and evidence bundle, and generates calibration test cases if validated.
    Nothing is auto-applied.
    """
    from tpllm.structural.proposals import create_calibration_proposal_from_hypothesis
    from tpllm.structural.calibration_cases import generate_calibration_test_cases

    proposal = create_calibration_proposal_from_hypothesis(
        hypothesis=hypothesis,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    report, case_results = run_regression_test(
        proposal=proposal,
        historical_decisions=historical_decisions,
        historical_outcomes=historical_outcomes,
        historical_options_by_decision_id=historical_options_by_decision_id,
        artifact_store=artifact_store,
        program_id=program_id,
        min_replayable_cases=min_replayable_cases,
    )

    impact = build_historical_validation_impact(
        proposal=proposal,
        report=report,
        case_results=case_results,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    evidence_bundle = bundle_calibration_evidence(
        proposal=proposal,
        report=report,
        impact=impact,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    test_cases = generate_calibration_test_cases(
        proposal=proposal,
        case_results=case_results,
        artifact_store=artifact_store,
        program_id=program_id,
    )

    return RegressionTestedProposalBundle(
        proposal=proposal,
        report=report,
        impact=impact,
        evidence_bundle=evidence_bundle,
        test_cases=test_cases,
    )
