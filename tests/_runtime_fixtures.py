"""Fixtures for runtime tests.

Models the spec's canonical organization:

    Project A  Enterprise Billing     needs 1 Platform review slot
    Project B  Mobile Migration       needs 1, has a viable workaround
    Project C  Identity Reliability   needs 1, has no workaround
    Project D  Partner Launch         unaffected by Platform

Platform Architecture is the shared approval authority. When its
capacity falls, A, B, and C move together and D must stay untouched —
that pairing is what most of the runtime tests are checking.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lltpm.coordination import (
    OrganizationalAutonomyState,
    OrganizationalDecisionAuthorityPolicy,
    OrganizationalDecisionMode,
    OrganizationalDecisionRisk,
    can_agent_finalize_decision,
    select_agent_recommendation,
)
from lltpm.integrations.envelopes import make_envelope
from lltpm.runtime import (
    ArtifactRecomputeError,
    ArtifactRecomputeRequest,
    DecisionContext,
    IntelligenceArtifactType as A,
    RecomputedArtifact,
    RuntimeEvidenceEvent,
    RuntimePolicy,
    RuntimeStateStore,
    RuntimeTopology,
    ScenarioMaterialityPolicy,
    build_decision_context_fingerprint,
)
from tests._recommendation_fixtures import (
    POLICY as RECOMMENDATION_POLICY,
    assessment,
    capsule,
    criteria,
)
from tests._coordination_fixtures import AS_OF, PROJECT, WS

T0 = datetime(2026, 11, 16, 9, 0, 0)

PLATFORM = "Platform Architecture"
PROJECT_A = "project-a"
PROJECT_B = "project-b"
PROJECT_C = "project-c"
PROJECT_D = "project-d"
ALL_PROJECTS = [PROJECT_A, PROJECT_B, PROJECT_C, PROJECT_D]


def at(minutes: float = 0.0) -> datetime:
    return T0 + timedelta(minutes=minutes)


# ── Envelopes ────────────────────────────────────────────────────────


def envelope(
    *,
    source_id: str,
    event_type: str,
    observed_at: datetime | None = None,
    received_at: datetime | None = None,
    project_ids: list[str] | None = None,
    subject_keys: list[str] | None = None,
    shared_resource_ids: list[str] | None = None,
    kpi_ids: list[str] | None = None,
    workstream_ids: list[str] | None = None,
    decision_ids: list[str] | None = None,
    canonical_entity_ids: list[str] | None = None,
    asserted_state: str | None = None,
    causal_event_id: str | None = None,
    source_system: str = "slack",
    content: str | None = None,
):
    observed_at = observed_at or T0
    metadata: dict[str, object] = {}
    for key, value in (
        ("subject_keys", subject_keys),
        ("shared_resource_ids", shared_resource_ids),
        ("kpi_ids", kpi_ids),
        ("workstream_ids", workstream_ids),
        ("decision_ids", decision_ids),
        ("canonical_entity_ids", canonical_entity_ids),
    ):
        if value:
            metadata[key] = list(value)
    if asserted_state is not None:
        metadata["asserted_state"] = asserted_state
    if causal_event_id is not None:
        metadata["causal_event_id"] = causal_event_id

    return make_envelope(
        source_system=source_system,
        source_type="message",
        source_id=source_id,
        event_type=event_type,
        observed_at=observed_at,
        received_at=received_at or observed_at,
        project_ids=list(project_ids or [PROJECT_A]),
        content=content,
        metadata=metadata,
    )


def kpi_envelope(**overrides):
    defaults = dict(
        source_id="kpi-activation-1",
        event_type="kpi_observation",
        project_ids=[PROJECT_A],
        kpi_ids=["kpi-activation-rate"],
        workstream_ids=["ws-project-a"],
        subject_keys=["kpi:kpi-activation-rate"],
    )
    defaults.update(overrides)
    return envelope(**defaults)


def capacity_incident_envelope(**overrides):
    """The spec's canonical event: Platform capacity 2 -> 1."""
    defaults = dict(
        source_id="platform-incident-1",
        event_type="capacity_changed",
        project_ids=[PROJECT_A, PROJECT_B, PROJECT_C],
        shared_resource_ids=[PLATFORM],
        canonical_entity_ids=["authority_platform_architecture"],
        subject_keys=[f"capacity:{PLATFORM}"],
        asserted_state="capacity=1",
        causal_event_id="causal-platform-incident",
        content=(
            "Platform Architecture entered incident response; review "
            "capacity falls from 2 slots to 1 before Friday."
        ),
    )
    defaults.update(overrides)
    return envelope(**defaults)


def duplicate_chat_envelope(**overrides):
    defaults = dict(
        source_id="chat-noise-1",
        event_type="chat_message",
        project_ids=[PROJECT_A],
        content="+1",
    )
    defaults.update(overrides)
    return envelope(**defaults)


# ── Runtime events ───────────────────────────────────────────────────


def event(
    *,
    envelope_id: str,
    event_class: str,
    occurred_at: datetime | None = None,
    received_at: datetime | None = None,
    project_ids: list[str] | None = None,
    causal_event_id: str = "causal-1",
    **overrides,
) -> RuntimeEvidenceEvent:
    occurred_at = occurred_at or T0
    defaults = dict(
        envelope_id=envelope_id,
        project_ids=list(project_ids or [PROJECT_A]),
        occurred_at=occurred_at,
        received_at=received_at or occurred_at,
        event_class=event_class,
        causal_event_id=causal_event_id,
        root_evidence_ids=[envelope_id],
        idempotency_key=envelope_id,
    )
    defaults.update(overrides)
    return RuntimeEvidenceEvent(**defaults)


# ── Topology and policy ──────────────────────────────────────────────


def topology(**overrides) -> RuntimeTopology:
    defaults = dict(
        projects_by_shared_resource={
            PLATFORM: [PROJECT_A, PROJECT_B, PROJECT_C],
            "Account Graph": [PROJECT_A, PROJECT_C],
        },
        projects_by_kpi={"kpi-activation-rate": [PROJECT_A]},
        projects_by_entity={
            "authority_platform_architecture": [
                PROJECT_A, PROJECT_B, PROJECT_C,
            ],
        },
        high_priority_project_ids=[PROJECT_C],
        all_project_ids=list(ALL_PROJECTS),
    )
    defaults.update(overrides)
    return RuntimeTopology(**defaults)


def runtime_policy(**overrides) -> RuntimePolicy:
    defaults = dict(
        organization_id="acme",
        topology=topology(),
        scenario_materiality=ScenarioMaterialityPolicy(),
        severe_outcome_labels=["severe_slip"],
        severe_outcome_alert_thresholds={"severe_slip": 0.20},
    )
    defaults.update(overrides)
    return RuntimePolicy(**defaults)


def store(**overrides) -> RuntimeStateStore:
    defaults = dict(organization_id="acme")
    defaults.update(overrides)
    return RuntimeStateStore(**defaults)


# ── Fake intelligence services ───────────────────────────────────────


class FakeIntelligenceServices:
    """A deterministic stand-in for the real engines.

    The runtime is a scheduler; these tests are about scheduling,
    coherence, and restraint. Wiring in every real engine would test the
    engines again and make the orchestration invariants harder to see.
    """

    def __init__(
        self,
        *,
        outcomes: dict[A, dict] | None = None,
        failures: dict[A, ArtifactRecomputeError] | None = None,
        unchanged: set[A] | None = None,
    ):
        self.outcomes = dict(outcomes or {})
        self.failures = dict(failures or {})
        self.unchanged = set(unchanged or set())
        self.calls: list[tuple[A, tuple[str, ...]]] = []

    def recompute_artifact(
        self,
        *,
        request: ArtifactRecomputeRequest,
        revision_id: str,
    ) -> RecomputedArtifact:
        artifact_type = request.artifact_type
        self.calls.append(
            (artifact_type, tuple(sorted(request.scope.project_ids)))
        )
        failure = self.failures.get(artifact_type)
        if failure is not None:
            raise failure

        overrides = dict(self.outcomes.get(artifact_type, {}))
        project_ids = overrides.pop(
            "project_ids", sorted(request.scope.project_ids),
        )
        defaults = dict(
            artifact_type=artifact_type,
            artifact_id=f"{artifact_type.value}-{'-'.join(project_ids) or 'org'}",
            revision_id=revision_id,
            state_fingerprint=f"fp-{artifact_type.value}-{revision_id}",
            project_ids=project_ids,
            changed=artifact_type not in self.unchanged,
            after_summary=f"{artifact_type.value} refreshed.",
            evidence_ids=list(request.source_event_ids),
        )
        defaults.update(overrides)
        return RecomputedArtifact(**defaults)

    def called_types(self) -> list[A]:
        return [artifact_type for artifact_type, _ in self.calls]

    def call_count(self, artifact_type: A) -> int:
        return sum(1 for t, _ in self.calls if t == artifact_type)

    def projects_seen(self) -> set[str]:
        return {p for _, projects in self.calls for p in projects}


def scenario_outcome(
    *,
    before: float,
    after: float,
    confidence: float = 0.8,
    label: str = "severe_slip",
    project_ids: list[str] | None = None,
) -> dict:
    """Programme a scenario artifact with a specific probability shift."""
    return {
        "previous_outcome_probabilities": {label: before},
        "outcome_probabilities": {label: after},
        "probability_confidence": confidence,
        "after_summary": (
            f"P({label}) {before:.0%} → {after:.0%}."
        ),
        "before_summary": f"P({label}) {before:.0%}.",
        "project_ids": project_ids or [PROJECT_A],
    }


# ── Decision context and the autonomy race ───────────────────────────


def decision_context(**overrides) -> DecisionContext:
    defaults = dict(
        decision_id="dec-contract-standard",
        project_id=PROJECT,
        critical_kpi_states={"kpi-activation-rate": "healthy"},
        hard_constraints=["must ship before the enterprise renewal"],
        option_ids=["opt-a", "opt-b"],
        decision_authority_id="person_priya_rao",
        critical_unknown_ids=[],
        contradiction_ids=[],
        commitment_ids=["commit-renewal"],
        scope_summary="global v3 rollout",
        scenario_outcome_probabilities={"severe_slip": 0.12},
    )
    defaults.update(overrides)
    return DecisionContext(**defaults)


def context_fingerprint(context: DecisionContext) -> str:
    return build_decision_context_fingerprint(context)


def eligible_recommendation(caps=None):
    """A high-confidence recommendation the authority policy accepts."""
    return select_agent_recommendation(
        capsule=caps or capsule(),
        assessments=[
            assessment(
                "opt-a", {f"crit-{i}": 0.98 for i in range(3)},
                evidence_confidence=0.98,
            ),
            assessment("opt-b", {f"crit-{i}": 0.30 for i in range(3)}),
        ],
        criteria=criteria(),
        policy=RECOMMENDATION_POLICY,
        critical_unknowns=[],
        now=AS_OF,
    )


def authority_policy(**overrides) -> OrganizationalDecisionAuthorityPolicy:
    defaults = dict(
        id="policy-acme-v1",
        organization_id="acme",
        mode=OrganizationalDecisionMode.BOUNDED_AUTONOMOUS,
        autonomous_allowed_decision_types=["internal_technical"],
        autonomous_allowed_project_ids=[PROJECT],
        autonomous_allowed_workstream_ids=[WS],
        maximum_autonomous_risk=OrganizationalDecisionRisk.MODERATE,
        created_at=AS_OF - timedelta(days=1),
        expires_at=AS_OF + timedelta(days=30),
    )
    defaults.update(overrides)
    return OrganizationalDecisionAuthorityPolicy(**defaults)


def autonomy_state(**overrides) -> OrganizationalAutonomyState:
    defaults = dict(enabled=True, kill_switch_engaged=False, updated_at=AS_OF)
    defaults.update(overrides)
    return OrganizationalAutonomyState(**defaults)


def baseline_authority_assessment(recommendation=None, caps=None):
    caps = caps or capsule()
    return can_agent_finalize_decision(
        recommendation=recommendation or eligible_recommendation(caps),
        capsule=caps,
        policy=authority_policy(),
        decision_risk=OrganizationalDecisionRisk.LOW,
        recent_autonomous_decisions=[],
        now=AS_OF,
        decision_type="internal_technical",
    )


class FakeScenarioRecommendation:
    """Minimal stand-in matching what the scenario gate reads."""

    def __init__(
        self,
        *,
        changed_by_scenario_analysis: bool = False,
        scenario_probability_confidence: float = 0.82,
        recommended_action: object = None,
    ):
        from lltpm.coordination.decision_timing import DecisionTimingAction

        class _Timing:
            def __init__(self, action):
                self.recommended_action = action

        self.recommendation_changed_by_scenario_analysis = (
            changed_by_scenario_analysis
        )
        self.scenario_probability_confidence = (
            scenario_probability_confidence
        )
        self.timing = _Timing(
            recommended_action or DecisionTimingAction.DECIDE_NOW
        )
