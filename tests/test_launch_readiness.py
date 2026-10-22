"""Predictive Launch Readiness Scorecard.

Readiness is computed from evidence, not task counts; readiness and
confidence stay separate; blockers affect score and forecast; the
predictor is honestly HEURISTIC_V1; training schemas reject leakage.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tpllm.intelligence import (
    ClaimContradiction,
    HeuristicLaunchReadinessPredictor,
    LaunchBlockerSeverity,
    LaunchDefinition,
    LaunchFeatureVector,
    LaunchForecastMethod,
    LaunchOutcomeRecord,
    LaunchReadinessDimension,
    LaunchReadinessDimensionType,
    LaunchTrainingExample,
    ProjectArchitectureMap,
    ProjectPurposeModel,
    ProjectSignificanceModel,
    ProjectUnderstandingSnapshot,
    ProjectUnknown,
    ProjectUnknownKind,
    ReadinessScoringConfig,
    ReadinessSignalDirection,
    ResearchPriority,
    TechnicalMechanismModel,
    TechnicalMechanismStep,
    UnderstandingClaim,
    UnderstandingClaimStatus,
    UnderstandingClaimType,
    UnderstandingCoverage,
    CompanyGoalLink,
    build_launch_readiness_snapshot,
    compare_launch_readiness_snapshots,
    extract_readiness_evidence,
    identify_launch_blockers,
    score_readiness_dimension,
    validate_launch_training_example,
)

_AS_OF = datetime(2026, 7, 1, 12, 0, 0)
_PROJECT = "atlas"
_D = LaunchReadinessDimensionType


# ── Fixtures ─────────────────────────────────────────────────────────

def _understanding(
    *,
    purpose: bool = True,
    goal_link: bool = True,
    steps: int = 3,
    failure_modes: bool = True,
    owners: bool = True,
    unknowns: list[ProjectUnknown] | None = None,
    contradictions: list[ClaimContradiction] | None = None,
    claims: list[UnderstandingClaim] | None = None,
    freshness: float = 0.9,
) -> ProjectUnderstandingSnapshot:
    purpose_model = ProjectPurposeModel(
        project_id=_PROJECT,
        one_sentence_purpose=(
            "Replace fragmented entitlement lookup with the account graph"
            if purpose else None
        ),
        success_criteria=["All clients resolve via the graph"] if purpose else [],
        evidence_ids=["pev-roadmap"] if purpose else [],
        confidence=0.7 if purpose else 0.0,
    )
    significance = ProjectSignificanceModel(
        project_id=_PROJECT,
        company_goal_links=(
            [
                CompanyGoalLink(
                    id="gl-1",
                    project_id=_PROJECT,
                    company_goal_name="platform-consolidation",
                    connection_mechanism=(
                        "Reduces entitlement latency so the graph becomes the "
                        "shared authorization layer."
                    ),
                    expected_impact="shared authz layer",
                    impact_type="strategic",
                    evidence_ids=["pev-strategy"],
                    confidence=0.7,
                )
            ]
            if goal_link else []
        ),
        evidence_ids=["pev-strategy"] if goal_link else [],
        confidence=0.7 if goal_link else 0.0,
    )
    mechanism = TechnicalMechanismModel(
        project_id=_PROJECT,
        steps=[
            TechnicalMechanismStep(
                order=i + 1,
                name=f"step-{i + 1}",
                description=f"step {i + 1}",
                evidence_ids=["pev-arch"],
                confidence=0.6,
            )
            for i in range(steps)
        ],
        known_failure_modes=(
            ["Event stream lag causes stale permissions"] if failure_modes else []
        ),
        evidence_ids=["pev-arch"] if steps else [],
        confidence=0.6 if steps else 0.0,
    )
    architecture = ProjectArchitectureMap(project_id=_PROJECT)
    coverage = UnderstandingCoverage(
        purpose_coverage=0.75 if purpose else 0.0,
        technical_mechanism_coverage=0.8 if steps else 0.0,
        overall_coverage=0.6,
        evidence_freshness=freshness,
    )
    return ProjectUnderstandingSnapshot(
        id="pus-test",
        project_id=_PROJECT,
        as_of=_AS_OF,
        purpose=purpose_model,
        significance=significance,
        technical_mechanism=mechanism,
        architecture=architecture,
        claims=list(claims or []),
        contradictions=list(contradictions or []),
        unknowns=list(unknowns or []),
        coverage=coverage,
        evidence_ids=["pev-roadmap", "pev-arch"],
        generated_at=_AS_OF,
    )


def _launch(**overrides) -> LaunchDefinition:
    defaults = dict(
        id="launch-atlas-v1",
        project_id=_PROJECT,
        launch_name="Atlas v1",
        target_date=_AS_OF + timedelta(days=30),
        forecast_horizon_start=_AS_OF - timedelta(days=60),
        required_scope=["graph service live", "billing integration"],
        success_criteria=["All clients resolve permissions via graph"],
        operational_requirements=["runbook", "rollback plan"],
        evidence_ids=["pev-launch-doc"],
    )
    defaults.update(overrides)
    return LaunchDefinition(**defaults)


def _artifact(
    dimension: str,
    direction: str,
    strength: float = 0.7,
    days_ago: int = 1,
    rationale: str = "artifact signal",
    **extra,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"art-{dimension}-{rationale[:12]}",
        readiness_dimension=dimension,
        direction=direction,
        strength=strength,
        observed_at=_AS_OF - timedelta(days=days_ago),
        rationale=rationale,
        reliability=extra.pop("reliability", 0.9),
        confidence=extra.pop("confidence", 0.8),
        **extra,
    )


def _snapshot(
    understanding=None,
    launch=None,
    artifacts=None,
    goals=None,
    previous=None,
    predictor=None,
):
    return build_launch_readiness_snapshot(
        understanding=understanding or _understanding(),
        launch=launch or _launch(),
        ontology_records=[],
        structural_artifacts=artifacts or [],
        project_goals=goals or [],
        previous_snapshots=previous or [],
        predictor=predictor or HeuristicLaunchReadinessPredictor(),
        config=ReadinessScoringConfig(),
        as_of=_AS_OF,
    )


def _dim(snapshot, dimension):
    return next(
        d for d in snapshot.scorecard.dimensions if d.dimension == dimension
    )


def _critical_unknown(kind=ProjectUnknownKind.CONFLICTING_EVIDENCE, question=None):
    return ProjectUnknown(
        id=f"unk-{kind.value}",
        project_id=_PROJECT,
        kind=kind,
        question=question or "Is the billing contract resolved?",
        why_it_matters="It gates launch scope.",
        priority=ResearchPriority.CRITICAL,
        created_at=_AS_OF,
    )


# ── Test 1: Launch definition validation ─────────────────────────────

class TestLaunchDefinition:
    def test_missing_target_date_fails(self):
        with pytest.raises(ValidationError):
            LaunchDefinition(
                id="l1", project_id=_PROJECT, launch_name="x",
                forecast_horizon_start=_AS_OF,
            )


# ── Test 2: Evidence cutoff ──────────────────────────────────────────

class TestEvidenceCutoff:
    def test_future_evidence_excluded(self):
        future = _artifact(
            "integration_readiness", "negative", days_ago=-5,
            rationale="future integration failure",
        )
        contributions = extract_readiness_evidence(
            understanding=_understanding(),
            launch=_launch(),
            ontology_records=[],
            structural_artifacts=[future],
            as_of=_AS_OF,
        )
        assert not any(
            "future integration failure" in c.rationale for c in contributions
        )


# ── Test 3–4: Scope evidence ─────────────────────────────────────────

class TestScopeEvidence:
    def test_defined_scope_raises_score(self):
        with_scope = _snapshot(launch=_launch())
        without_scope = _snapshot(
            launch=_launch(required_scope=[], success_criteria=[])
        )
        assert (
            _dim(with_scope, _D.SCOPE_CLARITY).score
            > _dim(without_scope, _D.SCOPE_CLARITY).score
        )

    def test_contested_scope_lowers_score_and_confidence(self):
        contested_claim = UnderstandingClaim(
            id="c-scope",
            project_id=_PROJECT,
            claim_type=UnderstandingClaimType.SCOPE,
            statement="Mobile is in v1 scope",
            status=UnderstandingClaimStatus.CONTESTED,
            confidence=0.4,
            evidence_ids=["pev-chat"],
            contradicting_evidence_ids=["pev-launch-doc"],
        )
        contradiction = ClaimContradiction(
            id="contra-1",
            project_id=_PROJECT,
            claim_a_id="c-scope",
            claim_b_id="c-scope-b",
            contradiction_type="included_vs_excluded",
            severity="high",
            evidence_ids=["pev-chat", "pev-launch-doc"],
            explanation="Mobile scope contested.",
        )
        clean = _snapshot()
        contested = _snapshot(
            understanding=_understanding(
                claims=[contested_claim], contradictions=[contradiction]
            )
        )
        clean_dim = _dim(clean, _D.SCOPE_CLARITY)
        contested_dim = _dim(contested, _D.SCOPE_CLARITY)
        assert contested_dim.score < clean_dim.score
        assert contested_dim.confidence < clean_dim.confidence
        assert contested_dim.contradiction_count == 1


# ── Test 5–6: Blockers from evidence ─────────────────────────────────

class TestBlockers:
    def test_missing_success_criteria_creates_blocker(self):
        snapshot = _snapshot(launch=_launch(success_criteria=[]))
        titles = [b.title.lower() for b in snapshot.scorecard.blockers]
        assert any("success criteria" in t for t in titles)

    def test_dependency_blocker_lowers_dependency_score(self):
        unknown = _critical_unknown(
            kind=ProjectUnknownKind.MISSING_DEPENDENCY,
            question="Who provides the account event stream?",
        )
        clean = _snapshot()
        blocked = _snapshot(understanding=_understanding(unknowns=[unknown]))
        assert (
            _dim(blocked, _D.DEPENDENCY_HEALTH).score
            < _dim(clean, _D.DEPENDENCY_HEALTH).score
        )
        assert any(
            b.severity == LaunchBlockerSeverity.CRITICAL
            for b in blocked.scorecard.blockers
        )

    def test_blockers_deduplicated_by_subject(self):
        launch = _launch(success_criteria=[])
        snapshot = _snapshot(launch=launch)
        titles = [b.title.lower() for b in snapshot.scorecard.blockers]
        assert len(titles) == len(set(titles))


# ── Test 7: Ticket completion cannot dominate ────────────────────────

class TestTicketCompletionCap:
    def test_ticket_strength_capped(self):
        ticket = _artifact(
            "validation_readiness", "positive", strength=1.0,
            rationale="90 percent of tickets closed",
            evidence_kind="ticket_completion",
        )
        contributions = extract_readiness_evidence(
            understanding=_understanding(),
            launch=_launch(),
            ontology_records=[],
            structural_artifacts=[ticket],
            as_of=_AS_OF,
        )
        ticket_contribution = next(
            c for c in contributions if "tickets closed" in c.rationale
        )
        assert ticket_contribution.strength <= 0.3

    def test_ticket_alone_cannot_produce_high_readiness(self):
        ticket = _artifact(
            "validation_readiness", "positive", strength=1.0,
            rationale="all tickets closed", evidence_kind="ticket_completion",
        )
        contributions = extract_readiness_evidence(
            understanding=_understanding(),
            launch=_launch(),
            ontology_records=[],
            structural_artifacts=[ticket],
            as_of=_AS_OF,
        )
        only_ticket = [c for c in contributions if "tickets closed" in c.rationale]
        dim = score_readiness_dimension(
            dimension=_D.VALIDATION_READINESS,
            contributions=only_ticket,
            contradictions=[],
            blockers=[],
            config=ReadinessScoringConfig(),
        )
        assert dim.score <= 65.0


# ── Test 8: Technical unknown lowers technical confidence ────────────

class TestTechnicalUnknown:
    def test_unknown_mechanism_lowers_dimension(self):
        unknown = _critical_unknown(
            kind=ProjectUnknownKind.MISSING_TECHNICAL_MECHANISM,
            question="How does prepaid entitlement resolve during migration?",
        )
        clean = _snapshot()
        unknown_snap = _snapshot(
            understanding=_understanding(unknowns=[unknown])
        )
        assert (
            _dim(unknown_snap, _D.TECHNICAL_CONFIDENCE).score
            < _dim(clean, _D.TECHNICAL_CONFIDENCE).score
        )


# ── Test 9: Integration failure outweighs meeting optimism ───────────

class TestOptimismVsFailure:
    def test_failure_outweighs_optimism(self):
        artifacts = [
            _artifact(
                "integration_readiness", "negative", strength=0.9,
                rationale="Billing contract integration test failed",
            ),
            _artifact(
                "integration_readiness", "positive", strength=0.9,
                rationale="Team feels good about integration",
                evidence_kind="meeting_optimism",
            ),
            _artifact(
                "integration_readiness", "positive", strength=0.9,
                rationale="Standup says integration is nearly done",
                evidence_kind="meeting_optimism",
            ),
        ]
        snapshot = _snapshot(artifacts=artifacts)
        assert _dim(snapshot, _D.INTEGRATION_READINESS).score < 50.0


# ── Test 10: Operational readiness requires evidence ─────────────────

class TestOperationalEvidence:
    def test_no_evidence_means_zero_confidence(self):
        snapshot = _snapshot()
        operational = _dim(snapshot, _D.OPERATIONAL_READINESS)
        assert operational.evidence_count == 0
        assert operational.confidence == 0.0


# ── Test 11–13: Bounds and separation ────────────────────────────────

class TestBoundsAndSeparation:
    def test_dimension_score_bounded(self):
        unknowns = [
            _critical_unknown(question=f"critical question {i}")
            for i in range(8)
        ]
        snapshot = _snapshot(understanding=_understanding(unknowns=unknowns))
        for dim in snapshot.scorecard.dimensions:
            assert 0.0 <= dim.score <= 100.0

    def test_dimension_model_rejects_out_of_bounds(self):
        with pytest.raises(ValueError):
            LaunchReadinessDimension(
                dimension=_D.SCOPE_CLARITY, score=120.0, confidence=0.5,
                evidence_count=1,
            )
        with pytest.raises(ValueError):
            LaunchReadinessDimension(
                dimension=_D.SCOPE_CLARITY, score=50.0, confidence=1.5,
                evidence_count=1,
            )

    def test_confidence_bounded(self):
        snapshot = _snapshot()
        assert 0.0 <= snapshot.scorecard.overall_confidence <= 1.0
        for dim in snapshot.scorecard.dimensions:
            assert 0.0 <= dim.confidence <= 1.0

    def test_score_and_confidence_are_separate(self):
        # Strong but stale evidence: readiness can stay decent while
        # confidence falls. The two are never multiplied together.
        stale = _snapshot(understanding=_understanding(freshness=0.05))
        fresh = _snapshot(understanding=_understanding(freshness=0.95))
        assert (
            stale.scorecard.overall_confidence
            < fresh.scorecard.overall_confidence
        )
        # Score difference is far smaller than the confidence collapse.
        assert abs(
            stale.scorecard.overall_score - fresh.scorecard.overall_score
        ) < 30.0


# ── Test 14: Critical blockers reduce overall readiness ──────────────

class TestCriticalBlockerImpact:
    def test_critical_blocker_reduces_overall(self):
        clean = _snapshot()
        blocked = _snapshot(
            understanding=_understanding(unknowns=[_critical_unknown()])
        )
        assert (
            blocked.scorecard.overall_score < clean.scorecard.overall_score
        )
        assert blocked.forecast.on_time_probability < (
            clean.forecast.on_time_probability
        )


# ── Test 15–18: Heuristic forecast ───────────────────────────────────

class TestHeuristicForecast:
    def test_forecast_deterministic(self):
        a = _snapshot()
        b = _snapshot()
        assert a.forecast.model_dump() == b.forecast.model_dump()
        assert a.forecast.method == LaunchForecastMethod.HEURISTIC_V1

    def test_confidence_capped_without_outcomes(self):
        snapshot = _snapshot()
        assert snapshot.forecast.confidence <= 0.75

    def test_forecast_worsens_with_more_critical_blockers(self):
        base = _snapshot()
        predictor = HeuristicLaunchReadinessPredictor()
        fv = base.forecast.feature_vector
        worse_fv = fv.model_copy(
            update={"critical_blocker_count": fv.critical_blocker_count + 2}
        )
        worse = predictor.predict(
            scorecard=base.scorecard, feature_vector=worse_fv
        )
        assert worse.on_time_probability < base.forecast.on_time_probability

    def test_forecast_improves_with_readiness_and_trend(self):
        base = _snapshot()
        predictor = HeuristicLaunchReadinessPredictor()
        better_scorecard = base.scorecard.model_copy(
            update={"overall_score": base.scorecard.overall_score + 15}
        )
        better_fv = base.forecast.feature_vector.model_copy(
            update={"readiness_trend_slope": 8.0}
        )
        better = predictor.predict(
            scorecard=better_scorecard, feature_vector=better_fv
        )
        assert better.on_time_probability > base.forecast.on_time_probability


# ── Test 19: Goal estimates ──────────────────────────────────────────

class TestGoalEstimates:
    def test_goals_get_dimension_specific_estimates(self):
        artifacts = [
            _artifact(
                "integration_readiness", "negative", strength=0.9,
                rationale="Billing contract integration test failed",
                blocker_candidate=True,
            ),
            _artifact(
                "operational_readiness", "positive", strength=0.8,
                rationale="Runbook and on-call rotation are in place",
            ),
        ]
        goals = [
            SimpleNamespace(id="g-contract", name="Validate billing contract integration"),
            SimpleNamespace(id="g-handoff", name="Operational handoff complete"),
        ]
        snapshot = _snapshot(artifacts=artifacts, goals=goals)
        assert len(snapshot.goal_estimates) == 2
        by_id = {g.goal_id: g for g in snapshot.goal_estimates}
        # Different dimensions → different probabilities.
        assert (
            by_id["g-contract"].completion_probability
            != by_id["g-handoff"].completion_probability
        )
        # The integration goal carries the integration blocker.
        assert by_id["g-contract"].blocker_ids
        assert by_id["g-contract"].evidence_ids
        assert "integration" in by_id["g-contract"].explanation


# ── Test 20–21: Evidence and explanation ─────────────────────────────

class TestScorecardTraceability:
    def test_evidence_references_preserved(self):
        snapshot = _snapshot()
        assert snapshot.scorecard.evidence_ids
        scope = _dim(snapshot, _D.SCOPE_CLARITY)
        assert scope.positive_evidence_ids or scope.negative_evidence_ids

    def test_top_reasons_explained(self):
        snapshot = _snapshot(
            understanding=_understanding(unknowns=[_critical_unknown()])
        )
        assert snapshot.scorecard.top_reasons_for_score
        assert all(
            "scored" in reason for reason in snapshot.scorecard.top_reasons_for_score
        )


# ── Test 22–23: Trend ────────────────────────────────────────────────

class TestTrend:
    def _degraded_snapshot(self):
        contested_claim = UnderstandingClaim(
            id="c-scope",
            project_id=_PROJECT,
            claim_type=UnderstandingClaimType.SCOPE,
            statement="Mobile is in v1 scope",
            status=UnderstandingClaimStatus.CONTESTED,
            confidence=0.4,
            evidence_ids=["pev-chat"],
            contradicting_evidence_ids=["pev-doc"],
        )
        contradiction = ClaimContradiction(
            id="contra-1", project_id=_PROJECT,
            claim_a_id="c-scope", claim_b_id="c-b",
            contradiction_type="included_vs_excluded", severity="high",
            evidence_ids=["pev-chat"],
            explanation="Mobile scope became contested.",
        )
        return _snapshot(
            understanding=_understanding(
                claims=[contested_claim],
                contradictions=[contradiction],
                unknowns=[_critical_unknown()],
            ),
        )

    def test_trend_identifies_deteriorating_dimensions(self):
        previous = _snapshot()
        current = self._degraded_snapshot()
        trend = compare_launch_readiness_snapshots(
            previous=previous, current=current
        )
        assert "scope_clarity" in trend.deteriorating_dimensions
        assert trend.score_delta < 0
        assert "because" in trend.summary

    def test_trend_identifies_new_blockers(self):
        previous = _snapshot()
        current = self._degraded_snapshot()
        trend = compare_launch_readiness_snapshots(
            previous=previous, current=current
        )
        assert trend.newly_opened_blocker_ids


# ── Test 24: Training example leakage ────────────────────────────────

class TestTrainingLeakage:
    def _feature_vector(self, as_of):
        return LaunchFeatureVector(
            project_id=_PROJECT, launch_id="l1", as_of=as_of,
            dimension_scores={}, dimension_confidences={},
            critical_blocker_count=0, high_blocker_count=0,
            unresolved_critical_unknown_count=0, contradiction_count=0,
        )

    def _outcome(self, observed_at):
        return LaunchOutcomeRecord(
            id="o1", project_id=_PROJECT, launch_id="l1",
            target_date=_AS_OF + timedelta(days=30),
            launched_on_time=False, slip_days=10.0,
            outcome_observed_at=observed_at,
        )

    def test_valid_example_passes(self):
        example = LaunchTrainingExample(
            feature_vector=self._feature_vector(_AS_OF),
            outcome=self._outcome(_AS_OF + timedelta(days=40)),
            evidence_cutoff_at=_AS_OF,
        )
        validate_launch_training_example(example)  # no raise

    def test_outcome_before_prediction_rejected(self):
        example = LaunchTrainingExample(
            feature_vector=self._feature_vector(_AS_OF),
            outcome=self._outcome(_AS_OF - timedelta(days=1)),
            evidence_cutoff_at=_AS_OF - timedelta(days=2),
        )
        with pytest.raises(ValueError, match="after the prediction"):
            validate_launch_training_example(example)

    def test_post_cutoff_features_rejected(self):
        example = LaunchTrainingExample(
            feature_vector=self._feature_vector(_AS_OF + timedelta(days=5)),
            outcome=self._outcome(_AS_OF + timedelta(days=40)),
            evidence_cutoff_at=_AS_OF,
        )
        with pytest.raises(ValueError, match="leaked"):
            validate_launch_training_example(example)

    def test_cutoff_after_outcome_rejected(self):
        example = LaunchTrainingExample(
            feature_vector=self._feature_vector(_AS_OF),
            outcome=self._outcome(_AS_OF + timedelta(days=40)),
            evidence_cutoff_at=_AS_OF + timedelta(days=60),
        )
        with pytest.raises(ValueError, match="post-launch"):
            validate_launch_training_example(example)


# ── Test 25: Determinism ─────────────────────────────────────────────

class TestDeterminism:
    def test_same_inputs_same_snapshot(self):
        a = _snapshot(
            understanding=_understanding(unknowns=[_critical_unknown()]),
            artifacts=[
                _artifact("integration_readiness", "negative",
                          rationale="integration failing"),
            ],
        )
        b = _snapshot(
            understanding=_understanding(unknowns=[_critical_unknown()]),
            artifacts=[
                _artifact("integration_readiness", "negative",
                          rationale="integration failing"),
            ],
        )
        assert a.model_dump() == b.model_dump()


# ── Test 26: Guardrails ──────────────────────────────────────────────

class TestGuardrails:
    def test_no_application_authority_imports(self):
        import inspect
        import tpllm.intelligence.launch_readiness as mod

        source = inspect.getsource(mod)
        forbidden = [
            "tpllm.application",
            "tpllm.structural.governance",
            "apply_calibration",
            "register_active_calibration",
            "transition_status",
            "issue_approval",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"launch readiness must not reference '{pattern}' — it "
                f"informs decisions but never grants application authority."
            )


# ── Test 27: End-to-end scenario ─────────────────────────────────────

class TestEndToEnd:
    def test_full_launch_scenario(self):
        contested_claim = UnderstandingClaim(
            id="c-mobile", project_id=_PROJECT,
            claim_type=UnderstandingClaimType.SCOPE,
            statement="Mobile is in v1 scope",
            status=UnderstandingClaimStatus.CONTESTED,
            confidence=0.4,
            evidence_ids=["pev-chat"],
            contradicting_evidence_ids=["pev-launch-doc"],
        )
        contradiction = ClaimContradiction(
            id="contra-mobile", project_id=_PROJECT,
            claim_a_id="c-mobile", claim_b_id="c-mobile-b",
            contradiction_type="included_vs_excluded", severity="high",
            evidence_ids=["pev-chat", "pev-launch-doc"],
            explanation="Mobile scope is contested for v1.",
        )
        billing_unknown = _critical_unknown(
            kind=ProjectUnknownKind.CONFLICTING_EVIDENCE,
            question="Is the Billing API contract resolved for downstream teams?",
        )
        artifacts = [
            _artifact("integration_readiness", "negative", strength=0.8,
                      rationale="Billing contract integration tests partially "
                                "failing", blocker_candidate=True),
            _artifact("operational_readiness", "negative", strength=0.8,
                      rationale="Rollback plan is missing",
                      blocker_candidate=True),
        ]
        goals = [
            SimpleNamespace(id="g-billing", name="Validate billing contract"),
        ]
        snapshot = _snapshot(
            understanding=_understanding(
                claims=[contested_claim],
                contradictions=[contradiction],
                unknowns=[billing_unknown],
            ),
            artifacts=artifacts,
            goals=goals,
        )

        scorecard = snapshot.scorecard
        forecast = snapshot.forecast

        # Scores and confidence exist, bounded, and separate.
        assert 0.0 < scorecard.overall_score < 100.0
        assert 0.0 < scorecard.overall_confidence < 1.0

        # Blockers include the billing critical unknown and affect forecast.
        assert any(
            b.severity == LaunchBlockerSeverity.CRITICAL
            and "billing" in b.title.lower()
            for b in scorecard.blockers
        )
        assert forecast.on_time_probability < 0.8
        assert forecast.expected_slip_days > 0
        assert forecast.method == LaunchForecastMethod.HEURISTIC_V1
        assert forecast.principal_risk_drivers

        # Contradiction remains visible.
        assert scorecard.contradiction_ids == ["contra-mobile"]

        # Goal estimate is evidence-bound and explained.
        goal = snapshot.goal_estimates[0]
        assert 0.0 <= goal.completion_probability <= 1.0
        assert goal.explanation
        assert goal.evidence_ids

        # Every scored dimension with evidence carries evidence references.
        for dim in scorecard.dimensions:
            if dim.evidence_count:
                assert dim.positive_evidence_ids or dim.negative_evidence_ids
