"""AI quality measurement.

The metrics evaluate the AI's own behavior — evidence coverage,
unsupported claims, question discipline, structural technical depth, and
forecast calibration — never the people the AI works with.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tpllm.intelligence import (
    AIQualityRiskType,
    ArchitectureComponent,
    ArchitectureRelation,
    ArchitectureRelationType,
    ClarificationNecessity,
    ClarificationQuestion,
    EvidenceReference,
    EvidenceSourceType,
    GapResolutionStatus,
    LaunchFeatureVector,
    LaunchForecast,
    LaunchForecastMethod,
    LaunchOutcomeRecord,
    LaunchReadinessScorecard,
    LaunchReadinessSnapshot,
    LaunchRiskBand,
    ProjectArchitectureMap,
    ProjectEvidence,
    ProjectPurposeModel,
    ProjectSignificanceModel,
    ProjectUnderstandingSnapshot,
    QuestionHistoryRecord,
    ResearchResolution,
    TechnicalMechanismModel,
    TechnicalMechanismStep,
    UnderstandingClaim,
    UnderstandingClaimStatus,
    UnderstandingClaimType,
    UnderstandingCoverage,
    assess_claim_quality,
    assess_technical_depth,
    build_ai_quality_report,
    can_emit_project_claim,
    compute_ai_quality_metrics,
)

_AS_OF = datetime(2026, 7, 1, 12, 0, 0)
_PROJECT = "atlas"


# ── Fixtures ─────────────────────────────────────────────────────────

def _pe(eid: str, content: str = "evidence content",
        reliability: float = 0.8) -> ProjectEvidence:
    reference = EvidenceReference(
        id=f"ref-{eid}", project_id=_PROJECT,
        source_type=EvidenceSourceType.DOCUMENT, source_id=eid,
        observed_at=_AS_OF - timedelta(days=1), ingested_at=_AS_OF,
        reliability=reliability, relevance=0.8,
    )
    return ProjectEvidence(
        id=eid, project_id=_PROJECT, content=content, source=reference,
        normalized_at=_AS_OF,
    )


def _claim(**overrides) -> UnderstandingClaim:
    defaults = dict(
        id="c-1", project_id=_PROJECT,
        claim_type=UnderstandingClaimType.PROJECT_PURPOSE,
        statement="Replace entitlement lookup with the account graph",
        status=UnderstandingClaimStatus.SUPPORTED,
        confidence=0.6, evidence_ids=["ev-1"],
    )
    defaults.update(overrides)
    return UnderstandingClaim(**defaults)


def _understanding(
    *,
    deep: bool = False,
    claims: list[UnderstandingClaim] | None = None,
    contradictions=(),
) -> ProjectUnderstandingSnapshot:
    if deep:
        mechanism = TechnicalMechanismModel(
            project_id=_PROJECT,
            mechanism_summary="billing → graph → clients",
            inputs=["account-events"],
            outputs=["permissions"],
            steps=[
                TechnicalMechanismStep(
                    order=i + 1, name=f"step-{i + 1}",
                    description=f"step {i + 1}",
                )
                for i in range(3)
            ],
            data_contracts=["billing→graph account event contract"],
            observability_signals=["permission resolution latency"],
            known_failure_modes=["event stream lag", "stale cache"],
        )
        architecture = ProjectArchitectureMap(
            project_id=_PROJECT,
            components=[
                ArchitectureComponent(
                    id="comp-billing", project_id=_PROJECT, name="Billing",
                    component_type="service", owner_ids=["team-billing"],
                ),
                ArchitectureComponent(
                    id="comp-graph", project_id=_PROJECT, name="Graph",
                    component_type="service", owner_ids=["team-graph"],
                ),
            ],
            relations=[
                ArchitectureRelation(
                    id="rel-1", project_id=_PROJECT,
                    source_component_id="comp-billing",
                    target_component_id="comp-graph",
                    relation_type=ArchitectureRelationType.EMITS_TO,
                ),
                ArchitectureRelation(
                    id="rel-2", project_id=_PROJECT,
                    source_component_id="comp-graph",
                    target_component_id="comp-billing",
                    relation_type=ArchitectureRelationType.READS_FROM,
                ),
            ],
        )
    else:
        mechanism = TechnicalMechanismModel(project_id=_PROJECT)
        architecture = ProjectArchitectureMap(project_id=_PROJECT)

    return ProjectUnderstandingSnapshot(
        id="pus-q", project_id=_PROJECT, as_of=_AS_OF,
        purpose=ProjectPurposeModel(
            project_id=_PROJECT,
            one_sentence_purpose="Replace entitlement lookup",
        ),
        significance=ProjectSignificanceModel(project_id=_PROJECT),
        technical_mechanism=mechanism,
        architecture=architecture,
        claims=list(claims or []),
        contradictions=list(contradictions),
        coverage=UnderstandingCoverage(overall_coverage=0.5),
        generated_at=_AS_OF,
    )


def _question(qid: str, necessity: ClarificationNecessity,
              signature: str = "sig-default") -> ClarificationQuestion:
    return ClarificationQuestion(
        id=qid, project_id=_PROJECT, gap_id=f"gap-{qid}",
        question="Is mobile launch-blocking?",
        what_the_ai_already_found="Conflicting scope evidence.",
        remaining_ambiguity="Launch doc vs deferral tickets.",
        why_the_answer_matters="Changes launch scope.",
        requested_answer_type="fact",
        necessity=necessity,
        question_signature=signature,
        created_at=_AS_OF,
    )


def _resolution(rid: str, status: GapResolutionStatus) -> ResearchResolution:
    return ResearchResolution(
        id=rid, gap_id=f"gap-{rid}", status=status,
        confidence=0.8 if status == GapResolutionStatus.RESOLVED else 0.3,
        evidence_ids=["e-1"] if status == GapResolutionStatus.RESOLVED else [],
        explanation="test resolution",
    )


def _metrics(**overrides):
    defaults = dict(
        understanding=_understanding(),
        research_attempts=[],
        resolutions=[],
        clarification_questions=[],
        question_history=[],
        claim_risk_flags=[],
        readiness_history=[],
        launch_outcomes=[],
    )
    defaults.update(overrides)
    return compute_ai_quality_metrics(**defaults)


# ── Test 1–5: Claim quality gate ─────────────────────────────────────

class TestClaimQuality:
    def test_supported_claim_without_resolvable_evidence_flagged(self):
        claim = _claim(evidence_ids=["ghost-evidence"])
        flags = assess_claim_quality(
            claim=claim, evidence_by_id={"real": _pe("real")}, as_of=_AS_OF,
        )
        assert any(
            f.risk_type == AIQualityRiskType.UNSUPPORTED_CLAIM for f in flags
        )
        assert can_emit_project_claim(
            claim=claim, evidence_by_id={"real": _pe("real")}, as_of=_AS_OF,
        ) is False

    def test_inferred_presented_as_supported_flagged(self):
        claim = _claim(
            evidence_ids=["ev-1"], inferred=True,
            inference_explanation="guessed from phrasing",
        )
        flags = assess_claim_quality(
            claim=claim, evidence_by_id={"ev-1": _pe("ev-1")}, as_of=_AS_OF,
        )
        assert any(
            f.risk_type == AIQualityRiskType.OVERCONFIDENT_INFERENCE
            for f in flags
        )

    def test_confidence_one_flagged(self):
        claim = _claim(confidence=1.0)
        flags = assess_claim_quality(
            claim=claim, evidence_by_id={"ev-1": _pe("ev-1")}, as_of=_AS_OF,
        )
        assert any(
            f.risk_type == AIQualityRiskType.OVERCONFIDENT_INFERENCE
            and "1.0" in f.explanation
            for f in flags
        )

    def test_stale_claim_without_stale_label_flagged(self):
        claim = _claim(last_supported_at=_AS_OF - timedelta(days=200))
        flags = assess_claim_quality(
            claim=claim, evidence_by_id={"ev-1": _pe("ev-1")}, as_of=_AS_OF,
        )
        assert any(
            f.risk_type == AIQualityRiskType.STALE_UNDERSTANDING for f in flags
        )

    def test_ignored_contradiction_flagged(self):
        claim = _claim(contradicting_evidence_ids=["ev-conflict"])
        flags = assess_claim_quality(
            claim=claim, evidence_by_id={"ev-1": _pe("ev-1")}, as_of=_AS_OF,
        )
        assert any(
            f.risk_type == AIQualityRiskType.CONTRADICTION_IGNORED
            for f in flags
        )

    def test_clean_claim_passes(self):
        claim = _claim()
        assert can_emit_project_claim(
            claim=claim, evidence_by_id={"ev-1": _pe("ev-1")}, as_of=_AS_OF,
        ) is True


# ── Test 6–7: Technical depth ────────────────────────────────────────

class TestTechnicalDepth:
    def test_purpose_only_is_shallow(self):
        depth = assess_technical_depth(understanding=_understanding())
        assert depth.overall_depth < 0.3
        assert "failure modes" in " ".join(depth.missing_elements)

    def test_full_structure_is_deep(self):
        depth = assess_technical_depth(understanding=_understanding(deep=True))
        assert depth.overall_depth > 0.8
        assert depth.mechanism_depth > 0.8
        assert depth.failure_mode_depth == 1.0
        assert depth.observability_depth == 1.0
        assert depth.missing_elements == []


# ── Test 8–11: Metric calculations ───────────────────────────────────

class TestMetricCalculations:
    def test_self_resolution_rate(self):
        resolutions = [
            _resolution("r1", GapResolutionStatus.RESOLVED),
            _resolution("r2", GapResolutionStatus.RESOLVED),
            _resolution("r3", GapResolutionStatus.UNRESOLVED),
            _resolution("r4", GapResolutionStatus.HUMAN_DECISION_REQUIRED),
        ]
        metrics = _metrics(resolutions=resolutions)
        assert metrics.self_resolution_rate == pytest.approx(2 / 3)

    def test_unnecessary_question_rate(self):
        questions = [
            _question("q1", ClarificationNecessity.REQUIRED, "sig-a"),
            _question("q2", ClarificationNecessity.NOT_NEEDED, "sig-b"),
        ]
        metrics = _metrics(clarification_questions=questions)
        assert metrics.unnecessary_question_rate == pytest.approx(0.5)

    def test_repeated_question_rate(self):
        questions = [
            _question("q1", ClarificationNecessity.REQUIRED, "sig-repeat"),
            _question("q2", ClarificationNecessity.REQUIRED, "sig-new"),
        ]
        history = [
            QuestionHistoryRecord(
                id="h1", project_id=_PROJECT,
                question_signature="sig-repeat", question_id="old",
                asked_at=_AS_OF - timedelta(days=5),
                answered_at=_AS_OF - timedelta(days=4),
            )
        ]
        metrics = _metrics(
            clarification_questions=questions, question_history=history,
        )
        assert metrics.repeated_question_rate == pytest.approx(0.5)

    def test_clarification_precision(self):
        questions = [
            _question("q1", ClarificationNecessity.REQUIRED, "sig-a"),
            _question("q2", ClarificationNecessity.STRATEGIC_DECISION_REQUIRED,
                      "sig-b"),
            _question("q3", ClarificationNecessity.OPTIONAL, "sig-c"),
            _question("q4", ClarificationNecessity.NOT_NEEDED, "sig-d"),
        ]
        metrics = _metrics(clarification_questions=questions)
        assert metrics.clarification_precision == pytest.approx(0.5)


# ── Test 12: Brier score only with outcomes ──────────────────────────

class TestForecastCalibration:
    def _readiness(self, launch_id: str, probability: float):
        scorecard = LaunchReadinessScorecard(
            id=f"sc-{launch_id}", project_id=_PROJECT, launch_id=launch_id,
            as_of=_AS_OF, overall_score=70.0, overall_confidence=0.6,
            generated_at=_AS_OF,
        )
        feature_vector = LaunchFeatureVector(
            project_id=_PROJECT, launch_id=launch_id, as_of=_AS_OF,
            dimension_scores={}, dimension_confidences={},
            critical_blocker_count=0, high_blocker_count=0,
            unresolved_critical_unknown_count=0, contradiction_count=0,
        )
        forecast = LaunchForecast(
            id=f"fc-{launch_id}", project_id=_PROJECT, launch_id=launch_id,
            as_of=_AS_OF, method=LaunchForecastMethod.HEURISTIC_V1,
            on_time_probability=probability, expected_slip_days=5.0,
            lower_slip_days=2.0, upper_slip_days=10.0,
            risk_band=LaunchRiskBand.MODERATE, confidence=0.6,
            feature_vector=feature_vector,
        )
        return LaunchReadinessSnapshot(
            id=f"lrs-{launch_id}", scorecard=scorecard, forecast=forecast,
        )

    def test_no_outcomes_means_no_calibration_error(self):
        metrics = _metrics(
            readiness_history=[self._readiness("l1", 0.7)],
            launch_outcomes=[],
        )
        assert metrics.forecast_calibration_error is None

    def test_brier_score_with_outcomes(self):
        outcome = LaunchOutcomeRecord(
            id="o1", project_id=_PROJECT, launch_id="l1",
            target_date=_AS_OF + timedelta(days=30),
            launched_on_time=False, slip_days=12.0,
            outcome_observed_at=_AS_OF + timedelta(days=45),
        )
        metrics = _metrics(
            readiness_history=[self._readiness("l1", 0.7)],
            launch_outcomes=[outcome],
        )
        # Predicted 0.7, actual 0 → Brier = 0.49.
        assert metrics.forecast_calibration_error == pytest.approx(0.49)


# ── Test 13–14: Report quality ───────────────────────────────────────

class TestReport:
    def _report(self, understanding=None):
        return build_ai_quality_report(
            project_id=_PROJECT,
            understanding=understanding or _understanding(),
            readiness_history=[],
            launch_outcomes=[],
            research_attempts=[],
            resolutions=[],
            clarification_questions=[],
            question_history=[],
            as_of=_AS_OF,
        )

    def test_recommendations_are_specific(self):
        report = self._report()
        assert report.highest_priority_improvements
        joined = " ".join(report.highest_priority_improvements)
        # Names concrete missing elements, not vague "improve depth".
        assert "failure modes" in joined
        assert _PROJECT in joined
        assert "Improve technical depth" not in joined

    def test_shallow_model_flagged(self):
        report = self._report()
        assert any(
            f.risk_type == AIQualityRiskType.SHALLOW_TECHNICAL_MODEL
            for f in report.risk_flags
        )

    def test_report_evaluates_ai_not_workers(self):
        import inspect
        import tpllm.intelligence.ai_quality as mod

        source = inspect.getsource(mod).lower()
        for pattern in (
            "productivity",
            "employee_score",
            "employee_rank",
            "performance_rank",
            "surveillance",
        ):
            assert pattern not in source
        report = self._report()
        assert "AI" in report.explanation

    def test_deep_model_clean_report(self):
        report = self._report(understanding=_understanding(deep=True))
        assert report.technical_depth.overall_depth > 0.8
        assert not any(
            f.risk_type == AIQualityRiskType.SHALLOW_TECHNICAL_MODEL
            for f in report.risk_flags
        )
