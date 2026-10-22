"""Fixtures driving the real engines behind the live runtime.

Deliberately uses the *real* engines, not test doubles. The whole point
here is that the orchestrator drives real project understanding,
temporal reconstruction, the scenario lattice and portfolio
simulation — so a fixture that
stubbed those out would prove nothing.

The two Protocols with no shipping implementation (claim extraction and
scenario candidate generation) get deterministic providers here, because
production wants LLM-backed ones and a reproducibility test wants the
same answer every run.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tpllm.intelligence import (
    EvidenceSourceType,
    KPIDefinition,
    KPIIndicatorRole,
    KPIObservation,
    KPITarget,
    KPITargetOperator,
    KPIType,
    LaunchDefinition,
    UnderstandingClaim,
    UnderstandingClaimStatus,
    UnderstandingClaimType,
)
from tpllm.persistence import JournalRetentionClass
from tpllm.portfolio import (
    CapacityResourceType,
    OrganizationalCapacityWindow,
    PortfolioCapacityDemand,
    SharedScenarioFactor,
)
from tpllm.runtime import RuntimeStateStore as RuntimeProjection
from tpllm.runtime.services import LiveIntelligenceServices
from tpllm.runtime.sources import DomainInputs, JournalBackedDataSource
from tpllm.scenarios import ScenarioEventCandidate, ScenarioEventType as E
from tpllm.scenarios.models import ScenarioHorizon
from tests._persistence_fixtures import (  # noqa: F401 — re-exported
    ORGANIZATION,
    PLATFORM,
    PROJECT_A,
    PROJECT_B,
    PROJECT_C,
    PROJECT_D,
    T0,
    Persistence,
    at,
    capacity_incident_envelope,
    envelope,
    manifest,
    runtime_policy,
)

FRIDAY = T0 + timedelta(days=5)


# ── Deterministic providers for the two injected Protocols ──────────


class KeywordClaimExtractor:
    """Maps evidence content to understanding claims by keyword.

    Deterministic by construction: the same evidence yields the same
    claims, which is what lets the replay-equivalence test mean
    something.
    """

    RULES: tuple[tuple[str, UnderstandingClaimType], ...] = (
        ("incident response", UnderstandingClaimType.FAILURE_MODE),
        ("review capacity", UnderstandingClaimType.OPERATIONAL_REQUIREMENT),
        ("approval", UnderstandingClaimType.DEPENDENCY),
        ("activation", UnderstandingClaimType.SUCCESS_CRITERION),
        ("integration", UnderstandingClaimType.TECHNICAL_MECHANISM),
    )

    def extract_claims(self, *, project_id, evidence, as_of):
        claims: list[UnderstandingClaim] = []
        for item in sorted(evidence, key=lambda e: e.id):
            lowered = (item.content or "").lower()
            for keyword, claim_type in self.RULES:
                if keyword not in lowered:
                    continue
                claims.append(
                    UnderstandingClaim(
                        id=f"claim-{claim_type.value}-{item.id}",
                        project_id=project_id,
                        claim_type=claim_type,
                        statement=(
                            f"{keyword.capitalize()} noted: "
                            f"{(item.content or '')[:80]}"
                        ),
                        status=UnderstandingClaimStatus.SUPPORTED,
                        evidence_ids=[item.id],
                        confidence=0.7,
                        first_observed_at=item.source.observed_at,
                        last_supported_at=item.source.observed_at,
                    )
                )
        return claims


class CapacityScenarioGenerator:
    """Deterministic candidates for a constrained-approval future.

    Two mutually exclusive outcomes so the lattice has real probability
    structure to report, and both carry a confidence separate from their
    probability.
    """

    def generate_events(self, *, temporal_state, horizon, context):
        return [
            ScenarioEventCandidate(
                id="cand-approved",
                event_type=E.DECISION_MADE,
                description="Platform grants the remaining review slot.",
                resulting_state_changes={
                    "decision_states": {"dec-platform": "approved"},
                },
                base_probability=0.55,
                impact=0.2,
                outcome_labels=["integration_on_time"],
                mutually_exclusive_group="platform-review",
            ),
            ScenarioEventCandidate(
                id="cand-slipped",
                event_type=E.DECISION_DELAYED,
                description="Platform review slips past the window.",
                resulting_state_changes={
                    "decision_states": {"dec-platform": "delayed"},
                },
                base_probability=0.45,
                impact=0.7,
                outcome_labels=["severe_slip"],
                mutually_exclusive_group="platform-review",
            ),
        ]


# ── Declared organizational structure ────────────────────────────────


def capacity_window(
    *, capacity: float = 2.0, confidence: float = 0.6,
) -> OrganizationalCapacityWindow:
    return OrganizationalCapacityWindow(
        resource_id=PLATFORM,
        resource_type=CapacityResourceType.APPROVAL,
        team_id="platform-architecture",
        window_start=T0,
        window_end=FRIDAY,
        estimated_capacity_units=capacity,
        committed_capacity_units=0.0,
        evidence_ids=["ev-approval-queue"],
        confidence=confidence,
    )


def demand(
    *,
    demand_id: str,
    project_id: str,
    criticality: float,
    has_workaround: bool = False,
    workaround: str | None = None,
    alternatives: list[str] | None = None,
) -> PortfolioCapacityDemand:
    return PortfolioCapacityDemand(
        id=demand_id,
        project_id=project_id,
        workstream_id=f"ws-{project_id}",
        resource_type=CapacityResourceType.APPROVAL,
        required_resource_id=PLATFORM,
        requested_units=1.0,
        earliest_needed_at=T0,
        latest_needed_at=FRIDAY,
        criticality=criticality,
        alternative_resource_ids=list(alternatives or []),
        has_workaround=has_workaround,
        workaround_description=workaround,
        evidence_ids=[f"ev-{demand_id}"],
    )


def approval_demands() -> list[PortfolioCapacityDemand]:
    """A has a workaround, B has a deputy, C has nowhere to go."""
    return [
        demand(
            demand_id="dem-a", project_id=PROJECT_A, criticality=0.95,
            has_workaround=True, workaround="Partial enterprise rollout",
        ),
        demand(
            demand_id="dem-b", project_id=PROJECT_B, criticality=0.70,
            has_workaround=True, workaround="Continue on contract v2",
            alternatives=["Platform Deputy"],
        ),
        demand(
            demand_id="dem-c", project_id=PROJECT_C, criticality=0.90,
        ),
    ]


def shared_factors() -> list[SharedScenarioFactor]:
    return [
        SharedScenarioFactor(
            id="factor-platform-saturated",
            factor_type="capacity_constraint",
            description="Platform queue saturates before Friday",
            affected_project_ids=[PROJECT_A, PROJECT_B, PROJECT_C],
            affected_resource_ids=[PLATFORM],
            probability=0.40,
            probability_confidence=0.5,
            evidence_ids=["ev-approval-queue"],
        ),
    ]


def launch_definition(project_id: str = PROJECT_C) -> LaunchDefinition:
    return LaunchDefinition(
        id=f"launch-{project_id}",
        project_id=project_id,
        launch_name=f"{project_id} launch",
        target_date=FRIDAY,
        forecast_horizon_start=T0,
        required_scope=["identity reliability integration"],
        success_criteria=["activation rate at or above 60 percent"],
        business_criticality="high",
        evidence_ids=["ev-launch-def"],
    )


def kpi_definition() -> KPIDefinition:
    return KPIDefinition(
        id="kpi-activation-rate",
        project_id=PROJECT_A,
        canonical_name="Activation rate",
        kpi_type=KPIType.PRODUCT,
        indicator_role=KPIIndicatorRole.LAGGING,
        unit="percent",
        description="Share of provisioned accounts that activate",
        why_it_matters="Activation is the launch's stated success criterion",
        source_systems=["metrics"],
        evidence_ids=["ev-kpi-def"],
        confidence=0.8,
    )


def kpi_target() -> KPITarget:
    return KPITarget(
        id="target-activation",
        kpi_id="kpi-activation-rate",
        operator=KPITargetOperator.GREATER_THAN_OR_EQUAL,
        target_value=60.0,
        effective_from=T0 - timedelta(days=30),
        rationale="Launch success criterion",
        evidence_ids=["ev-kpi-target"],
        confidence=0.8,
    )


def kpi_observations() -> list[KPIObservation]:
    """A breach, so the KPI artifact has something real to report."""
    return [
        KPIObservation(
            id="obs-1",
            kpi_id="kpi-activation-rate",
            value=41.0,
            unit="percent",
            observed_at=T0 - timedelta(hours=1),
            reported_at=T0 - timedelta(hours=1),
            source_system="metrics",
            source_id="metrics:activation",
            evidence_ids=["ev-kpi-1"],
            confidence=0.8,
        ),
    ]


def domain_inputs(**overrides) -> DomainInputs:
    defaults: dict[str, Any] = dict(
        capacity_windows=[capacity_window()],
        capacity_demands=approval_demands(),
        shared_factors=shared_factors(),
        launch_definitions={
            project_id: launch_definition(project_id)
            for project_id in (PROJECT_A, PROJECT_B, PROJECT_C)
        },
        approval_requirements={
            PROJECT_A: [PLATFORM], PROJECT_B: [PLATFORM],
            PROJECT_C: [PLATFORM], PROJECT_D: ["Partner Ops"],
        },
        service_usage={
            PROJECT_A: ["Account Graph"], PROJECT_C: ["Account Graph"],
        },
    )
    defaults.update(overrides)
    return DomainInputs(**defaults)


def scenario_horizon() -> ScenarioHorizon:
    return ScenarioHorizon(
        start_at=T0, end_at=FRIDAY, maximum_depth=3,
        minimum_branch_probability=0.02,
    )


# ── Wiring ───────────────────────────────────────────────────────────


def data_source(
    *, persistence: Persistence, inputs: DomainInputs | None = None,
) -> JournalBackedDataSource:
    return JournalBackedDataSource(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        inputs=inputs or domain_inputs(),
    )


def live_services(
    *,
    persistence: Persistence,
    inputs: DomainInputs | None = None,
    as_of: datetime | None = None,
    **overrides,
) -> LiveIntelligenceServices:
    """The production services object, wired to real engines."""
    defaults: dict[str, Any] = dict(
        data_source=data_source(persistence=persistence, inputs=inputs),
        claim_extractor=KeywordClaimExtractor(),
        scenario_generator=CapacityScenarioGenerator(),
        scenario_horizon=scenario_horizon(),
        as_of=as_of or at(60),
        kpi_definitions=[kpi_definition()],
        kpi_observations=kpi_observations(),
        kpi_targets={"kpi-activation-rate": kpi_target()},
    )
    defaults.update(overrides)
    return LiveIntelligenceServices(**defaults)


def evidence_envelopes() -> list:
    """Journalled evidence rich enough for the engines to chew on."""
    return [
        capacity_incident_envelope(),
        envelope(
            source_id="arch-1",
            event_type="architecture_evidence",
            observed_at=at(2),
            project_ids=[PROJECT_C],
            subject_keys=["decision:dec-platform"],
            content=(
                "The identity integration depends on Platform approval of "
                "the contract standard before activation can proceed."
            ),
        ),
        envelope(
            source_id="kpi-1",
            event_type="kpi_observation",
            observed_at=at(3),
            project_ids=[PROJECT_A],
            kpi_ids=["kpi-activation-rate"],
            content="Activation rate measured at 41 percent.",
        ),
    ]


def seeded_organization(
    *, inputs: DomainInputs | None = None,
) -> tuple[Persistence, RuntimeProjection, LiveIntelligenceServices]:
    """Journal the evidence, then run it through the real engines."""
    from tpllm.runtime import process_durable_organizational_events

    persistence = Persistence()
    services = live_services(persistence=persistence, inputs=inputs)
    projection = RuntimeProjection(organization_id=ORGANIZATION)

    durable, projection = process_durable_organizational_events(
        envelopes=evidence_envelopes(),
        organization_id=ORGANIZATION,
        journal=persistence.journal,
        state_store=persistence.state,
        artifact_store=persistence.artifacts,
        runtime_projection=projection,
        intelligence_services=services,
        runtime_policy=runtime_policy(),
        manifest=manifest(),
        now=at(60),
        retention_class=JournalRetentionClass.NORMALIZED,
    )
    return persistence, projection, services
