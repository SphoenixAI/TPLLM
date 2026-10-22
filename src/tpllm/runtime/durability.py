"""The durability seam between the runtime and the journal.

The orchestration pass is untouched. This module wraps it, and
the wrapping is where the ordering guarantees live.

**Journal first.** A durable change is appended before the runtime
treats it as real. Orchestrating first and crashing means the event
simply never happened — the one failure recovery cannot repair. The
reverse failure is fine: if the journal succeeds and the recompute
throws, the evidence is durable and a later replay rebuilds the
projection. Durable organizational truth is never rolled back because a
cache failed to build.

**Promote last.** Artifacts are persisted, the candidate is validated,
and only then does the current-revision pointer move. The audit record
that says a revision was published is appended after promotion, and is
deliberately not authoritative: a missing audit marker is a health
warning, not a reason to disbelieve a revision that is durably current.

Reconstruction runs the same orchestrator over the same journalled
evidence with a deterministic clock and the side-effect fence engaged,
which is what makes the delete-everything-and-replay test meaningful
rather than a separate code path that merely agrees.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from tpllm.integrations.envelopes import EvidenceEnvelope
from tpllm.persistence.integrity import content_fingerprint
from tpllm.persistence.models import (
    DurableEvidenceRecord,
    ExecutionContext,
    ExecutionMode,
    JournalEventType,
    JournalRecord,
    JournalRetentionClass,
    PortfolioRuntimeStateRecord,
    ProjectRuntimeStateRecord,
    ReplayClock,
    ReproducibilityManifest,
    RuntimeRevisionRecord,
    SourceAvailability,
    organization_stream,
    policy_stream,
    project_stream,
)
from tpllm.persistence.journal import build_journal_record
from tpllm.persistence.protocols import RevisionPromotionError
from tpllm.runtime.models import (
    LiveOrchestrationResult,
    PortfolioIntelligenceRuntimeState,
    ProjectIntelligenceRuntimeState,
    RuntimeAttentionAction,
    RuntimePolicy,
)
from tpllm.runtime.orchestrator import process_live_organizational_events
from tpllm.runtime.revisions import RuntimeStateStore as RuntimeProjection

logger = logging.getLogger(__name__)

# Attention actions that would have reached a person. During replay they
# are reconstructed and counted, never surfaced.
_SURFACING_ACTIONS = frozenset({
    RuntimeAttentionAction.ASYNC_INFORMATION,
    RuntimeAttentionAction.DECISION_REQUEST,
    RuntimeAttentionAction.HIGH_PRIORITY_ALERT,
})


# ── Evidence durability ──────────────────────────────────────────────


def envelope_to_durable_record(
    envelope: EvidenceEnvelope,
    *,
    retention_class: JournalRetentionClass = JournalRetentionClass.NORMALIZED,
) -> DurableEvidenceRecord:
    """Normalize an envelope into something durable on its own.

    Under a minimal retention policy the content is dropped and only its
    hash and provenance survive. Replay then reports an evidence
    limitation rather than reaching for a source that may be gone.
    """
    keep_content = retention_class != JournalRetentionClass.MINIMAL
    return DurableEvidenceRecord(
        evidence_id=envelope.id,
        source_system=envelope.source_system,
        source_type=envelope.source_type,
        source_id=envelope.source_id,
        event_type=envelope.event_type,
        observed_at=envelope.observed_at,
        received_at=envelope.received_at,
        project_ids=list(envelope.project_ids),
        canonical_entity_ids=[
            str(v) for v in (
                envelope.metadata.get("canonical_entity_ids") or []
            )
        ],
        source_pointer=envelope.source_pointer,
        content_hash=content_fingerprint(envelope.content),
        normalized_content=envelope.content if keep_content else None,
        title=envelope.title,
        metadata=dict(envelope.metadata),
        retention_class=retention_class.value,
        idempotency_key=envelope.idempotency_key,
    )


def durable_record_to_envelope(
    record: DurableEvidenceRecord,
) -> EvidenceEnvelope:
    """Rebuild an envelope from durable evidence.

    Reconstruction must not need Slack, Jira, or a meeting recorder to
    still be reachable, so everything the orchestrator reads is
    reproduced from the journal.
    """
    return EvidenceEnvelope(
        id=record.evidence_id,
        source_system=record.source_system,
        source_type=record.source_type,
        source_id=record.source_id,
        event_type=record.event_type,
        observed_at=record.observed_at,
        received_at=record.received_at,
        project_ids=list(record.project_ids),
        title=record.title,
        content=record.normalized_content,
        metadata=dict(record.metadata),
        source_pointer=record.source_pointer,
        idempotency_key=record.idempotency_key or record.evidence_id,
        retention_class=record.retention_class,
    )


def append_evidence_accepted(
    *,
    journal: Any,
    envelope: EvidenceEnvelope,
    organization_id: str,
    recorded_at: datetime,
    retention_class: JournalRetentionClass = JournalRetentionClass.NORMALIZED,
    causal_event_id: str | None = None,
) -> JournalRecord:
    """Make one piece of evidence durable, before anything derives from it."""
    durable = envelope_to_durable_record(
        envelope, retention_class=retention_class,
    )
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            stream_id=organization_stream(organization_id),
            occurred_at=envelope.observed_at,
            recorded_at=max(recorded_at, envelope.received_at),
            payload={"evidence": durable.model_dump(mode="json")},
            organization_id=organization_id,
            project_ids=list(envelope.project_ids),
            causal_event_id=causal_event_id,
            source_key=envelope.idempotency_key,
            evidence_ids=[envelope.id],
        )
    )


def append_source_availability_changed(
    *,
    journal: Any,
    organization_id: str,
    evidence_id: str,
    availability: SourceAvailability,
    occurred_at: datetime,
    recorded_at: datetime,
    reason: str = "",
) -> JournalRecord:
    """Record that a source went away, without touching the evidence.

    The original record stays exactly as it was. History can then answer
    honestly: this evidence existed and was used, and its source is no
    longer retrievable.
    """
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.SOURCE_AVAILABILITY_CHANGED,
            stream_id=organization_stream(organization_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "evidence_id": evidence_id,
                "availability": availability.value,
                "reason": reason,
            },
            organization_id=organization_id,
            evidence_ids=[evidence_id],
            source_key=f"{evidence_id}:{availability.value}",
        )
    )


# ── Human, agent, decision, policy, outcome ──────────────────────────


def append_human_action(
    *,
    journal: Any,
    organization_id: str,
    event_type: JournalEventType,
    actor_id: str,
    recommendation_id: str,
    occurred_at: datetime,
    recorded_at: datetime,
    project_ids: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> JournalRecord:
    """Persist what a person did before any projection reflects it.

    A human decision that survived only in a cache would be lost on the
    next crash, and nobody should be asked to make the same call twice.
    """
    return journal.append(
        record=build_journal_record(
            event_type=event_type,
            stream_id=organization_stream(organization_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "actor_id": actor_id,
                "recommendation_id": recommendation_id,
                **(payload or {}),
            },
            organization_id=organization_id,
            project_ids=list(project_ids or []),
            source_key=f"{recommendation_id}:{actor_id}:{event_type.value}",
        )
    )


def append_agent_recommendation(
    *,
    journal: Any,
    organization_id: str,
    recommendation_id: str,
    project_id: str,
    recommended_option_id: str | None,
    confidence: float,
    state_fingerprint: str,
    occurred_at: datetime,
    recorded_at: datetime,
    revision_id: str | None = None,
    method_version: str | None = None,
) -> JournalRecord:
    """Preserve what the agent actually recommended, when it did.

    "What did TPLLM recommend on August 18?" and "what would today's
    engine recommend from August 18's evidence?" are different
    questions. Only the first has a fixed answer, and this is where it
    is kept.
    """
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.AGENT_RECOMMENDATION_PUBLISHED,
            stream_id=project_stream(project_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "recommendation_id": recommendation_id,
                "project_id": project_id,
                "recommended_option_id": recommended_option_id,
                "confidence": confidence,
                "state_fingerprint": state_fingerprint,
                "method_version": method_version,
            },
            organization_id=organization_id,
            project_ids=[project_id],
            runtime_revision_id=revision_id,
            source_key=f"{recommendation_id}:{state_fingerprint}",
        )
    )


def append_recommendation_invalidated(
    *,
    journal: Any,
    organization_id: str,
    recommendation_id: str,
    project_id: str,
    reason: str,
    changed_inputs: list[str],
    occurred_at: datetime,
    recorded_at: datetime,
) -> JournalRecord:
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.AGENT_RECOMMENDATION_INVALIDATED,
            stream_id=project_stream(project_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "recommendation_id": recommendation_id,
                "project_id": project_id,
                "reason": reason,
                "changed_inputs": list(changed_inputs),
            },
            organization_id=organization_id,
            project_ids=[project_id],
            source_key=f"{recommendation_id}:invalidated:{occurred_at}",
        )
    )


def append_organizational_decision(
    *,
    journal: Any,
    organization_id: str,
    decision_id: str,
    project_id: str,
    conclusion: str | None,
    decided_by: str,
    origin: str,
    occurred_at: datetime,
    recorded_at: datetime,
    event_type: JournalEventType = (
        JournalEventType.ORGANIZATIONAL_DECISION_RECORDED
    ),
    payload: dict[str, Any] | None = None,
) -> JournalRecord:
    """Record a decision. Corrections come as new records, never edits.

    A wrong decision is reopened or superseded; the original stays, so
    history says what happened rather than what was later preferred.
    """
    return journal.append(
        record=build_journal_record(
            event_type=event_type,
            stream_id=project_stream(project_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "decision_id": decision_id,
                "project_id": project_id,
                "conclusion": conclusion,
                "decided_by": decided_by,
                "origin": origin,
                **(payload or {}),
            },
            organization_id=organization_id,
            project_ids=[project_id],
            source_key=f"{decision_id}:{event_type.value}:{occurred_at}",
        )
    )


def append_autonomous_decision(
    *,
    journal: Any,
    organization_id: str,
    decision_id: str,
    project_id: str,
    recommendation_id: str,
    policy_id: str,
    occurred_at: datetime,
    recorded_at: datetime,
    executed: bool,
    block_reason: str | None = None,
) -> JournalRecord:
    """Record an autonomous action, executed or refused.

    Replay restores these as facts. It never calls the finalizer again —
    an external consequence that already happened must not happen twice
    because history was rebuilt.
    """
    event_type = (
        JournalEventType.AUTONOMOUS_DECISION_FINALIZED if executed
        else JournalEventType.AUTONOMOUS_DECISION_BLOCKED
    )
    return journal.append(
        record=build_journal_record(
            event_type=event_type,
            stream_id=project_stream(project_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "decision_id": decision_id,
                "project_id": project_id,
                "recommendation_id": recommendation_id,
                "authority_policy_id": policy_id,
                "executed": executed,
                "block_reason": block_reason,
            },
            organization_id=organization_id,
            project_ids=[project_id],
            source_key=(
                f"{recommendation_id}:{event_type.value}:{occurred_at}"
            ),
        )
    )


def append_authority_policy_changed(
    *,
    journal: Any,
    organization_id: str,
    policy_id: str,
    previous_fingerprint: str | None,
    new_fingerprint: str,
    changed_fields: list[str],
    actor_id: str,
    effective_at: datetime,
    recorded_at: datetime,
) -> JournalRecord:
    """Policy change as a historical organizational event.

    Overwriting policy history would make "who widened the agent's
    authority, and when?" unanswerable — which is exactly the question
    an audit asks first.
    """
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.AUTHORITY_POLICY_CHANGED,
            stream_id=policy_stream(organization_id),
            occurred_at=effective_at,
            recorded_at=recorded_at,
            payload={
                "policy_id": policy_id,
                "previous_policy_fingerprint": previous_fingerprint,
                "new_policy_fingerprint": new_fingerprint,
                "changed_fields": list(changed_fields),
                "actor_id": actor_id,
                "effective_at": effective_at.isoformat(),
            },
            organization_id=organization_id,
            source_key=f"{policy_id}:{new_fingerprint}",
        )
    )


def append_kill_switch_changed(
    *,
    journal: Any,
    organization_id: str,
    engaged: bool,
    reason: str | None,
    actor_id: str,
    occurred_at: datetime,
    recorded_at: datetime,
) -> JournalRecord:
    """Every engagement and release of the kill switch, with authority."""
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.AUTONOMY_KILL_SWITCH_CHANGED,
            stream_id=policy_stream(organization_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "engaged": engaged,
                "reason": reason,
                "actor_id": actor_id,
            },
            organization_id=organization_id,
            source_key=f"kill-switch:{engaged}:{occurred_at.isoformat()}",
        )
    )


def append_outcome(
    *,
    journal: Any,
    organization_id: str,
    outcome_id: str,
    project_id: str,
    decision_id: str | None,
    kpi_ids: list[str] | None,
    milestone_ids: list[str] | None,
    observed_value: str,
    occurred_at: datetime,
    recorded_at: datetime,
) -> JournalRecord:
    """What actually happened, linked to what was decided.

    The link is what lets calibration and procedural memory learn from
    history rather than from impressions of it.
    """
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.OUTCOME_RECORDED,
            stream_id=project_stream(project_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "outcome_id": outcome_id,
                "project_id": project_id,
                "decision_id": decision_id,
                "kpi_ids": list(kpi_ids or []),
                "milestone_ids": list(milestone_ids or []),
                "observed_value": observed_value,
            },
            organization_id=organization_id,
            project_ids=[project_id],
            source_key=outcome_id,
        )
    )


def append_revision_published(
    *,
    journal: Any,
    organization_id: str,
    revision_id: str,
    state_fingerprints: dict[str, str],
    project_ids: list[str],
    occurred_at: datetime,
    recorded_at: datetime,
) -> JournalRecord:
    """The audit marker for a published revision.

    Carries the state fingerprints, which is what lets equivalence be
    checked after every projection has been deleted. Non-authoritative:
    if this append fails after promotion, the revision is still durably
    current and health reporting notes the missing marker.
    """
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.INTELLIGENCE_REVISION_PUBLISHED,
            stream_id=organization_stream(organization_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "revision_id": revision_id,
                "state_fingerprints": dict(state_fingerprints),
            },
            organization_id=organization_id,
            project_ids=list(project_ids),
            runtime_revision_id=revision_id,
            source_key=revision_id,
        )
    )


def append_artifact_published(
    *,
    journal: Any,
    organization_id: str,
    artifact_type: str,
    artifact_id: str,
    revision_id: str,
    fingerprint: str,
    manifest: ReproducibilityManifest,
    occurred_at: datetime,
    recorded_at: datetime,
    project_ids: list[str] | None = None,
) -> JournalRecord:
    """Note that an artifact mattered historically.

    The artifact itself stays in the cache; the journal keeps its
    identity, fingerprint, and the manifest it was derived under — which
    is what later distinguishes "reproduced" from "recomputed".
    """
    return journal.append(
        record=build_journal_record(
            event_type=JournalEventType.DERIVED_ARTIFACT_PUBLISHED,
            stream_id=organization_stream(organization_id),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            payload={
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
                "revision_id": revision_id,
                "fingerprint": fingerprint,
                "manifest": manifest.model_dump(mode="json"),
            },
            organization_id=organization_id,
            project_ids=list(project_ids or []),
            runtime_revision_id=revision_id,
            source_key=f"{artifact_type}:{artifact_id}:{revision_id}",
        )
    )


# ── The durable live pass ────────────────────────────────────────────


class DurableOrchestrationResult(BaseModel):
    """One durable pass: what was journalled, published, and prevented."""
    result: LiveOrchestrationResult
    journal_records: list[str] = Field(default_factory=list)
    # Newly durable records, not envelopes handed in: a redelivery is an
    # ordinary event and reporting it as an append would overstate what
    # changed.
    evidence_records_appended: int = 0
    evidence_records_deduplicated: int = 0
    revision_promoted: bool = False
    artifacts_persisted: int = 0
    identities_linked: dict[str, str] = Field(default_factory=dict)
    identities_ambiguous: dict[str, list[str]] = Field(default_factory=dict)
    identities_unresolved: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def identity_needs_attention(self) -> bool:
        """An ambiguous identity is a question for a person.

        It also means the portfolio layer may still be seeing two names
        for one thing, which is worth surfacing rather than burying.
        """
        return bool(self.identities_ambiguous)


def process_durable_organizational_events(
    *,
    envelopes: list[EvidenceEnvelope],
    organization_id: str,
    journal: Any,
    state_store: Any,
    artifact_store: Any,
    runtime_projection: RuntimeProjection,
    intelligence_services: Any,
    runtime_policy: RuntimePolicy | None = None,
    manifest: ReproducibilityManifest,
    now: datetime,
    retention_class: JournalRetentionClass = JournalRetentionClass.NORMALIZED,
    identity_registry: Any = None,
) -> tuple[DurableOrchestrationResult, RuntimeProjection]:
    """Append first, resolve identity, orchestrate, promote last.

    If the recompute raises after the journal append, the evidence is
    already durable and recovery will rebuild the projection. Durable
    organizational truth is never rolled back because a cache failed.

    Identity resolution sits between the append and the orchestration
    because it is the step that makes two names for one authority become
    one node — and because its decisions are themselves durable facts,
    not derived state.
    """
    # The journal head before this batch. An append is idempotent, so a
    # redelivered webhook comes back as the record that already exists —
    # and counting those as new would report work that did not happen.
    head = journal.latest_position()
    head_before = head.global_sequence if head is not None else 0

    appended: list[str] = []
    newly_appended = 0
    for envelope in envelopes:
        record = append_evidence_accepted(
            journal=journal,
            envelope=envelope,
            organization_id=organization_id,
            recorded_at=now,
            retention_class=retention_class,
        )
        appended.append(record.id)
        if record.position.global_sequence > head_before:
            newly_appended += 1
    logger.info(
        "evidence journalled before anything derived from it",
        extra={
            "organization_id": organization_id,
            "evidence_count": len(envelopes),
            "records_appended": newly_appended,
            "events_deduplicated": len(envelopes) - newly_appended,
        },
    )

    identity_outcome = None
    if identity_registry is not None:
        from tpllm.runtime.identity_resolution import (
            resolve_envelope_identities,
        )

        identity_registry, identity_outcome = resolve_envelope_identities(
            envelopes=envelopes,
            registry=identity_registry,
            organization_id=organization_id,
            journal=journal,
            now=now,
        )
        appended.extend(identity_outcome.journal_record_ids)

    result, projection = process_live_organizational_events(
        envelopes=envelopes,
        runtime_state_store=runtime_projection,
        intelligence_services=intelligence_services,
        runtime_policy=runtime_policy,
        now=now,
    )

    durable = DurableOrchestrationResult(
        result=result,
        journal_records=list(appended),
        evidence_records_appended=newly_appended,
        evidence_records_deduplicated=len(envelopes) - newly_appended,
        identities_linked=(
            dict(identity_outcome.linked) if identity_outcome else {}
        ),
        identities_ambiguous=(
            dict(identity_outcome.ambiguous) if identity_outcome else {}
        ),
        identities_unresolved=(
            list(identity_outcome.unresolved) if identity_outcome else []
        ),
    )
    if identity_outcome is not None and identity_outcome.ambiguous:
        logger.warning(
            "identities left unlinked as ambiguous rather than guessed",
            extra={
                "organization_id": organization_id,
                "identity_linked_count": len(identity_outcome.linked),
                "identity_ambiguous_count": len(identity_outcome.ambiguous),
            },
        )
        durable.warnings.append(
            f"{len(identity_outcome.ambiguous)} identity(ies) were "
            f"ambiguous and left unlinked; two names for one thing may "
            f"still be counted twice until a person settles them."
        )

    if not result.complete or result.revision_id is None:
        # A pass that completed and simply found nothing material is a
        # normal outcome, not a problem. Only an incomplete pass — an
        # engine that failed — deserves a warning, or the warning level
        # stops meaning anything.
        logger.log(
            logging.INFO if result.complete else logging.WARNING,
            "no revision published; evidence is durable regardless",
            extra={
                "organization_id": organization_id,
                "complete": result.complete,
                "failure_count": len(result.failures),
                "evidence_count": len(envelopes),
            },
        )
        durable.warnings.append(
            "No complete revision was produced; evidence is durable and a "
            "later recovery will rebuild the projection."
        )
        return durable, projection

    fingerprints = collect_state_fingerprints(projection)
    persisted = persist_revision(
        organization_id=organization_id,
        revision_id=result.revision_id,
        projection=projection,
        state_store=state_store,
        artifact_store=artifact_store,
        fingerprints=fingerprints,
        manifest=manifest,
        now=now,
    )
    durable.artifacts_persisted = persisted

    try:
        state_store.promote_revision(
            organization_id=organization_id,
            revision_id=result.revision_id,
            expected_current_revision_id=state_store.get_current_revision(
                organization_id
            ),
        )
        durable.revision_promoted = True
        logger.info(
            "revision promoted to current",
            extra={
                "organization_id": organization_id,
                "revision_id": result.revision_id,
                "project_count": len(result.projects_affected),
                "artifact_count": persisted,
                "promoted": True,
            },
        )
    except RevisionPromotionError as error:
        logger.error(
            "revision not promoted; previous revision remains current",
            extra={
                "organization_id": organization_id,
                "revision_id": result.revision_id,
                "promoted": False,
                "reason": str(error),
            },
        )
        durable.warnings.append(f"Revision not promoted: {error}")
        return durable, projection

    # Audit marker, after promotion and deliberately non-authoritative.
    try:
        marker = append_revision_published(
            journal=journal,
            organization_id=organization_id,
            revision_id=result.revision_id,
            state_fingerprints=fingerprints,
            project_ids=list(result.projects_affected),
            occurred_at=now,
            recorded_at=now,
        )
        durable.journal_records.append(marker.id)
    except Exception as error:  # noqa: BLE001 — audit, not truth
        logger.warning(
            "revision is durably current but its audit marker was not written",
            extra={
                "organization_id": organization_id,
                "revision_id": result.revision_id,
                "reason": str(error),
            },
        )
        durable.warnings.append(
            f"Revision {result.revision_id} is durably current but its "
            f"audit marker was not written: {error}"
        )

    return durable, projection


def collect_state_fingerprints(
    projection: RuntimeProjection,
) -> dict[str, str]:
    """The semantic fingerprints a revision published.

    Keyed by project, plus the portfolio. These are what equivalence is
    checked against, because artifact ids may carry generated
    identifiers while the semantic state does not.
    """
    fingerprints = {
        f"project:{project_id}": state.state_fingerprint
        for project_id, state in sorted(projection.project_states.items())
    }
    if projection.portfolio_state is not None:
        fingerprints["portfolio"] = projection.portfolio_state.state_fingerprint
    return fingerprints


def persist_revision(
    *,
    organization_id: str,
    revision_id: str,
    projection: RuntimeProjection,
    state_store: Any,
    artifact_store: Any,
    fingerprints: dict[str, str],
    manifest: ReproducibilityManifest,
    now: datetime,
) -> int:
    """Write the candidate's projections and artifacts, before promotion."""
    revision = projection.current_revision
    state_store.put_revision(
        RuntimeRevisionRecord(
            revision_id=revision_id,
            organization_id=organization_id,
            complete=True,
            parent_revision_id=(
                revision.parent_revision_id if revision else None
            ),
            evidence_cutoff_at=(
                revision.evidence_cutoff_at if revision else None
            ),
            created_at=now,
            state_fingerprint=(
                revision.state_fingerprint if revision else ""
            ),
            state_fingerprints=dict(fingerprints),
        )
    )

    for project_id, state in projection.project_states.items():
        state_store.put_project_state(
            ProjectRuntimeStateRecord(
                project_id=project_id,
                organization_id=organization_id,
                revision_id=revision_id,
                state_fingerprint=state.state_fingerprint,
                state=state.model_dump(mode="json"),
                updated_at=now,
            )
        )
    if projection.portfolio_state is not None:
        state_store.put_portfolio_state(
            PortfolioRuntimeStateRecord(
                organization_id=organization_id,
                revision_id=revision_id,
                state_fingerprint=(
                    projection.portfolio_state.state_fingerprint
                ),
                state=projection.portfolio_state.model_dump(mode="json"),
                updated_at=now,
            )
        )

    persisted = 0
    if artifact_store is not None:
        for project_id, state in projection.project_states.items():
            artifact_store.put(
                artifact_type="project_runtime_state",
                artifact_id=project_id,
                revision_id=revision_id,
                payload=state.model_dump(mode="json"),
                fingerprint=state.state_fingerprint,
                derivation_version=manifest.runtime_derivation_version,
                policy_fingerprint=manifest.policy_fingerprint,
                created_at=now,
            )
            persisted += 1
    return persisted


# ── Reconstruction ───────────────────────────────────────────────────


class ReconstructionOutcome(BaseModel):
    """What a replay rebuilt, and what it prevented."""
    revision_id: str | None = None
    state_fingerprints: dict[str, str] = Field(default_factory=dict)
    project_states: dict[str, dict] = Field(default_factory=dict)
    portfolio_state: dict | None = None
    suppressed_side_effects: int = 0
    historical_decisions: list[dict] = Field(default_factory=list)
    historical_recommendations: list[dict] = Field(default_factory=list)
    historical_outcomes: list[dict] = Field(default_factory=list)
    current_policy_fingerprint: str | None = None
    kill_switch_engaged: bool = False
    unavailable_evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RuntimeJournalReconstructor:
    """Rebuilds runtime projections from durable history.

    Deliberately drives the same orchestrator that produced
    the original state, over the same journalled evidence, with a
    deterministic clock and the side-effect fence engaged. A separate
    reconstruction path would only ever prove that two pieces of code
    agree with each other.
    """

    def __init__(
        self,
        *,
        intelligence_services: Any,
        runtime_policy: RuntimePolicy | None = None,
    ):
        self.intelligence_services = intelligence_services
        self.runtime_policy = runtime_policy

    def apply_records(
        self,
        *,
        records: list[JournalRecord],
        organization_id: str,
        context: ExecutionContext,
        baseline: dict[str, Any] | None = None,
    ) -> ReconstructionOutcome:
        if not context.suppress_side_effects:
            raise RuntimeError(
                "Reconstruction must run with side effects suppressed; "
                "rebuilding history must not repeat its consequences."
            )

        logger.info(
            "reconstructing projections from durable history",
            extra={
                "organization_id": organization_id,
                "execution_mode": context.mode.value,
                "records_replayed": len(records),
                "revision_id": (baseline or {}).get("active_revision_id"),
            },
        )
        outcome = ReconstructionOutcome()
        projection = _projection_from_baseline(
            organization_id=organization_id, baseline=baseline,
        )
        baseline_revision = (baseline or {}).get("active_revision_id")
        outcome.revision_id = baseline_revision

        envelopes: list[EvidenceEnvelope] = []
        for record in records:
            self._apply_record(
                record=record,
                envelopes=envelopes,
                outcome=outcome,
                context=context,
            )

        if envelopes:
            # One deterministic pass over all reconstructed evidence.
            # The orchestrator orders it by reality time itself, so the
            # late-arriving events stay late. Seeded from the snapshot,
            # so projects the delta does not touch keep the state the
            # checkpoint already established rather than vanishing.
            result, projection = process_live_organizational_events(
                envelopes=envelopes,
                runtime_state_store=projection,
                intelligence_services=self.intelligence_services,
                runtime_policy=self.runtime_policy,
                now=context.clock.now(),
            )
            outcome.revision_id = result.revision_id or baseline_revision
            outcome.suppressed_side_effects += sum(
                1 for action in result.attention_actions
                if action in _SURFACING_ACTIONS
            )

        outcome.project_states = {
            project_id: state.model_dump(mode="json")
            for project_id, state in projection.project_states.items()
        }
        outcome.portfolio_state = (
            projection.portfolio_state.model_dump(mode="json")
            if projection.portfolio_state is not None else None
        )
        outcome.state_fingerprints = collect_state_fingerprints(projection)
        logger.info(
            "reconstruction complete; no external effect was repeated",
            extra={
                "organization_id": organization_id,
                "execution_mode": context.mode.value,
                "revision_id": outcome.revision_id,
                "project_count": len(outcome.project_states),
                "side_effects_suppressed": outcome.suppressed_side_effects,
            },
        )
        return outcome

    def _apply_record(
        self,
        *,
        record: JournalRecord,
        envelopes: list[EvidenceEnvelope],
        outcome: ReconstructionOutcome,
        context: ExecutionContext,
    ) -> None:
        event_type = record.event_type
        payload = record.payload

        if event_type == JournalEventType.EVIDENCE_ACCEPTED:
            evidence = DurableEvidenceRecord.model_validate(
                payload["evidence"]
            )
            if evidence.content_omitted_by_retention:
                outcome.warnings.append(
                    f"Evidence {evidence.evidence_id} was retained without "
                    f"content under a minimal policy; reconstruction is "
                    f"limited by design, not by loss."
                )
            envelopes.append(durable_record_to_envelope(evidence))

        elif event_type == JournalEventType.SOURCE_AVAILABILITY_CHANGED:
            if payload.get("availability") != SourceAvailability.AVAILABLE.value:
                outcome.unavailable_evidence_ids.append(
                    str(payload.get("evidence_id"))
                )

        elif event_type in (
            JournalEventType.ORGANIZATIONAL_DECISION_RECORDED,
            JournalEventType.ORGANIZATIONAL_DECISION_REOPENED,
            JournalEventType.ORGANIZATIONAL_DECISION_SUPERSEDED,
        ):
            outcome.historical_decisions.append({
                "event_type": event_type.value,
                "occurred_at": record.occurred_at.isoformat(),
                **payload,
            })

        elif event_type == JournalEventType.AUTONOMOUS_DECISION_FINALIZED:
            # Restored as a fact. The finalizer is not called again: the
            # external consequence already happened once.
            outcome.historical_decisions.append({
                "event_type": event_type.value,
                "occurred_at": record.occurred_at.isoformat(),
                **payload,
            })
            outcome.suppressed_side_effects += 1

        elif event_type == JournalEventType.AUTONOMOUS_DECISION_BLOCKED:
            outcome.historical_decisions.append({
                "event_type": event_type.value,
                "occurred_at": record.occurred_at.isoformat(),
                **payload,
            })

        elif event_type in (
            JournalEventType.AGENT_RECOMMENDATION_PUBLISHED,
            JournalEventType.AGENT_RECOMMENDATION_INVALIDATED,
            JournalEventType.HUMAN_RECOMMENDATION_VALIDATED,
            JournalEventType.HUMAN_RECOMMENDATION_MODIFIED,
            JournalEventType.HUMAN_RECOMMENDATION_REJECTED,
            JournalEventType.HUMAN_RECOMMENDATION_DEFERRED,
        ):
            outcome.historical_recommendations.append({
                "event_type": event_type.value,
                "occurred_at": record.occurred_at.isoformat(),
                **payload,
            })

        elif event_type == JournalEventType.AUTHORITY_POLICY_CHANGED:
            outcome.current_policy_fingerprint = str(
                payload.get("new_policy_fingerprint")
            )

        elif event_type == JournalEventType.AUTONOMY_KILL_SWITCH_CHANGED:
            outcome.kill_switch_engaged = bool(payload.get("engaged"))

        elif event_type == JournalEventType.OUTCOME_RECORDED:
            outcome.historical_outcomes.append({
                "occurred_at": record.occurred_at.isoformat(),
                **payload,
            })


def build_replay_context(
    *, replay_as_of: datetime, manifest: ReproducibilityManifest | None = None,
) -> ExecutionContext:
    """A deterministic, fenced context for reconstruction."""
    return ExecutionContext(
        mode=ExecutionMode.REPLAY,
        clock=ReplayClock(replay_as_of=replay_as_of),
        random_seed=0,
        manifest=manifest,
        suppress_side_effects=True,
    )


def _projection_from_baseline(
    *,
    organization_id: str,
    baseline: dict[str, Any] | None,
) -> RuntimeProjection:
    """Seed a projection from a snapshot checkpoint.

    Without this a snapshot-accelerated replay would start from nothing
    and silently lose every project the delta happens not to mention —
    reporting them as "published but not reconstructed" when the
    snapshot held their state all along. A snapshot is an accelerator,
    so starting from it must give the same answer as replaying from
    record one.
    """
    projection = RuntimeProjection(organization_id=organization_id)
    if not baseline:
        return projection

    for project_id, state in (baseline.get("project_states") or {}).items():
        try:
            projection = projection.with_project_state(
                ProjectIntelligenceRuntimeState.model_validate(state)
            )
        except Exception:  # noqa: BLE001 — a bad entry is not fatal
            continue

    portfolio_state = baseline.get("portfolio_state")
    if portfolio_state:
        try:
            projection = projection.with_portfolio_state(
                PortfolioIntelligenceRuntimeState.model_validate(
                    portfolio_state
                )
            )
        except Exception:  # noqa: BLE001
            pass
    return projection
