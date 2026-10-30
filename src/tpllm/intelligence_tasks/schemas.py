"""TPLLM-owned v1 contracts. Material semantic changes require new classes/IDs."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=2000)]
Ref = Annotated[str, Field(min_length=1, max_length=240)]
Texts = Annotated[list[Text], Field(max_length=12)]
Refs = Annotated[list[Ref], Field(max_length=40)]
Family = Literal["schedule", "dependency", "security", "reliability", "staffing", "cost", "scope", "compliance", "quality", "unknown"]
Cause = Literal["schema_incompatibility", "configuration_mismatch", "dependency_delay", "capacity_reduction",
                "test_outage", "transient_noise", "security_exposure", "cost_growth", "schedule_change", "recovered", "stale_status", "scope_change", "unknown"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class GroundedArtifact(Contract):
    completion: Literal["complete", "partial", "insufficient_evidence"]
    supporting_evidence_refs: Refs
    contradictory_evidence_refs: Refs
    entity_refs: Refs
    ontology_refs: Refs
    task_refs: Refs
    unknowns: Texts


class RootCauseV1(GroundedArtifact):
    primary_hypothesis: Text
    alternative_hypotheses: Texts
    cause_tags: Annotated[list[Cause], Field(min_length=1, max_length=8)]
    operational_interpretation: Annotated[list[Family], Field(min_length=1, max_length=8)]
    recommended_next_evidence: Texts


class OperationalSummaryV1(GroundedArtifact):
    situation: Text
    material_changes: Texts
    dependencies: Texts
    risks_to_review: Texts


class DependencyPath(Contract):
    entity_refs: Refs
    evidence_refs: Refs
    relationship: Literal["depends_on", "blocks", "references", "hypothesized"]
    explanation: Text


class DependencyAnalysisV1(GroundedArtifact):
    affected_entities: Refs
    dependency_paths: Annotated[list[DependencyPath], Field(max_length=12)]
    likely_bottlenecks: Texts
    downstream_impacts: Texts


class RecoveryOption(Contract):
    description: Text
    expected_benefit: Text
    tradeoffs: Texts
    dependencies: Texts
    risks: Texts
    required_authority: Literal["human_review", "existing_scoped_delegation", "not_established"]
    evidence_refs: Refs


class RecoveryOptionsV1(GroundedArtifact):
    options: Annotated[list[RecoveryOption], Field(max_length=8)]


class InsightExplanationV1(GroundedArtifact):
    insight_ref: Ref
    summary: Text
    why_it_matters: Text
    uncertainties: Texts
    next_questions: Texts


SCHEMAS = {
    "reasoning.root_cause_analysis.v1": RootCauseV1,
    "reasoning.operational_summary.v1": OperationalSummaryV1,
    "reasoning.dependency_analysis.v1": DependencyAnalysisV1,
    "reasoning.recovery_options.v1": RecoveryOptionsV1,
    "reasoning.insight_explanation.v1": InsightExplanationV1,
}
