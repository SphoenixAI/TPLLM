"""Lineage is shared; probabilistic judgments and synthesis stay distinct."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from tpllm.decisions.models import DecisionState, DecisionTrace, FrozenModel, Usage, fingerprint, utc

Lane = Literal["decision", "reasoning", "hybrid_evaluation"]
COHORTS = ("software.deployments", "software.reliability", "project.dependencies",
           "project.schedule", "security.events", "team.capacity", "cost.variance", "general.operations")


class IntelligenceTask(FrozenModel):
    task_id: str
    version: int = Field(ge=1)
    lane: Lane
    domain: str
    description: str
    required_context: tuple[str, ...] = ("evidence",)
    evidence_policy: str = "bounded-bitemporal.v1"
    output_contract: str
    schema_version: int = 1
    output_schema: dict = Field(default_factory=dict)
    cohort_policy: tuple[str, ...] = COHORTS
    sensitivity: Literal["review_only"] = "review_only"
    evaluation_policy: str
    provider_constraints: tuple[str, ...] = ()

    @property
    def definition_id(self):
        return f"{self.task_id}.v{self.version}"

    @property
    def definition_hash(self):
        return fingerprint(self.model_dump(mode="json"))


class CohortAssignment(FrozenModel):
    cohort_id: str
    cohort_version: int = 1
    assignment_method: Literal["ontology", "deterministic_rule", "entity_type", "manual", "fallback"]
    basis: str

    @model_validator(mode="after")
    def known_cohort(self):
        if self.cohort_id not in COHORTS or self.cohort_version != 1:
            raise ValueError("Unknown cohort definition")
        return self


class EvidenceExcerpt(FrozenModel):
    evidence_ref: str
    text: str = Field(max_length=1000)


class InsightContext(FrozenModel):
    id: str
    summary: str = Field(max_length=2000)
    evidence_refs: tuple[str, ...]
    valid_at: datetime
    known_at: datetime
    validation_outcome: str


class EvidencePacket(FrozenModel):
    schema_version: str = "evidence-packet.v1"
    state: DecisionState
    cohort: CohortAssignment
    excerpts: tuple[EvidenceExcerpt, ...] = ()
    entity_refs: tuple[str, ...] = ()
    kpi_refs: tuple[str, ...] = ()
    trend_refs: tuple[str, ...] = ()
    dependency_refs: tuple[str, ...] = ()
    task_refs: tuple[str, ...] = ()
    insight: InsightContext | None = None
    limitations: tuple[str, ...] = ()

    @property
    def id(self):
        return "packet-" + fingerprint(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def temporal_integrity(self):
        s = self.state
        refs = set(s.evidence_refs)
        for fact in s.facts:
            if (utc(fact.valid_time.valid_from) > utc(s.valid_at)
                    or utc(fact.valid_time.known_from) > utc(s.known_at)
                    or not set(fact.evidence_refs) <= refs):
                raise ValueError("Fact outside packet lineage or cutoff")
        if any(e.evidence_ref not in refs for e in self.excerpts):
            raise ValueError("Excerpt outside packet lineage")
        if self.insight and (utc(self.insight.valid_at) > utc(s.valid_at)
                or utc(self.insight.known_at) > utc(s.known_at)
                or not set(self.insight.evidence_refs) <= refs):
            raise ValueError("Insight outside packet lineage or cutoff")
        if len(self.excerpts) > 20 or len(self.model_dump_json().encode()) > 64000:
            raise ValueError("Evidence packet exceeds bounded context")
        return self


class ExecutionMetadata(FrozenModel):
    request_id: str
    latency_ms: float = Field(ge=0)
    input_bytes: int = Field(ge=0)
    usage: Usage | None = None
    attempts: int = Field(default=1, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    cost_basis: Literal["unavailable", "provider_reported", "configured_estimate"] = "unavailable"
    provenance: Literal["mock", "live"]


class ReasoningTrace(FrozenModel):
    schema_id: str
    schema_version: int
    structured_output: dict | None
    schema_valid: bool | None
    evidence_refs_valid: bool | None
    unsupported_claim_flags: tuple[str, ...] = ()
    reference_count: int = 0
    unsupported_reference_count: int = 0
    # Deliberately excludes hidden reasoning, raw transport responses and tools.
    provider_metadata: dict[str, str] = Field(default_factory=dict)
    downstream_effect: Literal["none"] = "none"


class IntelligenceRecord(FrozenModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task: IntelligenceTask
    project_id: str
    entity_id: str
    cohort: CohortAssignment
    provider: str
    requested_model: str
    resolved_model: str | None
    evidence_packet_id: str
    context_hash: str
    evidence_refs: tuple[str, ...]
    created_at: datetime
    valid_time: datetime
    transaction_time: datetime
    execution: ExecutionMetadata
    status: Literal["available", "unavailable", "rejected", "skipped"]
    failure_kind: Literal["provider_failure", "schema_failure", "state_builder_failure", "evidence_validation_failure"] | None = None
    reason: str | None = None
    decision: DecisionTrace | None = None
    reasoning: ReasoningTrace | None = None
    downstream_effect: Literal["none"] = "none"

    @model_validator(mode="after")
    def distinct_payloads(self):
        if self.decision is not None and (self.reasoning is not None or self.task.lane != "decision"):
            raise ValueError("Decision payload belongs only to the decision lane")
        if self.reasoning is not None and self.task.lane != "reasoning":
            raise ValueError("Reasoning payload belongs only to the reasoning lane")
        if self.status == "available" and ((self.task.lane == "decision" and not self.decision)
                or (self.task.lane == "reasoning" and not self.reasoning)):
            raise ValueError("Available result requires its lane payload")
        if self.decision and (self.decision.mode != "shadow" or self.decision.policy_evaluation.influence_allowed):
            raise ValueError("Phase 2 envelopes are shadow only")
        if self.decision and (self.decision.definition.definition_id != self.task.definition_id
                or self.decision.provider != self.provider or self.decision.model_requested != self.requested_model
                or self.decision.model_resolved != self.resolved_model or self.decision.project_id != self.project_id
                or self.decision.entity_id != self.entity_id):
            raise ValueError("Envelope does not match its decision trace")
        if self.status == "available" and ((self.decision and self.decision.result is None)
                or (self.reasoning and (self.reasoning.structured_output is None
                    or not self.reasoning.schema_valid or not self.reasoning.evidence_refs_valid))):
            raise ValueError("Invalid or unavailable payload cannot be available")
        if utc(self.transaction_time) > utc(self.created_at) or utc(self.valid_time) > utc(self.created_at):
            raise ValueError("Inference cannot precede evidence cutoff")
        return self


class ReviewEvent(FrozenModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    record_id: str
    label: Literal["agree", "disagree", "ambiguous", "insufficient_evidence", "correct_label",
                   "accept", "partially_correct", "reject", "missing_evidence", "unsupported_inference", "corrected_interpretation"]
    reviewer: str = Field(min_length=1, max_length=160)
    interpretation: str = Field(default="", max_length=3000)
    decision_value: str | float | bool | None = None
    evidence_refs: tuple[str, ...] = ()
    observed_at: datetime
    recorded_at: datetime
    source: Literal["human", "fixture"] = "human"
    supersedes: str | None = None


class OutcomeEvent(FrozenModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    record_id: str
    outcome: Literal["deployment_recovered", "deployment_rolled_back", "release_slipped", "dependency_delivered",
                     "incident_confirmed", "incident_dismissed", "kpi_returned_to_baseline"]
    source_evidence_ref: str
    source_kind: Literal["structured_signal"] = "structured_signal"
    observed_at: datetime
    recorded_at: datetime
    decision_value: str | float | bool | None = None
    supersedes: str | None = None


class ComparisonRecord(FrozenModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    left_id: str
    right_id: str
    kind: Literal["cross_lane", "human", "outcome", "existing_tpllm"]
    state: Literal["agree", "compatible", "partially_compatible", "conflict", "ambiguous", "unresolved"]
    method: str
    explanation: str
    created_at: datetime
