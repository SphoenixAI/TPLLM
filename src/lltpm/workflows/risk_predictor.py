"""Risk Prediction Engine — predict problems before they become blockers.

Uses historical patterns from:
- Milestone completion history (who slips, how often, by how much)
- Engagement profiles (response degradation over time)
- Scope change frequency (creep detection)
- Delegation completion rates (per person reliability)
- Nudge escalation patterns (who needs escalation)

Also generates quarterly employee evaluation reports from the same data.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.prediction import (
    PerformanceGrade,
    PersonMetrics,
    PredictionConfidence,
    QuarterlyEvaluation,
    RiskPrediction,
    RiskSignalType,
)

PREDICTIONS_ARTIFACT = "risk_predictions"


class RiskPredictor:
    """Predict risks and generate evaluation reports from historical data."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Risk prediction ───────────────────────────────────────

    def predict_risks(self, program_id: str) -> list[RiskPrediction]:
        """Run all prediction models and return predicted risks."""
        predictions: list[RiskPrediction] = []

        predictions.extend(self._predict_milestone_slips(program_id))
        predictions.extend(self._predict_capacity_overload(program_id))
        predictions.extend(self._predict_engagement_drops(program_id))
        predictions.extend(self._predict_scope_creep(program_id))
        predictions.extend(self._predict_commitment_misses(program_id))

        # Save predictions
        self._save_predictions(program_id, predictions)
        return predictions

    def get_predictions(self, program_id: str) -> list[RiskPrediction]:
        """Get saved predictions."""
        return self._load_predictions(program_id)

    def get_prediction_summary(self, program_id: str) -> str:
        """Get a formatted summary of current risk predictions."""
        predictions = self._load_predictions(program_id)
        if not predictions:
            return "No risk predictions generated yet. Run predict_risks to analyze."

        lines = ["# Risk Predictions"]
        by_confidence: dict[str, list[RiskPrediction]] = {}
        for p in predictions:
            by_confidence.setdefault(p.confidence.value, []).append(p)

        for conf in ["high", "medium", "low"]:
            preds = by_confidence.get(conf, [])
            if preds:
                lines.append(f"\n## {conf.upper()} confidence ({len(preds)})")
                for p in preds:
                    lines.append(
                        f"- **{p.title}** ({p.signal_type.value}): "
                        f"{p.description} — probability: {p.probability:.0%}"
                    )
                    if p.recommended_actions:
                        for action in p.recommended_actions:
                            lines.append(f"  → {action}")

        return "\n".join(lines)

    # ── Quarterly evaluations ─────────────────────────────────

    def generate_evaluation(
        self,
        program_id: str,
        person: str,
        quarter: str = "",
        metrics: PersonMetrics | None = None,
    ) -> QuarterlyEvaluation:
        """Generate a quarterly evaluation report for a person.

        If metrics are not provided, collects them from available data sources.
        """
        if not quarter:
            now = date.today()
            q = (now.month - 1) // 3 + 1
            quarter = f"{now.year}-Q{q}"

        if not metrics:
            metrics = self._collect_metrics(program_id, person)

        evaluation = QuarterlyEvaluation(
            person=person,
            program_id=program_id,
            quarter=quarter,
            metrics=metrics,
        )

        # Grade each dimension
        evaluation.responsiveness_grade = self._grade_responsiveness(metrics)
        evaluation.reliability_grade = self._grade_reliability(metrics)
        evaluation.collaboration_grade = self._grade_collaboration(metrics)
        evaluation.overall_grade = self._compute_overall_grade(evaluation)

        # Generate narrative
        evaluation.strengths = self._identify_strengths(metrics)
        evaluation.areas_for_improvement = self._identify_improvements(metrics)
        evaluation.recommendations = self._generate_recommendations(metrics, evaluation)

        # Save
        self._save_evaluation(program_id, person, quarter, evaluation)
        return evaluation

    def get_evaluation(
        self,
        program_id: str,
        person: str,
        quarter: str = "",
    ) -> QuarterlyEvaluation | None:
        """Get a saved evaluation."""
        if not quarter:
            now = date.today()
            q = (now.month - 1) // 3 + 1
            quarter = f"{now.year}-Q{q}"
        return self._load_evaluation(program_id, person, quarter)

    def render_evaluation(self, evaluation: QuarterlyEvaluation) -> str:
        """Render an evaluation as formatted text."""
        m = evaluation.metrics
        lines = [
            f"# Quarterly Evaluation: {evaluation.person}",
            f"**Program:** {evaluation.program_id} | **Quarter:** {evaluation.quarter}",
            f"**Overall Grade:** {evaluation.overall_grade.value.upper()}",
            f"**Trend:** {evaluation.trend}",
            "",
            "## Grades",
            f"| Dimension | Grade |",
            f"|-----------|-------|",
            f"| Responsiveness | {evaluation.responsiveness_grade.value} |",
            f"| Reliability | {evaluation.reliability_grade.value} |",
            f"| Collaboration | {evaluation.collaboration_grade.value} |",
            f"| **Overall** | **{evaluation.overall_grade.value}** |",
            "",
            "## Key Metrics",
            f"- Response rate: {m.response_rate:.0%} ({m.total_responses}/{m.total_requests_received})",
            f"- High-priority response rate: {m.high_priority_response_rate:.0%}",
            f"- Avg response time: {m.avg_response_hours:.1f}h",
            f"- Commitment follow-through: {m.commitment_follow_through:.0%} ({m.commitments_met}/{m.commitments_made})",
            f"- Collaboration score: {m.collaboration_score:.0%}",
            f"- Ignored messages: {m.ignored_messages}",
        ]

        if evaluation.strengths:
            lines.append("\n## Strengths")
            for s in evaluation.strengths:
                lines.append(f"- {s}")

        if evaluation.areas_for_improvement:
            lines.append("\n## Areas for Improvement")
            for a in evaluation.areas_for_improvement:
                lines.append(f"- {a}")

        if evaluation.recommendations:
            lines.append("\n## Recommendations")
            for r in evaluation.recommendations:
                lines.append(f"- {r}")

        return "\n".join(lines)

    # ── Prediction models ─────────────────────────────────────

    def _predict_milestone_slips(self, program_id: str) -> list[RiskPrediction]:
        """Predict milestone slips from historical patterns."""
        predictions = []

        from lltpm.models.milestone import MilestonePlan
        plan = self._store.load(program_id, "milestone_plan", MilestonePlan)
        if not plan:
            return predictions

        at_risk = plan.at_risk
        overdue = plan.overdue

        if len(at_risk) > 0:
            predictions.append(RiskPrediction(
                id=f"PRED-{uuid.uuid4().hex[:8]}",
                signal_type=RiskSignalType.MILESTONE_SLIP,
                title=f"{len(at_risk)} milestones at risk of slipping",
                description=f"Milestones at risk: {', '.join(m.name for m in at_risk)}",
                confidence=PredictionConfidence.HIGH if len(at_risk) >= 2 else PredictionConfidence.MEDIUM,
                probability=min(0.9, 0.3 + 0.2 * len(at_risk)),
                impact="high",
                affected_milestones=[m.id for m in at_risk],
                affected_people=[m.owner for m in at_risk],
                recommended_actions=[
                    f"Check in with {m.owner} about {m.name}" for m in at_risk
                ],
                data_points=[f"{m.name}: {m.status.value}" for m in at_risk],
                program_id=program_id,
            ))

        if len(overdue) > 0:
            predictions.append(RiskPrediction(
                id=f"PRED-{uuid.uuid4().hex[:8]}",
                signal_type=RiskSignalType.MILESTONE_SLIP,
                title=f"{len(overdue)} milestones already overdue",
                description=f"Overdue: {', '.join(m.name for m in overdue)}",
                confidence=PredictionConfidence.HIGH,
                probability=1.0,
                impact="critical",
                affected_milestones=[m.id for m in overdue],
                affected_people=[m.owner for m in overdue],
                recommended_actions=[
                    "Assess downstream impact immediately",
                    "Create recovery plan with revised dates",
                ],
                data_points=[f"{m.name}: due {m.target_date}" for m in overdue if m.target_date],
                program_id=program_id,
            ))

        return predictions

    def _predict_capacity_overload(self, program_id: str) -> list[RiskPrediction]:
        """Predict capacity issues from delegation load."""
        predictions = []

        from lltpm.models.delegation import DelegationPlan
        plan = self._store.load(program_id, "delegation_plan", DelegationPlan)
        if not plan:
            return predictions

        # Count active items per person
        person_load: dict[str, int] = {}
        for item in plan.items:
            if item.status.value in ("ASSIGNED", "IN_PROGRESS"):
                person_load[item.assigned_to] = person_load.get(item.assigned_to, 0) + 1

        for person, count in person_load.items():
            if count >= 5:
                predictions.append(RiskPrediction(
                    id=f"PRED-{uuid.uuid4().hex[:8]}",
                    signal_type=RiskSignalType.CAPACITY_OVERLOAD,
                    title=f"{person} has {count} active delegations",
                    description=f"{person} may be overloaded with {count} concurrent tasks",
                    confidence=PredictionConfidence.HIGH if count >= 7 else PredictionConfidence.MEDIUM,
                    probability=min(0.9, 0.4 + 0.1 * count),
                    impact="medium",
                    affected_people=[person],
                    recommended_actions=[
                        f"Review {person}'s workload and reprioritize",
                        f"Consider redistributing {count - 3} items to others",
                    ],
                    data_points=[f"{count} active delegations"],
                    program_id=program_id,
                ))

        return predictions

    def _predict_engagement_drops(self, program_id: str) -> list[RiskPrediction]:
        """Predict engagement degradation from engagement profiles."""
        predictions = []

        raw = self._store.load_raw(program_id, "engagement_profiles")
        if not raw:
            return predictions

        try:
            from lltpm.models.engagement import EngagementProfile
            profiles = json.loads(raw)
            for name, data in profiles.items():
                profile = EngagementProfile.model_validate(data)
                if profile.grade.value in ("SLOW", "UNRESPONSIVE"):
                    predictions.append(RiskPrediction(
                        id=f"PRED-{uuid.uuid4().hex[:8]}",
                        signal_type=RiskSignalType.ENGAGEMENT_DROP,
                        title=f"{name} has {profile.grade.value} engagement",
                        description=(
                            f"{name} avg response: {profile.avg_response_hours:.0f}h, "
                            f"response rate: {profile.response_rate:.0%}"
                        ),
                        confidence=PredictionConfidence.HIGH if profile.total_requests >= 5 else PredictionConfidence.LOW,
                        probability=0.7 if profile.grade.value == "UNRESPONSIVE" else 0.4,
                        impact="medium",
                        affected_people=[name],
                        recommended_actions=[
                            f"Switch to {profile.preferred_channel.value} for {name}",
                            f"Use quick-response options exclusively",
                            f"Consider batching requests during {profile.best_response_window}",
                        ],
                        data_points=[
                            f"Response rate: {profile.response_rate:.0%}",
                            f"Avg response: {profile.avg_response_hours:.0f}h",
                        ],
                        program_id=program_id,
                    ))
        except (json.JSONDecodeError, TypeError):
            pass

        return predictions

    def _predict_scope_creep(self, program_id: str) -> list[RiskPrediction]:
        """Predict scope creep from scope change request patterns."""
        predictions = []

        from lltpm.models.scope import ScopeBaseline
        baseline = self._store.load(program_id, "scope_baseline", ScopeBaseline)
        if not baseline:
            return predictions

        pending = [cr for cr in baseline.change_requests if cr.status.value in ("PROPOSED", "UNDER_REVIEW")]
        approved = [cr for cr in baseline.change_requests if cr.status.value == "APPROVED"]

        total_changes = len(baseline.change_requests)
        if total_changes >= 3:
            predictions.append(RiskPrediction(
                id=f"PRED-{uuid.uuid4().hex[:8]}",
                signal_type=RiskSignalType.SCOPE_CREEP,
                title=f"Scope creep: {total_changes} change requests",
                description=(
                    f"{total_changes} scope changes ({len(pending)} pending, "
                    f"{len(approved)} approved). Program scope may be expanding uncontrolled."
                ),
                confidence=PredictionConfidence.HIGH if total_changes >= 5 else PredictionConfidence.MEDIUM,
                probability=min(0.9, 0.3 + 0.15 * total_changes),
                impact="high",
                recommended_actions=[
                    "Review all pending scope changes for necessity",
                    "Consider scope freeze if approaching launch",
                    "Assess cumulative timeline impact of approved changes",
                ],
                data_points=[f"Total SCRs: {total_changes}"],
                program_id=program_id,
            ))

        return predictions

    def _predict_commitment_misses(self, program_id: str) -> list[RiskPrediction]:
        """Predict commitment misses from nudge escalation patterns."""
        predictions = []

        raw = self._store.load_raw(program_id, "nudge_log")
        if not raw:
            return predictions

        try:
            from lltpm.models.nudge import NudgeLog
            log = NudgeLog.model_validate(json.loads(raw))

            # People who required escalation
            escalated_people: dict[str, int] = {}
            for record in log.records:
                if record.current_tier.value in ("FIRM_REMIND", "ESCALATE"):
                    escalated_people[record.person] = escalated_people.get(record.person, 0) + 1

            for person, count in escalated_people.items():
                if count >= 2:
                    predictions.append(RiskPrediction(
                        id=f"PRED-{uuid.uuid4().hex[:8]}",
                        signal_type=RiskSignalType.COMMITMENT_MISS,
                        title=f"{person} required escalation {count} times",
                        description=f"{person} has a pattern of not responding until escalated",
                        confidence=PredictionConfidence.HIGH if count >= 3 else PredictionConfidence.MEDIUM,
                        probability=min(0.85, 0.3 + 0.2 * count),
                        impact="medium",
                        affected_people=[person],
                        recommended_actions=[
                            f"Use quick-response options for all requests to {person}",
                            f"Set shorter initial deadlines",
                            f"Consider reassigning critical-path items from {person}",
                        ],
                        data_points=[f"Escalated {count} times"],
                        program_id=program_id,
                    ))
        except (json.JSONDecodeError, TypeError):
            pass

        return predictions

    # ── Metric collection ─────────────────────────────────────

    def _collect_metrics(self, program_id: str, person: str) -> PersonMetrics:
        """Collect metrics for a person from all available data sources."""
        metrics = PersonMetrics(person=person)

        # From engagement profiles
        raw = self._store.load_raw(program_id, "engagement_profiles")
        if raw:
            try:
                profiles = json.loads(raw)
                if person in profiles:
                    from lltpm.models.engagement import EngagementProfile
                    profile = EngagementProfile.model_validate(profiles[person])
                    metrics.total_requests_received = profile.total_requests
                    metrics.total_responses = int(profile.total_requests * profile.response_rate)
                    metrics.avg_response_hours = profile.avg_response_hours
                    metrics.ignored_messages = profile.total_ignored
            except (json.JSONDecodeError, TypeError):
                pass

        # From delegation plan
        from lltpm.models.delegation import DelegationPlan
        plan = self._store.load(program_id, "delegation_plan", DelegationPlan)
        if plan:
            person_items = [i for i in plan.items if i.assigned_to == person]
            metrics.delegations_completed = sum(
                1 for i in person_items if i.status.value == "COMPLETED"
            )
            metrics.delegations_overdue = sum(
                1 for i in person_items if i.status.value in ("ASSIGNED", "IN_PROGRESS")
                and i.due_date and i.due_date < date.today()
            )
            metrics.commitments_made = len(person_items)
            metrics.commitments_met = metrics.delegations_completed

        return metrics

    # ── Grading ───────────────────────────────────────────────

    def _grade_responsiveness(self, metrics: PersonMetrics) -> PerformanceGrade:
        rate = metrics.response_rate
        hp_rate = metrics.high_priority_response_rate
        avg_h = metrics.avg_response_hours

        if rate >= 0.95 and avg_h <= 4:
            return PerformanceGrade.EXCEPTIONAL
        elif rate >= 0.85 and avg_h <= 8:
            return PerformanceGrade.STRONG
        elif rate >= 0.70 and avg_h <= 24:
            return PerformanceGrade.MEETING
        elif rate >= 0.50:
            return PerformanceGrade.BELOW
        else:
            return PerformanceGrade.CRITICAL

    def _grade_reliability(self, metrics: PersonMetrics) -> PerformanceGrade:
        ft = metrics.commitment_follow_through
        if ft >= 0.95:
            return PerformanceGrade.EXCEPTIONAL
        elif ft >= 0.85:
            return PerformanceGrade.STRONG
        elif ft >= 0.70:
            return PerformanceGrade.MEETING
        elif ft >= 0.50:
            return PerformanceGrade.BELOW
        else:
            return PerformanceGrade.CRITICAL

    def _grade_collaboration(self, metrics: PersonMetrics) -> PerformanceGrade:
        cs = metrics.collaboration_score
        if cs >= 0.8:
            return PerformanceGrade.EXCEPTIONAL
        elif cs >= 0.65:
            return PerformanceGrade.STRONG
        elif cs >= 0.45:
            return PerformanceGrade.MEETING
        elif cs >= 0.3:
            return PerformanceGrade.BELOW
        else:
            return PerformanceGrade.CRITICAL

    def _compute_overall_grade(self, evaluation: QuarterlyEvaluation) -> PerformanceGrade:
        grades = [
            evaluation.responsiveness_grade,
            evaluation.reliability_grade,
            evaluation.collaboration_grade,
        ]
        order = list(PerformanceGrade)
        indices = [order.index(g) for g in grades]
        avg = sum(indices) / len(indices)

        # Round to nearest grade
        return order[round(avg)]

    # ── Narrative generation ──────────────────────────────────

    def _identify_strengths(self, metrics: PersonMetrics) -> list[str]:
        strengths = []
        if metrics.response_rate >= 0.9:
            strengths.append(f"Excellent response rate ({metrics.response_rate:.0%})")
        if metrics.avg_response_hours <= 4:
            strengths.append(f"Fast average response time ({metrics.avg_response_hours:.1f}h)")
        if metrics.commitment_follow_through >= 0.9:
            strengths.append(f"High commitment follow-through ({metrics.commitment_follow_through:.0%})")
        if metrics.collaboration_score >= 0.7:
            strengths.append(f"Strong collaborator — unblocked others {metrics.unblocked_others} times")
        if metrics.high_priority_response_rate >= 0.95:
            strengths.append("Consistently responsive to high-priority requests")
        return strengths

    def _identify_improvements(self, metrics: PersonMetrics) -> list[str]:
        improvements = []
        if metrics.response_rate < 0.7:
            improvements.append(f"Response rate is {metrics.response_rate:.0%} — target is 85%+")
        if metrics.ignored_messages > 3:
            improvements.append(f"{metrics.ignored_messages} messages ignored — consider quicker acknowledgments")
        if metrics.commitment_follow_through < 0.7:
            improvements.append(
                f"Commitment follow-through is {metrics.commitment_follow_through:.0%} — "
                f"{metrics.commitments_missed} commitments missed"
            )
        if metrics.avg_response_hours > 24:
            improvements.append(f"Average response time is {metrics.avg_response_hours:.0f}h — target is <8h")
        if metrics.was_blocker > metrics.unblocked_others:
            improvements.append(
                f"Was a blocker {metrics.was_blocker} times vs unblocking others {metrics.unblocked_others} times"
            )
        return improvements

    def _generate_recommendations(
        self,
        metrics: PersonMetrics,
        evaluation: QuarterlyEvaluation,
    ) -> list[str]:
        recommendations = []
        if evaluation.responsiveness_grade.value in ("below", "critical"):
            recommendations.append(
                "Set up quick-response options for all routine requests"
            )
        if evaluation.reliability_grade.value in ("below", "critical"):
            recommendations.append(
                "Break down large commitments into smaller, trackable milestones"
            )
        if evaluation.collaboration_grade.value in ("below", "critical"):
            recommendations.append(
                "Pair with a responsive team member for time-sensitive items"
            )
        if metrics.delegations_overdue > 0:
            recommendations.append(
                f"Address {metrics.delegations_overdue} overdue delegations"
            )
        return recommendations

    # ── Persistence ───────────────────────────────────────────

    def _save_predictions(self, program_id: str, predictions: list[RiskPrediction]) -> None:
        data = [p.model_dump() for p in predictions]
        self._store.save_raw(
            program_id, PREDICTIONS_ARTIFACT,
            json.dumps(data, indent=2, default=str),
        )

    def _load_predictions(self, program_id: str) -> list[RiskPrediction]:
        raw = self._store.load_raw(program_id, PREDICTIONS_ARTIFACT)
        if not raw:
            return []
        try:
            return [RiskPrediction.model_validate(p) for p in json.loads(raw)]
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_evaluation(
        self, program_id: str, person: str, quarter: str, evaluation: QuarterlyEvaluation,
    ) -> None:
        name = f"eval_{person}_{quarter}"
        self._store.save_raw(
            program_id, name,
            json.dumps(evaluation.model_dump(), indent=2, default=str),
        )

    def _load_evaluation(
        self, program_id: str, person: str, quarter: str,
    ) -> QuarterlyEvaluation | None:
        name = f"eval_{person}_{quarter}"
        raw = self._store.load_raw(program_id, name)
        if not raw:
            return None
        try:
            return QuarterlyEvaluation.model_validate(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return None
