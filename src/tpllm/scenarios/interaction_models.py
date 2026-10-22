"""Organizational interaction models.

Many project failures are not technical. They happen because teams
believe different things, a conclusive decision never propagates, a
request reaches the wrong authority, or an acronym means two things.

These are modeled as *structural* interaction risks. This module counts
handoff depth, divergence between team belief states, and propagation
coverage — never message volume, responsiveness, or anything resembling
an assessment of a person.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class MiscommunicationType(str, Enum):
    STALE_DECISION_PROPAGATION = "stale_decision_propagation"
    CONFLICTING_TEAM_ASSUMPTIONS = "conflicting_team_assumptions"
    AUTHORITY_CONFUSION = "authority_confusion"
    OWNERSHIP_CONFUSION = "ownership_confusion"
    TERMINOLOGY_AMBIGUITY = "terminology_ambiguity"
    KPI_DEFINITION_MISMATCH = "kpi_definition_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    LOST_CONTEXT = "lost_context"
    PARTIAL_INFORMATION = "partial_information"
    UNACKNOWLEDGED_DEPENDENCY = "unacknowledged_dependency"
    ASYNC_TIMING_GAP = "async_timing_gap"
    OUTDATED_DOCUMENT = "outdated_document"
    DUPLICATE_DECISION_PATH = "duplicate_decision_path"


class OrganizationalInteractionEdge(BaseModel):
    """Communication *structure* between two organizational entities.

    Deliberately carries no message counts and no per-person metrics.
    """
    id: str
    source_entity_id: str
    target_entity_id: str
    interaction_type: str
    topic_ids: list[str] = Field(default_factory=list)
    last_material_exchange_at: datetime | None = None
    shared_context_ids: list[str] = Field(default_factory=list)
    unresolved_contradiction_ids: list[str] = Field(default_factory=list)
    open_request_ids: list[str] = Field(default_factory=list)
    handoff_depth: int = 0
    evidence_ids: list[str] = Field(default_factory=list)


class MiscommunicationRisk(BaseModel):
    id: str
    risk_type: MiscommunicationType
    affected_entity_ids: list[str] = Field(default_factory=list)
    topic: str
    probability: float
    probability_confidence: float
    impact: float
    triggering_conditions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    mitigation_options: list[str] = Field(default_factory=list)
    explanation: str = ""

    @model_validator(mode="after")
    def _check(self) -> "MiscommunicationRisk":
        for name in ("probability", "probability_confidence", "impact"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1].")
        return self


class TeamBeliefState(BaseModel):
    """What one team currently believes about one topic."""
    team_id: str
    topic_id: str
    active_claim_ids: list[str] = Field(default_factory=list)
    assumed_decision_id: str | None = None
    as_of: datetime


class DecisionPropagationAssessment(BaseModel):
    """Comprehension is never inferred from delivery.

    An entity counts as having current context only when evidence shows
    the current decision present in its downstream state — not because a
    message was sent to it.
    """
    decision_id: str
    expected_recipient_entity_ids: list[str] = Field(default_factory=list)
    confirmed_context_entity_ids: list[str] = Field(default_factory=list)
    stale_context_entity_ids: list[str] = Field(default_factory=list)
    unknown_context_entity_ids: list[str] = Field(default_factory=list)
    propagation_coverage: float = 0.0
    explanation: str = ""


class InteractionEffect(BaseModel):
    """Compounding risk. Factors are not assumed independent — a
    capacity-constrained approver plus a lost-context request plus
    unclear authority delays far more than any one alone."""
    factor_ids: list[str] = Field(default_factory=list)
    effect_type: str
    multiplier: float
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float

    @model_validator(mode="after")
    def _check(self) -> "InteractionEffect":
        if self.multiplier <= 0:
            raise ValueError("Interaction multiplier must be positive.")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within [0, 1].")
        return self


class CoordinationPreventiveAction(BaseModel):
    """A cheaper intervention than a meeting, wherever one will do."""
    id: str
    risk_id: str
    action_type: str
    description: str
    expected_probability_reduction: float
    expected_cost: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    requires_human_attention: bool = False

    @model_validator(mode="after")
    def _check(self) -> "CoordinationPreventiveAction":
        if not (0.0 <= self.expected_probability_reduction <= 1.0):
            raise ValueError(
                "expected_probability_reduction must be within [0, 1]."
            )
        return self
