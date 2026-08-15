"""Scenario lattice models (Commit 24).

Organizational futures do not proceed along a single line. Several paths
may reach the same outcome — dependency delivered, or delayed then
worked around, or delayed then scope-reduced — so states can have
multiple parents. That makes this a *lattice*, not a tree.

Probability rules encoded here:
- ``conditional_probability`` (how likely) and ``probability_confidence``
  (how sure we are of that estimate) are separate numbers. P=0.75 with
  confidence 0.30 is a valid, honest statement.
- Probability and impact are separate: a 4% catastrophic path can matter
  more than a 60% inconvenience.
- Pruned branches are never silently dropped — their mass is preserved
  in ScenarioTailMass so displayed paths never falsely sum to 100%.
- The method is honestly labeled; heuristic weights are not called
  learned probabilities.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ScenarioProbabilityMethod(str, Enum):
    HEURISTIC_V1 = "heuristic_v1"
    HISTORICALLY_CALIBRATED = "historically_calibrated"
    LEARNED_MODEL = "learned_model"


class ScenarioEventType(str, Enum):
    DEPENDENCY_DELIVERED = "dependency_delivered"
    DEPENDENCY_DELAYED = "dependency_delayed"
    DEPENDENCY_FAILED = "dependency_failed"
    DECISION_MADE = "decision_made"
    DECISION_DELAYED = "decision_delayed"
    DECISION_REOPENED = "decision_reopened"
    KPI_IMPROVES = "kpi_improves"
    KPI_DETERIORATES = "kpi_deteriorates"
    KPI_STAYS_FLAT = "kpi_stays_flat"
    INTEGRATION_PASSES = "integration_passes"
    INTEGRATION_FAILS = "integration_fails"
    SCOPE_EXPANDS = "scope_expands"
    SCOPE_REDUCES = "scope_reduces"
    RESOURCE_CAPACITY_IMPROVES = "resource_capacity_improves"
    RESOURCE_CAPACITY_CONSTRAINED = "resource_capacity_constrained"
    COMMUNICATION_SUCCEEDS = "communication_succeeds"
    MISCOMMUNICATION_OCCURS = "miscommunication_occurs"
    ASSUMPTION_VALIDATED = "assumption_validated"
    ASSUMPTION_INVALIDATED = "assumption_invalidated"
    WORKAROUND_ADOPTED = "workaround_adopted"
    RECOVERY_ACTION_SUCCEEDS = "recovery_action_succeeds"
    RECOVERY_ACTION_FAILS = "recovery_action_fails"
    EXTERNAL_COMMITMENT_CHANGES = "external_commitment_changes"
    PRIORITY_CHANGE = "priority_change"
    INCIDENT_OCCURS = "incident_occurs"
    MILESTONE_MET = "milestone_met"
    MILESTONE_SLIPS = "milestone_slips"


class ScenarioFactorType(str, Enum):
    HISTORICAL_BASE_RATE = "historical_base_rate"
    CURRENT_STRUCTURAL_SIGNAL = "current_structural_signal"
    KPI_SIGNAL = "kpi_signal"
    DECISION_AGE = "decision_age"
    DEPENDENCY_STATE = "dependency_state"
    CAPACITY_CONSTRAINT = "capacity_constraint"
    COMMITMENT_PRESSURE = "commitment_pressure"
    TECHNICAL_UNCERTAINTY = "technical_uncertainty"
    COMMUNICATION_RISK = "communication_risk"
    PROCEDURAL_MEMORY = "procedural_memory"
    RECOVERY_CAPABILITY = "recovery_capability"
    EXTERNAL_EVENT = "external_event"


class ScenarioDirection(str, Enum):
    """The six reasoning directions every material scenario is evaluated
    in — an explicit invariant, not an aspiration."""
    FORWARD = "forward"
    BACKWARD = "backward"
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    LATERAL = "lateral"
    RECOVERY = "recovery"


def build_state_signature(parts: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in sorted(parts):
        value = parts[key]
        if isinstance(value, dict):
            inner = ",".join(f"{k}:{value[k]}" for k in sorted(value))
            pieces.append(f"{key}={{{inner}}}")
        elif isinstance(value, (list, set, tuple)):
            pieces.append(f"{key}=[{','.join(sorted(str(v) for v in value))}]")
        else:
            pieces.append(f"{key}={value}")
    return hashlib.sha256("|".join(pieces).encode()).hexdigest()[:20]


class ScenarioState(BaseModel):
    """A possible future state. Multiple parents are allowed so distinct
    paths can converge on the same future."""
    id: str
    project_id: str
    horizon_step: int
    simulated_at: datetime
    parent_state_ids: list[str] = Field(default_factory=list)
    workstream_health: dict[str, str] = Field(default_factory=dict)
    kpi_states: dict[str, str] = Field(default_factory=dict)
    dependency_states: dict[str, str] = Field(default_factory=dict)
    decision_states: dict[str, str] = Field(default_factory=dict)
    active_blockers: list[str] = Field(default_factory=list)
    unresolved_unknowns: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence_root_ids: list[str] = Field(default_factory=list)
    outcome_labels: list[str] = Field(default_factory=list)
    state_signature: str = ""


class ScenarioProbabilityFactor(BaseModel):
    """One signal moving a transition's probability.

    ``independent_group`` prevents correlated evidence from compounding:
    three Slack messages describing the same integration failure share a
    group and are counted once, not cubed.
    """
    id: str
    factor_type: ScenarioFactorType
    description: str
    direction: str  # "increases" | "decreases"
    magnitude: float
    confidence: float
    source_ids: list[str] = Field(default_factory=list)
    independent_group: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ScenarioProbabilityFactor":
        if not (0.0 <= self.magnitude <= 1.0):
            raise ValueError("Factor magnitude must be within [0, 1].")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Factor confidence must be within [0, 1].")
        if self.direction not in ("increases", "decreases"):
            raise ValueError(
                "Factor direction must be 'increases' or 'decreases'."
            )
        return self


class ScenarioTransition(BaseModel):
    id: str
    from_state_id: str
    to_state_id: str
    event_type: ScenarioEventType
    event_description: str
    conditional_probability: float
    probability_confidence: float
    factor_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    controllable: bool = False
    possible_intervention_ids: list[str] = Field(default_factory=list)
    impact: float = 0.0

    @model_validator(mode="after")
    def _check(self) -> "ScenarioTransition":
        if not (0.0 <= self.conditional_probability <= 1.0):
            raise ValueError(
                "conditional_probability must be within [0, 1]."
            )
        if not (0.0 <= self.probability_confidence <= 1.0):
            raise ValueError(
                "probability_confidence must be within [0, 1] — it is a "
                "separate quantity from the probability itself."
            )
        return self


class ScenarioTailMass(BaseModel):
    """Probability mass removed by pruning. Displayed branches must never
    look like they sum to 100% when mass was pruned."""
    parent_state_id: str
    omitted_probability_mass: float
    omitted_branch_count: int
    explanation: str


class ScenarioHorizon(BaseModel):
    start_at: datetime
    end_at: datetime
    step_days: int = 1
    maximum_depth: int = 8
    maximum_active_states: int = 100
    minimum_branch_probability: float = 0.02


class ScenarioEventCandidate(BaseModel):
    id: str
    event_type: ScenarioEventType
    description: str
    earliest_at: datetime | None = None
    latest_at: datetime | None = None
    prerequisite_state_conditions: list[str] = Field(default_factory=list)
    resulting_state_changes: dict[str, Any] = Field(default_factory=dict)
    factor_ids: list[str] = Field(default_factory=list)
    controllable: bool = False
    intervention_ids: list[str] = Field(default_factory=list)
    base_probability: float = 0.5
    impact: float = 0.0
    outcome_labels: list[str] = Field(default_factory=list)
    mutually_exclusive_group: str | None = None


class ScenarioLattice(BaseModel):
    id: str
    project_id: str
    root_state_id: str
    states: list[ScenarioState] = Field(default_factory=list)
    transitions: list[ScenarioTransition] = Field(default_factory=list)
    tail_masses: list[ScenarioTailMass] = Field(default_factory=list)
    factors: list[ScenarioProbabilityFactor] = Field(default_factory=list)
    method: ScenarioProbabilityMethod = (
        ScenarioProbabilityMethod.HEURISTIC_V1
    )
    generated_at: datetime
    horizon: ScenarioHorizon
    evidence_cutoff_at: datetime | None = None

    def state(self, state_id: str) -> ScenarioState | None:
        return next((s for s in self.states if s.id == state_id), None)

    def transition(self, transition_id: str) -> ScenarioTransition | None:
        return next(
            (t for t in self.transitions if t.id == transition_id), None
        )

    def outgoing(self, state_id: str) -> list[ScenarioTransition]:
        return [t for t in self.transitions if t.from_state_id == state_id]

    def incoming(self, state_id: str) -> list[ScenarioTransition]:
        return [t for t in self.transitions if t.to_state_id == state_id]


class ScenarioPath(BaseModel):
    id: str
    state_ids: list[str] = Field(default_factory=list)
    transition_ids: list[str] = Field(default_factory=list)
    path_probability: float
    probability_confidence: float
    outcome_summary: str = ""
    milestone_outcomes: dict[str, str] = Field(default_factory=dict)
    kpi_outcomes: dict[str, str] = Field(default_factory=dict)
    major_pitfalls: list[str] = Field(default_factory=list)
    recovery_opportunities: list[str] = Field(default_factory=list)
    intervention_points: list[str] = Field(default_factory=list)
    outcome_labels: list[str] = Field(default_factory=list)


class ScenarioRiskSummary(BaseModel):
    """Ranks paths by more than probability, so a rare catastrophe is not
    buried beneath a likely inconvenience."""
    path_id: str
    probability: float
    impact: float
    expected_loss: float
    recoverability: float
    detectability: float
    time_to_intervene: float
    priority: float


class ScenarioSensitivity(BaseModel):
    factor_id: str
    baseline_outcome_probability: float
    perturbed_outcome_probability: float
    absolute_delta: float
    explanation: str


class InformationValueEstimate(BaseModel):
    unknown_id: str
    current_uncertainty: float
    expected_probability_reduction: float
    affected_outcomes: list[str] = Field(default_factory=list)
    priority: float
    explanation: str = ""


class ScenarioCoverageAssessment(BaseModel):
    """A forecast may not claim comprehensive coverage when it explored
    only the happy path and the direct failure path."""
    forward_coverage: float
    backward_coverage: float
    upstream_coverage: float
    downstream_coverage: float
    lateral_coverage: float
    recovery_coverage: float
    overall_coverage: float
    missing_scenario_classes: list[str] = Field(default_factory=list)
    explanation: str = ""
