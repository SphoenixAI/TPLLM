"""Fixtures for Commit 31 persistence tests.

Builds the synthetic organization the acceptance test uses: four
projects, one shared Platform bottleneck, KPI observations, an agent
recommendation, a human-modified decision, an autonomous decision, a
reopened decision, and a recorded outcome — enough durable history that
"delete everything derived and replay" is a real claim rather than a
trivial one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from lltpm.persistence import (
    ExecutionContext,
    ExecutionMode,
    InMemoryEventJournalStore,
    InMemoryIntelligenceArtifactStore,
    InMemoryRuntimeStateStore,
    InMemorySnapshotStore,
    JournalEventType,
    JournalRecord,
    ReplayClock,
    ReproducibilityManifest,
    SourceAvailability,
    build_journal_record,
    build_manifest,
    build_runtime_snapshot,
    organization_stream,
)
from lltpm.runtime import (
    IntelligenceArtifactType as A,
    RuntimeJournalReconstructor,
    RuntimeStateStore as RuntimeProjection,
    append_agent_recommendation,
    append_authority_policy_changed,
    append_autonomous_decision,
    append_kill_switch_changed,
    append_organizational_decision,
    append_outcome,
    append_source_availability_changed,
    process_durable_organizational_events,
)
from tests._runtime_fixtures import (  # noqa: F401 — re-exported
    ALL_PROJECTS,
    PLATFORM,
    PROJECT_A,
    PROJECT_B,
    PROJECT_C,
    PROJECT_D,
    T0,
    FakeIntelligenceServices,
    at,
    capacity_incident_envelope,
    duplicate_chat_envelope,
    envelope,
    kpi_envelope,
    runtime_policy,
    scenario_outcome,
)

ORGANIZATION = "acme"
POLICY_FINGERPRINT = "policy-fp-v1"


def manifest(**overrides) -> ReproducibilityManifest:
    defaults: dict[str, Any] = dict(policy_fingerprint=POLICY_FINGERPRINT)
    defaults.update(overrides)
    return build_manifest(**defaults)


def services(**overrides) -> FakeIntelligenceServices:
    """Deterministic engines, so replay equivalence means something."""
    defaults = {
        A.SCENARIO_LATTICE: scenario_outcome(
            before=0.19, after=0.49,
            project_ids=[PROJECT_A, PROJECT_B, PROJECT_C],
        ),
        A.PORTFOLIO_RECOMMENDATION: {
            "decision_required": True,
            "after_summary": "Prioritize project-c.",
        },
    }
    defaults.update(overrides)
    return FakeIntelligenceServices(outcomes=defaults)


class Persistence:
    """One bundle of the four stores, in-memory."""

    def __init__(self) -> None:
        self.journal = InMemoryEventJournalStore()
        self.state = InMemoryRuntimeStateStore()
        self.artifacts = InMemoryIntelligenceArtifactStore()
        self.snapshots = InMemorySnapshotStore()

    def delete_all_derived_state(self) -> None:
        """The acceptance-test move: keep only the journal."""
        self.state.clear_projections(ORGANIZATION)
        self.artifacts.clear()
        self.snapshots.clear()


def reconstructor(**overrides) -> RuntimeJournalReconstructor:
    return RuntimeJournalReconstructor(
        intelligence_services=overrides.pop("intelligence_services", None)
        or services(),
        runtime_policy=overrides.pop("runtime_policy", None)
        or runtime_policy(),
    )


def replay_context(*, replay_as_of: datetime | None = None) -> ExecutionContext:
    return ExecutionContext(
        mode=ExecutionMode.REPLAY,
        clock=ReplayClock(replay_as_of=replay_as_of or at(120)),
        suppress_side_effects=True,
    )


def record(
    *,
    event_type: JournalEventType = JournalEventType.EVIDENCE_ACCEPTED,
    stream_id: str | None = None,
    occurred_at: datetime | None = None,
    recorded_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
    source_key: str = "src-1",
    **overrides,
) -> JournalRecord:
    occurred_at = occurred_at or T0
    return build_journal_record(
        event_type=event_type,
        stream_id=stream_id or organization_stream(ORGANIZATION),
        occurred_at=occurred_at,
        recorded_at=recorded_at or occurred_at,
        payload=payload if payload is not None else {"note": "test"},
        organization_id=overrides.pop("organization_id", ORGANIZATION),
        source_key=source_key,
        **overrides,
    )


class FakeOutboundPort:
    """Counts every external call. Zero is the expected number in replay."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send(
        self,
        *,
        target_system: str,
        action_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        context: Any = None,
    ):
        from lltpm.persistence import DurableOutboundIntent

        self.calls.append((target_system, action_type))
        return DurableOutboundIntent(
            id=f"intent-{len(self.calls)}",
            originating_journal_record_id="",
            target_system=target_system,
            action_type=action_type,
            payload_hash="",
            idempotency_key=idempotency_key,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FakeAutonomousExecutor:
    """Stands in for finalize_agent_decision, counting invocations.

    Replay must restore an autonomous decision as a fact and leave this
    at zero.
    """

    def __init__(self) -> None:
        self.finalizations = 0

    def finalize(self, **_: Any) -> None:
        self.finalizations += 1


def build_live_organization(
    *,
    persistence: Persistence | None = None,
    intelligence_services: FakeIntelligenceServices | None = None,
) -> tuple[Persistence, RuntimeProjection, dict[str, Any]]:
    """Run the synthetic organization live and journal everything.

    Returns the persistence bundle, the final runtime projection, and the
    semantic fingerprints the acceptance test compares against.
    """
    persistence = persistence or Persistence()
    engines = intelligence_services or services()
    projection = RuntimeProjection(organization_id=ORGANIZATION)
    reproducibility = manifest()

    # 1. Policy history: bounded autonomy is turned on.
    append_authority_policy_changed(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        policy_id="policy-acme-v1",
        previous_fingerprint=None,
        new_fingerprint=POLICY_FINGERPRINT,
        changed_fields=["mode", "maximum_autonomous_risk"],
        actor_id="person_priya_rao",
        effective_at=at(-60),
        recorded_at=at(-60),
    )

    # 2. A KPI observation on one project.
    durable_kpi, projection = process_durable_organizational_events(
        envelopes=[kpi_envelope()],
        organization_id=ORGANIZATION,
        journal=persistence.journal,
        state_store=persistence.state,
        artifact_store=persistence.artifacts,
        runtime_projection=projection,
        intelligence_services=engines,
        runtime_policy=runtime_policy(),
        manifest=reproducibility,
        now=at(5),
    )

    # 3. The shared Platform incident: A, B, and C move together.
    durable_incident, projection = process_durable_organizational_events(
        envelopes=[capacity_incident_envelope()],
        organization_id=ORGANIZATION,
        journal=persistence.journal,
        state_store=persistence.state,
        artifact_store=persistence.artifacts,
        runtime_projection=projection,
        intelligence_services=engines,
        runtime_policy=runtime_policy(),
        manifest=reproducibility,
        now=at(30),
    )

    # 4. What the agent recommended, at the time it recommended it.
    append_agent_recommendation(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        recommendation_id="rec-contract-standard",
        project_id=PROJECT_C,
        recommended_option_id="opt-b",
        confidence=0.84,
        state_fingerprint="ctx-fp-august",
        occurred_at=at(35),
        recorded_at=at(35),
        revision_id=durable_incident.result.revision_id,
        method_version="evidence-weighted-v21",
    )

    # 5. A human modified it before deciding.
    append_organizational_decision(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        decision_id="dec-contract-standard",
        project_id=PROJECT_C,
        conclusion="opt-b-scoped",
        decided_by="person_priya_rao",
        origin="human_modified_agent_recommendation",
        occurred_at=at(40),
        recorded_at=at(40),
    )

    # 6. An autonomous decision on a different project.
    append_autonomous_decision(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        decision_id="decision-auto-rec-rollout",
        project_id=PROJECT_A,
        recommendation_id="rec-rollout",
        policy_id="policy-acme-v1",
        occurred_at=at(45),
        recorded_at=at(45),
        executed=True,
    )

    # 7. A decision reopened — a correction, appended, never an edit.
    append_organizational_decision(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        decision_id="dec-contract-standard",
        project_id=PROJECT_C,
        conclusion=None,
        decided_by="person_priya_rao",
        origin="human_decision",
        occurred_at=at(50),
        recorded_at=at(50),
        event_type=JournalEventType.ORGANIZATIONAL_DECISION_REOPENED,
        payload={"reason": "mobile client compatibility was not considered"},
    )

    # 8. What actually happened.
    append_outcome(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        outcome_id="outcome-integration-1",
        project_id=PROJECT_C,
        decision_id="dec-contract-standard",
        kpi_ids=["kpi-activation-rate"],
        milestone_ids=["milestone-integration"],
        observed_value="integration_delivered_late",
        occurred_at=at(55),
        recorded_at=at(55),
    )

    fingerprints = dict(
        durable_incident.result.metrics and {} or {}
    )
    from lltpm.runtime import collect_state_fingerprints

    fingerprints = collect_state_fingerprints(projection)

    return persistence, projection, {
        "fingerprints": fingerprints,
        "revision_id": durable_incident.result.revision_id,
        "kpi_revision_id": durable_kpi.result.revision_id,
        "manifest": reproducibility,
    }


def make_snapshot(
    *,
    persistence: Persistence,
    projection: RuntimeProjection,
    snapshot_id: str = "snap-1",
    journal_global_sequence: int | None = None,
    created_at: datetime | None = None,
    reproducibility: ReproducibilityManifest | None = None,
):
    from lltpm.runtime import collect_state_fingerprints

    head = persistence.journal.latest_position()
    snapshot = build_runtime_snapshot(
        snapshot_id=snapshot_id,
        organization_id=ORGANIZATION,
        journal_global_sequence=(
            journal_global_sequence
            if journal_global_sequence is not None
            else (head.global_sequence if head else 0)
        ),
        active_revision_id=(
            projection.current_revision.id
            if projection.current_revision else "revision-0"
        ),
        project_states={
            pid: state.model_dump(mode="json")
            for pid, state in projection.project_states.items()
        },
        portfolio_state=(
            projection.portfolio_state.model_dump(mode="json")
            if projection.portfolio_state else None
        ),
        state_fingerprints=collect_state_fingerprints(projection),
        manifest=reproducibility or manifest(),
        created_at=created_at or at(60),
    )
    persistence.snapshots.put_snapshot(snapshot)
    return snapshot


def mark_source_deleted(
    *, persistence: Persistence, evidence_id: str,
) -> JournalRecord:
    return append_source_availability_changed(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        evidence_id=evidence_id,
        availability=SourceAvailability.DELETED,
        occurred_at=at(70),
        recorded_at=at(70),
        reason="channel history purged by workspace retention",
    )


def engage_kill_switch(
    *, persistence: Persistence, engaged: bool, minutes: float,
) -> JournalRecord:
    return append_kill_switch_changed(
        journal=persistence.journal,
        organization_id=ORGANIZATION,
        engaged=engaged,
        reason="incident response" if engaged else "incident resolved",
        actor_id="person_priya_rao",
        occurred_at=at(minutes),
        recorded_at=at(minutes),
    )
