"""Risk prediction — predict problems before they become blockers.

Uses historical patterns from:
- Milestone completion history (which milestones slip, how often, by how much)
- Scope change frequency (creep detection)
- Delegation load (how much concurrent work is assigned to one point)

**What was removed in Commit 41, and why.** This engine also generated
quarterly employee evaluations — responsiveness, reliability and
collaboration letter grades — and two of its five risk models predicted
individuals rather than structure:

- *engagement drops* reported "Alice has UNRESPONSIVE engagement, avg
  response 48h, response rate 30%";
- *commitment misses* reported "Bob required escalation 3 times; has a
  pattern of not responding until escalated", and recommended
  "reassigning critical-path items from Bob".

Both were grading dressed as forecasting. The structural reading of the
same evidence is more useful and more likely true: a long reply latency
usually means a person sits in several projects' critical paths, which
is a fact about how work was routed. That question belongs to the
portfolio and coordination layers, which model approval queues and
shared authorities directly.

What remains predicts milestones, scope and allocation load. Where a
person appears it is as the locus of an allocation decision, never as
its cause.
"""

from __future__ import annotations

import json
import uuid

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.prediction import (
    PredictionConfidence,
    RiskPrediction,
    RiskSignalType,
)

PREDICTIONS_ARTIFACT = "risk_predictions"


class RiskPredictor:
    """Predict structural risks from historical patterns."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Risk prediction ───────────────────────────────────────

    def predict_risks(self, program_id: str) -> list[RiskPrediction]:
        """Run all prediction models and return predicted risks."""
        predictions: list[RiskPrediction] = []

        predictions.extend(self._predict_milestone_slips(program_id))
        predictions.extend(self._predict_allocation_load(program_id))
        predictions.extend(self._predict_scope_creep(program_id))

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

    def _predict_allocation_load(
        self, program_id: str,
    ) -> list[RiskPrediction]:
        """Where concurrent work has piled up at one assignment point.

        This is a statement about distribution, not about whoever holds
        the queue: the organization assigned nine concurrent items to
        one place, and the remedy is redistribution. The wording
        matters, because the same count can be written as a capacity
        observation or as an accusation, and the old version leaned
        toward the second ("may be overloaded", "review their
        workload").
        """
        predictions = []

        from lltpm.models.delegation import DelegationPlan
        plan = self._store.load(program_id, "delegation_plan", DelegationPlan)
        if not plan:
            return predictions

        concurrent: dict[str, int] = {}
        for item in plan.items:
            if item.status.value in ("ASSIGNED", "IN_PROGRESS"):
                concurrent[item.assigned_to] = (
                    concurrent.get(item.assigned_to, 0) + 1
                )

        for holder, count in sorted(concurrent.items()):
            if count < 5:
                continue
            predictions.append(RiskPrediction(
                id=f"PRED-{uuid.uuid4().hex[:8]}",
                signal_type=RiskSignalType.ALLOCATION_LOAD,
                title=f"{count} concurrent items assigned to {holder}",
                description=(
                    f"{count} items are assigned to {holder} at once. "
                    f"Concurrent load at a single assignment point "
                    f"lengthens everything in the queue behind it, "
                    f"whoever holds it."
                ),
                confidence=(
                    PredictionConfidence.HIGH if count >= 7
                    else PredictionConfidence.MEDIUM
                ),
                probability=min(0.9, 0.4 + 0.1 * count),
                impact="medium",
                affected_people=[holder],
                recommended_actions=[
                    f"Redistribute {count - 3} item(s) to another "
                    f"assignment point",
                    "Sequence the remainder explicitly rather than "
                    "running them concurrently",
                ],
                data_points=[f"{count} concurrent items"],
                program_id=program_id,
            ))

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
