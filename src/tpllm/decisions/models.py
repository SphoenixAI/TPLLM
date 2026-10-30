"""Owned decision ontology; provider wire formats never leave their adapters."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tpllm.temporal.models import TemporalFactWindow

QuestionType = Literal["noul", "choice", "score"]
Mode = Literal["off", "shadow", "advisory"]


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DecisionQuestion(FrozenModel):
    decision_id: str = Field(pattern=r"^[a-z]+\.[a-z_]+$")
    version: int = Field(ge=1)
    question_type: QuestionType
    instructions: str = Field(min_length=8, max_length=4000)
    criteria: dict[str, str] | tuple[str, ...] | None = None
    domain: str
    description: str
    owner: str
    status: Literal["active", "retired"] = "active"

    @property
    def definition_id(self) -> str:
        return f"{self.decision_id}.v{self.version}"

    @property
    def definition_hash(self) -> str:
        return fingerprint(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def criteria_match(self):
        c = self.criteria
        if self.question_type == "noul" and c is not None:
            raise ValueError("Phase-one Noul definitions use instructions only")
        if self.question_type == "choice" and not (isinstance(c, dict) and 2 <= len(c) <= 255):
            raise ValueError("Choice requires a finite criteria map")
        if self.question_type == "score" and not (isinstance(c, tuple) and 2 <= len(c) <= 10 and len(set(c)) == len(c)):
            raise ValueError("Score requires 2-10 distinct ordered levels")
        if c is not None and any(not str(v).strip() for v in (c.values() if isinstance(c, dict) else c)):
            raise ValueError("Criteria descriptions cannot be empty")
        return self


class DecisionFact(FrozenModel):
    id: str
    kind: str
    evidence_refs: tuple[str, ...]
    valid_time: TemporalFactWindow
    # Values are deliberately shallow; never arbitrary connector metadata.
    attributes: dict[str, str | float | bool | None]


class DecisionState(FrozenModel):
    schema_version: str = "decision-state.v1"
    project_id: str
    entity_id: str
    valid_at: datetime
    known_at: datetime
    facts: tuple[DecisionFact, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    observation_refs: tuple[str, ...] = ()
    ontology_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def state_hash(self) -> str:
        return fingerprint(self.model_dump(mode="json"))


class DecisionResult(FrozenModel):
    question_type: QuestionType
    selected_value: str | float
    probability: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    probability_distribution: dict[str, float] = Field(default_factory=dict)
    legend: dict[str, str] = Field(default_factory=dict)
    raw_result: dict = Field(default_factory=dict)

    @property
    def margin(self) -> float | None:
        p = sorted(self.probability_distribution.values(), reverse=True)
        return p[0] - p[1] if len(p) > 1 else None

    def validate_for(self, question: DecisionQuestion) -> None:
        if self.question_type != question.question_type:
            raise ValueError("Question/result type mismatch")
        if self.question_type == "noul":
            if self.probability is None or self.selected_value != self.probability or self.confidence is not None:
                raise ValueError("Noul retains probability, without invented confidence or Boolean")
            if self.probability_distribution or self.legend:
                raise ValueError("Unexpected Noul distribution")
            return
        expected = (set(question.criteria) if self.question_type == "choice"
                    else {str(i) for i in range(len(question.criteria))})
        dist = self.probability_distribution
        if set(dist) != expected or any(not math.isfinite(p) or not 0 <= p <= 1 for p in dist.values()):
            raise ValueError("Invalid probability support")
        if not math.isclose(sum(dist.values()), 1, abs_tol=1e-4):
            raise ValueError("Distribution must sum to one")
        if self.confidence is None or self.probability is not None:
            raise ValueError("Choice/Score requires confidence, not a Noul probability")
        if self.question_type == "choice":
            if self.selected_value not in expected or dist[self.selected_value] < max(dist.values()) - 1e-8:
                raise ValueError("Choice must be a maximum-probability option")
            if self.legend:
                raise ValueError("Choice cannot carry a score legend")
        else:
            legend = {str(i): v for i, v in enumerate(question.criteria)}
            if self.legend != legend:
                raise ValueError("Score legend differs from trusted definition")
            expected_score = sum(int(k) * v for k, v in dist.items())
            if not isinstance(self.selected_value, (int, float)) or not math.isclose(self.selected_value, expected_score, abs_tol=1e-3):
                raise ValueError("Score differs from distribution expectation")


class DecisionEvaluation(FrozenModel):
    policy_id: str
    policy_hash: str
    status: Literal["accepted", "uncertain", "unavailable"]
    strength: Literal["strong", "weak", "ambiguous", "unavailable"]
    reasons: tuple[str, ...]
    influence_allowed: bool = False
    calibration_key: str | None = None
    calibration_samples: int = 0


class Usage(FrozenModel):
    # The journal refuses credential-like field names including '*token*'.
    input_count: int = Field(default=0, ge=0, strict=True)
    output_count: int = Field(default=0, ge=0, strict=True)


class ProviderBatch(FrozenModel):
    model_resolved: str
    results: dict[str, DecisionResult]
    errors: dict[str, str] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    attempts: int = 1


class DecisionTrace(FrozenModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    batch_id: str
    project_id: str
    entity_id: str
    definition: DecisionQuestion
    definition_hash: str
    provider: str
    model_requested: str
    model_resolved: str | None
    mode: Mode
    state_hash: str
    state_schema_version: str
    evidence_refs: tuple[str, ...]
    observation_refs: tuple[str, ...]
    ontology_refs: tuple[str, ...]
    evidence_cutoff: datetime
    temporal: TemporalFactWindow
    created_at: datetime
    inference_at: datetime
    reused_from: str | None = None
    latency_ms: float
    usage: Usage
    question_count: int
    inference_performed: bool = True
    result: DecisionResult | None
    error: str | None
    policy_evaluation: DecisionEvaluation
    policy_parameters: dict
    calibration_profile: DecisionCalibrationProfile | None = None
    baseline_value: str | float | bool | None = None
    disagreement: bool | None = None
    downstream_effect: Literal["none", "review_priority"] = "none"
    supersedes: str | None = None


class CalibrationLabel(FrozenModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str
    value: str | float | bool
    source: Literal["human_review", "observed_outcome", "corrected_classification", "alternative_provider", "fixture"]
    reviewer: str
    evidence_refs: tuple[str, ...] = ()
    observed_at: datetime
    recorded_at: datetime
    supersedes: str | None = None


class DecisionCalibrationProfile(FrozenModel):
    key: str
    samples: int = 0
    brier: float | None = None
    calibration_error: float | None = None
    mean_absolute_error: float | None = None
    # Eligibility comes from explicit governed promotion, never from the model.
    approved: bool = False
    expires_at: datetime | None = None
    evidence_refs: tuple[str, ...] = ()


DecisionTrace.model_rebuild()
