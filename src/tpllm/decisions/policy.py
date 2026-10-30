"""Conservative advisory acceptance; no action or permission output exists."""
from datetime import datetime
from pydantic import Field
from tpllm.decisions.models import (DecisionCalibrationProfile, DecisionEvaluation, DecisionQuestion,
    DecisionResult, FrozenModel, fingerprint, utc)


def calibration_key(question, provider, model, project_id):
    return "|".join((project_id, question.definition_id, question.definition_hash, provider, model))


class DecisionAcceptancePolicy(FrozenModel):
    policy_id: str = "bounded-advisory.v1"
    min_probability: float = Field(default=.9, ge=.5, le=1)
    min_confidence: float = Field(default=.8, ge=0, le=1)
    min_margin: float = Field(default=.2, ge=0, le=1)
    min_evidence: int = Field(default=2, ge=1)
    min_samples: int = Field(default=50, ge=1)
    max_brier: float = Field(default=.15, ge=0, le=2)
    max_calibration_error: float = Field(default=.1, ge=0, le=1)
    max_score_mae: float = Field(default=.5, ge=0)
    max_evidence_age_hours: float = Field(default=168, gt=0)
    # Deployment-controlled approvals, exact definition/model/project segment.
    approved_segments: tuple[str, ...] = ()


class DecisionPolicyGate:
    def __init__(self, policy=None):
        self.policy = policy or DecisionAcceptancePolicy()

    def evaluate(self, *, question: DecisionQuestion, result: DecisionResult | None, provider: str,
                 model: str | None, state, mode, at: datetime, profile: DecisionCalibrationProfile | None = None,
                 kill_switch=False, action_sensitivity="review_only") -> DecisionEvaluation:
        p = self.policy
        base = dict(policy_id=p.policy_id, policy_hash=fingerprint(p.model_dump(mode="json")))
        if result is None:
            return DecisionEvaluation(**base, status="unavailable", strength="unavailable", reasons=("No valid provider result.",))
        result.validate_for(question)
        strong = (max(result.probability, 1-result.probability) >= p.min_probability if question.question_type == "noul"
                  else result.confidence >= p.min_confidence)
        ambiguous = question.question_type == "choice" and (result.margin is None or result.margin < p.min_margin
                        or result.selected_value in {"unknown", "ambiguous"})
        strength = "ambiguous" if ambiguous else "strong" if strong else "weak"
        key = calibration_key(question, provider, model or "unresolved", state.project_id)
        reasons = []
        if not model or model in {"jev-latest", "jev-preview"}:
            reasons.append("Provider did not report a pinned model version.")
        if not strong or ambiguous:
            reasons.append("Judgment is weak or alternatives remain ambiguous.")
        if len(state.evidence_refs) < p.min_evidence:
            reasons.append("Insufficient independently traceable evidence references.")
        source_times = [utc(f.valid_time.valid_from) for f in state.facts if f.kind not in {"kpi_trend", "dependency", "project_definition"}]
        if not source_times or (utc(at) - max(source_times)).total_seconds() > p.max_evidence_age_hours * 3600:
            reasons.append("Evidence is missing or stale.")
        if action_sensitivity != "review_only":
            reasons.append("This policy cannot authorize action-sensitive use.")
        if not profile or profile.key != key or not profile.approved or key not in p.approved_segments:
            reasons.append("No governed calibration approval for this definition, model and project.")
        elif (profile.samples < p.min_samples or profile.expires_at is None or utc(profile.expires_at) <= utc(at)
                or not profile.evidence_refs):
            reasons.append("Calibration coverage or validity is insufficient.")
        elif (profile.brier is None or profile.brier > p.max_brier or profile.calibration_error is None
                or profile.calibration_error > p.max_calibration_error or (question.question_type == "score"
                and (profile.mean_absolute_error is None or profile.mean_absolute_error > p.max_score_mae))):
            reasons.append("Calibration quality is outside configured limits.")
        if kill_switch:
            reasons.append("Existing kill switch disables advisory influence.")
        accepted = not reasons
        return DecisionEvaluation(**base, status="accepted" if accepted else "uncertain", strength=strength,
            reasons=tuple(reasons or ["Calibrated advisory judgment; existing validation and authority remain required."]),
            influence_allowed=accepted and mode == "advisory", calibration_key=key,
            calibration_samples=profile.samples if profile else 0)
