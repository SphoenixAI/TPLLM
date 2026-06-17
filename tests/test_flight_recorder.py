"""Tests for the Flight Data Recorder (Commit 3).

Covers human decision capture, override reason parsing, outcome
measurement, discrepancy detection, calibration hypothesis generation,
feedback KPI aggregation, and safety guardrails (no auto-tuning,
no validate_insight mutation).
"""

from __future__ import annotations

from datetime import datetime

from lltpm.structural.feedback import (
    HumanDecisionRecord,
    HumanDecisionType,
    OverrideReason,
    ParsedOverrideReason,
    parse_override_reason,
    post_recommendation_hook,
    record_human_decision,
)
from lltpm.structural.outcomes import (
    DecisionDiscrepancy,
    DecisionOutcomeLog,
    DecisionOutcomeType,
    DiscrepancyType,
    calculate_prediction_error,
    derive_decision_discrepancy,
    record_decision_outcome,
)
from lltpm.structural.calibration import (
    CalibrationHypothesis,
    CalibrationHypothesisStatus,
    CalibrationHypothesisType,
    FeedbackKPISummary,
    generate_calibration_hypothesis,
    summarize_feedback_kpis,
)


# ── 1. Human Decision Recording ───────────────────────────────────

class TestHumanDecisionRecording:
    def test_accept_decision_records_correctly(self):
        rec = record_human_decision(
            "rec-opt-split-payments",
            HumanDecisionType.ACCEPTED,
            signal_id="sig-bottleneck-payments",
            agent_predicted_risk_reduction=0.4,
            agent_confidence=0.7,
        )
        assert rec.recommendation_id == "rec-opt-split-payments"
        assert rec.decision_type == HumanDecisionType.ACCEPTED
        assert rec.signal_id == "sig-bottleneck-payments"
        assert rec.agent_predicted_risk_reduction == 0.4
        assert rec.override_reason is None

    def test_rejection_parses_override_reason(self):
        rec = record_human_decision(
            "rec-1",
            HumanDecisionType.REJECTED,
            override_text="This was already handled by the team last week",
        )
        assert rec.decision_type == HumanDecisionType.REJECTED
        assert rec.override_reason is not None
        assert rec.override_reason.reason == OverrideReason.ALREADY_HANDLED

    def test_override_with_disruptive_reason(self):
        rec = record_human_decision(
            "rec-2",
            HumanDecisionType.OVERRIDDEN,
            override_text="Too disruptive right before launch",
        )
        assert rec.override_reason is not None
        assert rec.override_reason.reason == OverrideReason.TOO_DISRUPTIVE


# ── 2. Override Reason Parsing ─────────────────────────────────────

class TestOverrideReasonParsing:
    def test_keyword_parser_matches_priority(self):
        result = parse_override_reason("Wrong priority — this isn't urgent")
        assert result.reason == OverrideReason.WRONG_PRIORITY
        assert result.confidence == 0.8

    def test_keyword_parser_matches_context(self):
        result = parse_override_reason("Missing context about the reorg")
        assert result.reason == OverrideReason.MISSING_CONTEXT

    def test_keyword_parser_matches_timing(self):
        result = parse_override_reason("Not now, we'll do it later")
        assert result.reason == OverrideReason.TIMING_NOT_RIGHT

    def test_keyword_parser_matches_incorrect_diagnosis(self):
        result = parse_override_reason("The diagnosis was wrong, root cause is elsewhere")
        assert result.reason == OverrideReason.INCORRECT_DIAGNOSIS

    def test_keyword_parser_matches_organizational(self):
        result = parse_override_reason("Political reasons — we can't move that team")
        assert result.reason == OverrideReason.ORGANIZATIONAL_CONSTRAINT

    def test_unknown_text_returns_other(self):
        result = parse_override_reason("Just because I said so")
        assert result.reason == OverrideReason.OTHER
        assert result.confidence == 0.3

    def test_empty_text_returns_default(self):
        result = parse_override_reason("")
        assert result.reason == OverrideReason.OTHER
        assert result.confidence == 0.0


# ── 3. Post-Recommendation Hook ───────────────────────────────────

class TestPostRecommendationHook:
    def test_hook_captures_agent_state(self):
        snapshot = post_recommendation_hook(
            recommendation_id="rec-1",
            intervention_option_id="opt-split-payments",
            signal_id="sig-bottleneck-payments",
            agent_predicted_risk_reduction=0.35,
            agent_confidence=0.7,
            agent_severity="high",
        )
        assert snapshot["recommendation_id"] == "rec-1"
        assert snapshot["agent_predicted_risk_reduction"] == 0.35
        assert snapshot["agent_confidence"] == 0.7
        assert "snapshot_at" in snapshot


# ── 4. Outcome Recording ──────────────────────────────────────────

class TestOutcomeRecording:
    def test_risk_resolved_outcome(self):
        decision = HumanDecisionRecord(
            id="hdr-1",
            recommendation_id="rec-1",
            decision_type=HumanDecisionType.ACCEPTED,
            agent_predicted_risk_reduction=0.4,
        )
        outcome = record_decision_outcome(decision, actual_risk_change=0.35)
        assert outcome.decision_record_id == "hdr-1"
        assert outcome.outcome_type == DecisionOutcomeType.RISK_RESOLVED
        assert outcome.actual_risk_change == 0.35

    def test_risk_materialized_outcome(self):
        decision = HumanDecisionRecord(
            id="hdr-2",
            recommendation_id="rec-2",
            decision_type=HumanDecisionType.REJECTED,
            agent_predicted_risk_reduction=0.5,
        )
        outcome = record_decision_outcome(decision, actual_risk_change=-0.3)
        assert outcome.outcome_type == DecisionOutcomeType.RISK_MATERIALIZED

    def test_better_than_predicted_outcome(self):
        decision = HumanDecisionRecord(
            id="hdr-3",
            recommendation_id="rec-3",
            decision_type=HumanDecisionType.ACCEPTED,
            agent_predicted_risk_reduction=0.3,
        )
        outcome = record_decision_outcome(decision, actual_risk_change=0.5)
        assert outcome.outcome_type == DecisionOutcomeType.BETTER_THAN_PREDICTED

    def test_worse_than_predicted_outcome(self):
        decision = HumanDecisionRecord(
            id="hdr-4",
            recommendation_id="rec-4",
            decision_type=HumanDecisionType.ACCEPTED,
            agent_predicted_risk_reduction=0.6,
        )
        outcome = record_decision_outcome(decision, actual_risk_change=0.1)
        assert outcome.outcome_type == DecisionOutcomeType.WORSE_THAN_PREDICTED


# ── 5. Prediction Error Calculation ───────────────────────────────

class TestPredictionError:
    def test_exact_match_is_zero_error(self):
        assert calculate_prediction_error(0.4, 0.4) == 0.0

    def test_overestimate_error(self):
        error = calculate_prediction_error(0.5, 0.2)
        assert abs(error - 0.3) < 1e-9

    def test_underestimate_error(self):
        error = calculate_prediction_error(0.2, 0.5)
        assert abs(error - 0.3) < 1e-9


# ── 6. Discrepancy Derivation ─────────────────────────────────────

class TestDiscrepancyDerivation:
    def test_correct_prediction_within_tolerance(self):
        decision = HumanDecisionRecord(
            id="hdr-1", recommendation_id="rec-1",
            agent_predicted_risk_reduction=0.4,
            agent_severity="medium",
        )
        outcome = DecisionOutcomeLog(
            id="dol-1", decision_record_id="hdr-1",
            actual_risk_change=0.38,
        )
        disc = derive_decision_discrepancy(decision, outcome)
        assert disc.discrepancy_type == DiscrepancyType.CORRECT_PREDICTION
        assert disc.prediction_error < 0.1

    def test_overestimated_risk(self):
        decision = HumanDecisionRecord(
            id="hdr-2", recommendation_id="rec-2",
            agent_predicted_risk_reduction=0.6,
        )
        outcome = DecisionOutcomeLog(
            id="dol-2", decision_record_id="hdr-2",
            actual_risk_change=0.1,
        )
        disc = derive_decision_discrepancy(decision, outcome)
        assert disc.discrepancy_type == DiscrepancyType.OVERESTIMATED_RISK
        assert disc.prediction_error == 0.5

    def test_underestimated_risk(self):
        decision = HumanDecisionRecord(
            id="hdr-3", recommendation_id="rec-3",
            agent_predicted_risk_reduction=0.1,
        )
        outcome = DecisionOutcomeLog(
            id="dol-3", decision_record_id="hdr-3",
            actual_risk_change=0.6,
        )
        disc = derive_decision_discrepancy(decision, outcome)
        assert disc.discrepancy_type == DiscrepancyType.UNDERESTIMATED_RISK


# ── 7. Calibration Hypothesis Generation ──────────────────────────

class TestCalibrationHypothesis:
    def test_overestimate_pattern_generates_too_sensitive_hypothesis(self):
        discrepancies = [
            DecisionDiscrepancy(
                id=f"disc-{i}", discrepancy_type=DiscrepancyType.OVERESTIMATED_RISK,
                predicted_risk_reduction=0.5, actual_risk_change=0.1,
            )
            for i in range(4)
        ]
        records = [
            HumanDecisionRecord(
                id=f"hdr-{i}", recommendation_id=f"rec-{i}",
                decision_type=HumanDecisionType.OVERRIDDEN,
            )
            for i in range(4)
        ]
        hyp = generate_calibration_hypothesis(
            discrepancies, records,
            signal_type="bottleneck_node",
            metric_name="betweenness_centrality",
        )
        assert hyp is not None
        assert hyp.hypothesis_type == CalibrationHypothesisType.THRESHOLD_TOO_SENSITIVE
        assert hyp.status == CalibrationHypothesisStatus.HYPOTHESIS
        assert hyp.metric_name_affected == "betweenness_centrality"
        assert "overestimates" in hyp.summary

    def test_underestimate_pattern_generates_too_lenient_hypothesis(self):
        discrepancies = [
            DecisionDiscrepancy(
                id=f"disc-{i}", discrepancy_type=DiscrepancyType.UNDERESTIMATED_RISK,
                predicted_risk_reduction=0.1, actual_risk_change=0.5,
            )
            for i in range(5)
        ]
        records = [
            HumanDecisionRecord(
                id=f"hdr-{i}", recommendation_id=f"rec-{i}",
                decision_type=HumanDecisionType.ACCEPTED,
            )
            for i in range(5)
        ]
        hyp = generate_calibration_hypothesis(
            discrepancies, records,
            signal_type="scope_drift",
            metric_name="scope_variance",
        )
        assert hyp is not None
        assert hyp.hypothesis_type == CalibrationHypothesisType.THRESHOLD_TOO_LENIENT
        assert hyp.status == CalibrationHypothesisStatus.HYPOTHESIS

    def test_insufficient_data_returns_none(self):
        discrepancies = [
            DecisionDiscrepancy(id="disc-1", discrepancy_type=DiscrepancyType.CORRECT_PREDICTION),
            DecisionDiscrepancy(id="disc-2", discrepancy_type=DiscrepancyType.OVERESTIMATED_RISK),
        ]
        records = [
            HumanDecisionRecord(id="hdr-1", recommendation_id="rec-1"),
        ]
        hyp = generate_calibration_hypothesis(discrepancies, records)
        assert hyp is None

    def test_hypothesis_never_auto_applies(self):
        discrepancies = [
            DecisionDiscrepancy(
                id=f"disc-{i}", discrepancy_type=DiscrepancyType.OVERESTIMATED_RISK,
            )
            for i in range(5)
        ]
        records = [
            HumanDecisionRecord(
                id=f"hdr-{i}", recommendation_id=f"rec-{i}",
                decision_type=HumanDecisionType.OVERRIDDEN,
            )
            for i in range(5)
        ]
        hyp = generate_calibration_hypothesis(discrepancies, records)
        assert hyp is not None
        assert hyp.status == CalibrationHypothesisStatus.HYPOTHESIS
        assert hyp.status != CalibrationHypothesisStatus.APPLIED_MANUALLY


# ── 8. Feedback KPI Aggregation ───────────────────────────────────

class TestFeedbackKPIs:
    def test_summarize_empty_records(self):
        summary = summarize_feedback_kpis([])
        assert summary.total_decisions == 0
        assert summary.acceptance_rate == 0.0

    def test_summarize_mixed_decisions(self):
        records = [
            HumanDecisionRecord(
                id="hdr-1", recommendation_id="rec-1",
                decision_type=HumanDecisionType.ACCEPTED,
                agent_confidence=0.8,
            ),
            HumanDecisionRecord(
                id="hdr-2", recommendation_id="rec-2",
                decision_type=HumanDecisionType.REJECTED,
                agent_confidence=0.6,
            ),
            HumanDecisionRecord(
                id="hdr-3", recommendation_id="rec-3",
                decision_type=HumanDecisionType.OVERRIDDEN,
                agent_confidence=0.7,
            ),
            HumanDecisionRecord(
                id="hdr-4", recommendation_id="rec-4",
                decision_type=HumanDecisionType.ACCEPTED,
                agent_confidence=0.9,
            ),
        ]
        summary = summarize_feedback_kpis(records)
        assert summary.total_decisions == 4
        assert summary.accepted_count == 2
        assert summary.rejected_count == 1
        assert summary.overridden_count == 1
        assert summary.acceptance_rate == 0.5
        assert summary.override_rate == 0.25
        assert abs(summary.average_agent_confidence - 0.75) < 1e-9

    def test_summarize_with_outcome_logs(self):
        records = [
            HumanDecisionRecord(
                id="hdr-1", recommendation_id="rec-1",
                decision_type=HumanDecisionType.ACCEPTED,
                agent_predicted_risk_reduction=0.4,
                agent_confidence=0.8,
            ),
            HumanDecisionRecord(
                id="hdr-2", recommendation_id="rec-2",
                decision_type=HumanDecisionType.ACCEPTED,
                agent_predicted_risk_reduction=0.6,
                agent_confidence=0.7,
            ),
        ]
        outcome_logs = [
            DecisionOutcomeLog(
                id="dol-1", decision_record_id="hdr-1",
                actual_risk_change=0.3,
            ),
            DecisionOutcomeLog(
                id="dol-2", decision_record_id="hdr-2",
                actual_risk_change=0.4,
            ),
        ]
        summary = summarize_feedback_kpis(records, outcome_logs)
        # Error for hdr-1: |0.4 - 0.3| = 0.1
        # Error for hdr-2: |0.6 - 0.4| = 0.2
        # Average: 0.15
        assert abs(summary.average_prediction_error - 0.15) < 1e-9


# ── 9. Safety Guardrails ──────────────────────────────────────────

class TestSafetyGuardrails:
    def test_validate_insight_is_never_imported_by_feedback(self):
        """Feedback modules must not import or call validate_insight."""
        import lltpm.structural.feedback as fb_mod
        import lltpm.structural.outcomes as out_mod
        import lltpm.structural.calibration as cal_mod

        for mod in [fb_mod, out_mod, cal_mod]:
            source = open(mod.__file__).read()
            assert "validate_insight" not in source, (
                f"{mod.__name__} must not reference validate_insight"
            )

    def test_calibration_hypothesis_defaults_to_hypothesis_status(self):
        hyp = CalibrationHypothesis()
        assert hyp.status == CalibrationHypothesisStatus.HYPOTHESIS

    def test_no_graph_mutation_in_feedback_modules(self):
        """Feedback modules must not import networkx or mutate graphs."""
        import lltpm.structural.feedback as fb_mod
        import lltpm.structural.outcomes as out_mod
        import lltpm.structural.calibration as cal_mod

        for mod in [fb_mod, out_mod, cal_mod]:
            source = open(mod.__file__).read()
            assert "import networkx" not in source, (
                f"{mod.__name__} must not import networkx — feedback is observational"
            )


# ── 10. End-to-End Flight Recorder Lifecycle ──────────────────────

class TestFlightRecorderLifecycle:
    def test_full_lifecycle_record_observe_hypothesize(self):
        """Walk through the complete lifecycle:
        1. Agent recommends → hook captures state
        2. Human overrides → decision recorded
        3. Outcome observed → discrepancy derived
        4. Pattern detected → calibration hypothesis generated
        5. Hypothesis stays as HYPOTHESIS — never auto-applied
        """
        # 1. Agent recommends
        snapshot = post_recommendation_hook(
            recommendation_id="rec-split-payments",
            signal_id="sig-bottleneck-payments",
            agent_predicted_risk_reduction=0.45,
            agent_confidence=0.7,
            agent_severity="high",
        )
        assert snapshot["agent_predicted_risk_reduction"] == 0.45

        # 2. Human overrides — says it's too disruptive
        decision = record_human_decision(
            "rec-split-payments",
            HumanDecisionType.OVERRIDDEN,
            signal_id="sig-bottleneck-payments",
            override_text="Too disruptive before the Q3 launch",
            agent_predicted_risk_reduction=0.45,
            agent_confidence=0.7,
            agent_severity="high",
        )
        assert decision.override_reason is not None
        assert decision.override_reason.reason == OverrideReason.TOO_DISRUPTIVE

        # 3. Outcome: risk didn't change much (human's choice held up)
        # actual=0.05 is within [-0.1, 0.1] base range → RISK_UNCHANGED,
        # but predicted=0.45 > 0 and 0.05 < 0.45*0.5 → WORSE_THAN_PREDICTED
        outcome = record_decision_outcome(decision, actual_risk_change=0.05)
        assert outcome.outcome_type == DecisionOutcomeType.WORSE_THAN_PREDICTED

        # Discrepancy: agent overestimated the benefit
        disc = derive_decision_discrepancy(decision, outcome)
        assert disc.discrepancy_type == DiscrepancyType.OVERESTIMATED_RISK
        assert disc.prediction_error == 0.4  # |0.45 - 0.05|

        # 4. After accumulating several similar cases, generate hypothesis
        similar_discs = [disc] * 4  # simulate 4 similar overestimates
        similar_records = [decision] * 4
        hyp = generate_calibration_hypothesis(
            similar_discs, similar_records,
            signal_type="bottleneck_node",
            metric_name="betweenness_centrality",
        )
        assert hyp is not None
        assert hyp.hypothesis_type == CalibrationHypothesisType.THRESHOLD_TOO_SENSITIVE

        # 5. Hypothesis is observational — never auto-applied
        assert hyp.status == CalibrationHypothesisStatus.HYPOTHESIS

        # Feedback KPIs reflect the pattern
        summary = summarize_feedback_kpis(similar_records, [outcome] * 4)
        assert summary.override_rate == 1.0
        assert summary.acceptance_rate == 0.0
        assert summary.average_prediction_error == 0.4
