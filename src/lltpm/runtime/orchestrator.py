"""The live organizational intelligence orchestrator (Commit 30).

This is the layer that makes the rest of TPLLM behave like one system
rather than a shelf of engines. It answers, for every incoming event:

    What changed? What does that invalidate? What must be recomputed, in
    what order? Which projects and portfolio relationships are affected?
    Did any probability move enough to matter? Did the best
    recommendation change? Is an autonomous action still eligible? Does a
    human need to know — or can the system stay silent?

The path is deliberately long and each step is deliberately narrow:

    envelopes -> dedupe -> temporal order -> historical classification ->
    scope -> coalesced batch -> candidate revision -> topological execute
    -> materiality -> causal compression -> attention -> publish

The alternative — rerun everything, notify — is what makes live systems
unusable. Restraint is the feature.

The whole pass is a function of its inputs: load state, compute, write
state, return. No daemon, no broker, no scheduler. A serverless
invocation can run it and exit.
"""

from __future__ import annotations

import logging
from datetime import datetime

from lltpm.integrations.envelopes import EvidenceEnvelope
from lltpm.runtime.causality import (
    bundle_causally_related_changes,
    build_causal_change,
    build_root_summaries,
)
from lltpm.runtime.dependency_graph import build_intelligence_dependency_graph
from lltpm.runtime.ingest import (
    assert_no_future_events,
    classify_historical_effect,
    deduplicate_events,
    detect_contested_subjects,
    envelope_to_runtime_event,
    latest_occurred_at,
    order_runtime_events,
    partition_planned_events,
    subject_latest_map,
)
from lltpm.runtime.materiality import assess_artifact_materiality
from lltpm.runtime.models import (
    PORTFOLIO_ARTIFACT_TYPES,
    CausalIntelligenceChange,
    IntelligenceArtifactType as A,
    LiveOrchestrationResult,
    NoActionReason,
    PortfolioIntelligenceUpdate,
    ProjectIntelligenceRuntimeState,
    ProjectIntelligenceUpdate,
    RecomputedArtifact,
    RuntimeAttentionAction,
    RuntimeEvidenceEvent,
    RuntimeMetrics,
    RuntimePolicy,
)
from lltpm.runtime.recompute import (
    IntelligenceServices,
    build_recompute_batch,
    execute_recompute_batch,
)
from lltpm.runtime.revisions import (
    RuntimeStateStore,
    apply_artifacts_to_portfolio_state,
    apply_artifacts_to_project_state,
    build_candidate_revision,
    complete_revision,
)
from lltpm.runtime.surfaces import determine_attention_action

logger = logging.getLogger(__name__)

_PROJECT_UPDATE_FIELDS: dict[A, str] = {
    A.KPI_STATE: "kpi_changes",
    A.LAUNCH_READINESS: "readiness_changes",
    A.SCENARIO_LATTICE: "scenario_changes",
    A.DECISION_RECOMMENDATION: "recommendation_changes",
    A.SCENARIO_DECISION_RECOMMENDATION: "recommendation_changes",
}


def process_live_organizational_events(
    *,
    envelopes: list[EvidenceEnvelope],
    runtime_state_store: RuntimeStateStore,
    intelligence_services: IntelligenceServices,
    runtime_policy: RuntimePolicy | None = None,
    now: datetime,
) -> tuple[LiveOrchestrationResult, RuntimeStateStore]:
    """Run one live orchestration pass.

    Returns the result and the new store. The store is replaced rather
    than mutated, so a pass that fails part-way leaves the previous
    state — and the previous published revision — completely intact.
    """
    policy = runtime_policy or RuntimePolicy()
    graph = build_intelligence_dependency_graph()

    # ── Normalize and deduplicate ────────────────────────────────────
    incoming = [envelope_to_runtime_event(e) for e in envelopes]
    fresh, duplicates = deduplicate_events(
        incoming, known_keys=runtime_state_store.processed_keys,
    )
    store = runtime_state_store.with_metrics(
        events_received=len(incoming),
        events_deduplicated=len(duplicates),
    )
    logger.debug(
        "orchestration pass starting",
        extra={
            "organization_id": store.organization_id,
            "events_received": len(incoming),
            "events_deduplicated": len(duplicates),
        },
    )

    if not fresh:
        return _quiet_result(
            store=store,
            events_processed=0,
            events_deduplicated=len(duplicates),
            reason=(
                NoActionReason.DUPLICATE if duplicates
                else NoActionReason.NON_MATERIAL
            ),
        )

    # ── Temporal discipline ──────────────────────────────────────────
    assert_no_future_events(fresh, now=now)
    observed, planned = partition_planned_events(fresh)
    if not observed:
        store = store.with_processed_keys(
            [e.idempotency_key or e.envelope_id for e in fresh]
        )
        return _quiet_result(
            store=store,
            events_processed=len(fresh),
            events_deduplicated=len(duplicates),
            reason=NoActionReason.EXISTING_PLAN_SUFFICIENT,
        )

    ordered = order_runtime_events(observed)
    # Merge by newest, never by overwrite: a late batch must not lower the
    # watermark for a subject something newer has already spoken about.
    subject_latest = dict(store.subject_latest_occurred_at)
    for subject_key, moment in subject_latest_map(ordered).items():
        current = subject_latest.get(subject_key)
        if current is None or moment > current:
            subject_latest[subject_key] = moment

    lineage_only_ids: set[str] = set()
    late_count = 0
    for event in ordered:
        effect = classify_historical_effect(
            event,
            latest_known_occurred_at=store.latest_occurred_at,
            subject_latest_occurred_at=subject_latest,
        )
        if effect != "current":
            late_count += 1
        if effect == "lineage_only":
            lineage_only_ids.add(event.envelope_id)

    contested = detect_contested_subjects(ordered)

    # ── Plan ─────────────────────────────────────────────────────────
    revision_index = store.next_revision_index()
    revision_id = f"revision-{revision_index}"
    batch = build_recompute_batch(
        events=ordered,
        dependency_graph=graph,
        current_runtime_state=store,
        revision_id=revision_id,
        created_at=now,
        topology=policy.topology,
        fanout_policy=policy.fanout,
        lineage_only_event_ids=lineage_only_ids,
    )

    if not batch.requests:
        store = _record_processed(store, fresh, ordered, contested)
        return _quiet_result(
            store=store,
            events_processed=len(fresh),
            events_deduplicated=len(duplicates),
            reason=NoActionReason.NON_MATERIAL,
            lineage_only_event_ids=sorted(lineage_only_ids),
            contested=contested,
        )

    # ── Candidate revision ───────────────────────────────────────────
    candidate = build_candidate_revision(
        revision_index=revision_index,
        evidence_cutoff_at=latest_occurred_at(ordered) or now,
        created_at=now,
        project_ids=sorted(batch.affected_project_ids),
        root_evidence_ids=[
            eid for e in ordered for eid in e.root_evidence_ids
        ],
        required_artifact_types=list(batch.topological_order),
        causal_event_ids=[e.causal_event_id for e in ordered],
        parent_revision_id=(
            store.current_revision.id if store.current_revision else None
        ),
        organization_id=policy.organization_id,
    )
    store = store.with_revision_index(revision_index).with_metrics(
        candidate_revisions=1,
        recompute_requests_generated=len(batch.requests),
        late_events_processed=late_count,
    )

    # ── Execute ──────────────────────────────────────────────────────
    execution = execute_recompute_batch(
        batch=batch,
        runtime_services=intelligence_services,
        current_state=store,
        known_fingerprints=store.artifact_fingerprints,
        skip_unchanged=policy.skip_unchanged_artifacts,
        dependency_graph=graph,
    )

    revision = complete_revision(
        revision=candidate,
        artifacts=execution.artifacts,
        failures=execution.failures,
    )
    for failure in execution.failures:
        # Recorded, never raised: one engine failing must not take the
        # whole pass down, and the revision stays incomplete rather than
        # partially publishing.
        logger.warning(
            "artifact recompute failed",
            extra={
                "organization_id": store.organization_id,
                "revision_id": revision_id,
                "artifact_type": failure.artifact_type.value,
                "reason": failure.explanation,
            },
        )

    # ── Interpret ────────────────────────────────────────────────────
    root_summaries = build_root_summaries(ordered)
    changes = _build_changes(
        artifacts=execution.artifacts,
        events=ordered,
        policy=policy,
        revision_id=revision_id,
    )
    bundles = bundle_causally_related_changes(
        changes, root_summaries=root_summaries,
    )
    quiet_projects = sorted(
        set(policy.topology.all_project_ids) - set(batch.affected_project_ids)
    )
    for index, bundle in enumerate(bundles):
        bundles[index] = bundle.model_copy(update={
            "no_action_project_ids": quiet_projects,
        })

    attention_actions = [
        determine_attention_action(
            bundle=bundle, materiality_policy=policy.scenario_materiality,
        )
        for bundle in bundles
    ]

    project_updates = _build_project_updates(
        artifacts=execution.artifacts,
        changes=changes,
        revision_id=revision_id,
        affected_project_ids=sorted(batch.affected_project_ids),
        root_summaries=root_summaries,
        quiet_projects=quiet_projects,
    )
    portfolio_update = _build_portfolio_update(
        artifacts=execution.artifacts,
        changes=changes,
        revision_id=revision_id,
        affected_project_ids=sorted(batch.affected_project_ids),
        quiet_projects=quiet_projects,
    )

    # ── Publish ──────────────────────────────────────────────────────
    material_count = sum(1 for c in changes if c.materiality.material)
    store = store.with_metrics(
        artifacts_recomputed=len(execution.artifacts),
        artifacts_skipped_unchanged=len(execution.skipped),
        projects_affected=len(batch.affected_project_ids),
        material_changes_detected=material_count,
        non_material_changes_suppressed=len(changes) - material_count,
        notifications_compressed=max(0, len(changes) - len(bundles)),
    )
    store = _record_processed(store, fresh, ordered, contested)
    store = store.with_fingerprints({
        f"{a.artifact_type.value}:{_scope_key_for(batch, a.artifact_type)}":
            a.state_fingerprint
        for a in execution.artifacts
    })

    if revision.complete:
        store = store.promote(revision).with_metrics(successful_revisions=1)
        store = _apply_states(
            store=store,
            artifacts=execution.artifacts,
            revision_id=revision_id,
            now=now,
            affected_project_ids=sorted(batch.affected_project_ids),
            material=material_count > 0,
        )
    else:
        store = store.record_failed_candidate(revision)

    logger.info(
        "revision published" if revision.complete
        else "candidate revision incomplete; nothing promoted",
        extra={
            "organization_id": store.organization_id,
            "revision_id": revision_id,
            "parent_revision_id": candidate.parent_revision_id,
            "complete": revision.complete,
            "events_processed": len(fresh),
            "project_count": len(batch.affected_project_ids),
            "artifact_count": len(execution.artifacts),
            "skipped_count": len(execution.skipped),
            "failure_count": len(execution.failures),
        },
    )
    return (
        LiveOrchestrationResult(
            revision_id=revision_id if revision.complete else None,
            events_processed=len(fresh),
            events_deduplicated=len(duplicates),
            projects_affected=sorted(batch.affected_project_ids),
            artifacts_recomputed=[
                a.artifact_id for a in execution.artifacts
            ],
            artifacts_skipped=[
                r.artifact_type.value for r in execution.skipped
            ],
            failures=execution.failures,
            project_updates=project_updates,
            portfolio_update=portfolio_update,
            causal_bundles=bundles,
            attention_actions=attention_actions,
            autonomous_actions_blocked=(
                _blocked_autonomous_actions(execution.failures)
            ),
            complete=revision.complete,
            deferred_project_ids=list(batch.deferred_project_ids),
            no_action_reasons=(
                [] if material_count else [NoActionReason.NON_MATERIAL]
            ),
            lineage_only_event_ids=sorted(lineage_only_ids),
            contested_subject_keys=contested,
            metrics=store.metrics,
        ),
        store,
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _scope_key_for(batch, artifact_type: A) -> str:
    requests = batch.request_for(artifact_type)
    return requests[0].scope.scope_key() if requests else ""


def _record_processed(
    store: RuntimeStateStore,
    fresh: list[RuntimeEvidenceEvent],
    ordered: list[RuntimeEvidenceEvent],
    contested: list[str],
) -> RuntimeStateStore:
    store = store.with_processed_keys(
        [e.idempotency_key or e.envelope_id for e in fresh]
    )
    store = store.with_temporal_watermarks(
        latest_occurred_at=latest_occurred_at(ordered),
        subject_latest=subject_latest_map(ordered),
    )
    if contested:
        store = store.with_contested_subjects(contested)
    return store


def _build_changes(
    *,
    artifacts: list[RecomputedArtifact],
    events: list[RuntimeEvidenceEvent],
    policy: RuntimePolicy,
    revision_id: str,
) -> list[CausalIntelligenceChange]:
    """Attach materiality and ancestry to every recomputed artifact.

    Parent links follow the dependency graph, so a scenario change points
    back at the KPI change that caused it and the bundle can render the
    chain instead of a list.
    """
    causal_event_id = events[0].causal_event_id if events else "unknown"
    root_evidence = sorted({
        eid for e in events for eid in e.root_evidence_ids
    })
    origin = _origin_artifact_type(events)

    changes: list[CausalIntelligenceChange] = []
    by_type: dict[A, str] = {}
    graph = build_intelligence_dependency_graph()
    upstream_of: dict[A, set[A]] = {
        target: {s for s, targets in graph.items() if target in targets}
        for target in A
    }

    for artifact in artifacts:
        materiality = assess_artifact_materiality(
            artifact=artifact,
            policy=policy.scenario_materiality,
            severe_outcome_labels=policy.severe_outcome_labels,
            alert_thresholds=policy.severe_outcome_alert_thresholds,
        )
        parents = [
            by_type[source]
            for source in sorted(
                upstream_of.get(artifact.artifact_type, set()),
                key=lambda a: a.value,
            )
            if source in by_type
        ]
        change = build_causal_change(
            artifact=artifact,
            materiality=materiality,
            causal_event_id=causal_event_id,
            root_evidence_ids=root_evidence,
            parent_change_ids=parents,
            origin_artifact_type=origin,
        )
        by_type[artifact.artifact_type] = change.id
        changes.append(change)
    return changes


def _origin_artifact_type(events: list[RuntimeEvidenceEvent]) -> A | None:
    from lltpm.runtime.recompute import determine_direct_invalidations

    for event in events:
        direct = determine_direct_invalidations(event=event)
        if direct:
            return sorted(direct, key=lambda a: a.value)[0]
    return None


def _build_project_updates(
    *,
    artifacts: list[RecomputedArtifact],
    changes: list[CausalIntelligenceChange],
    revision_id: str,
    affected_project_ids: list[str],
    root_summaries: dict[str, str],
    quiet_projects: list[str],
) -> list[ProjectIntelligenceUpdate]:
    """One update per affected project, phrased as change and cause."""
    by_project: dict[str, ProjectIntelligenceUpdate] = {
        project_id: ProjectIntelligenceUpdate(
            project_id=project_id, revision_id=revision_id,
        )
        for project_id in affected_project_ids
    }
    change_by_artifact = {c.artifact_id: c for c in changes}

    for artifact in artifacts:
        if artifact.artifact_type in PORTFOLIO_ARTIFACT_TYPES:
            continue
        change = change_by_artifact.get(artifact.artifact_id)
        if change is None or not change.materiality.material:
            continue
        field = _PROJECT_UPDATE_FIELDS.get(artifact.artifact_type)
        for project_id in artifact.project_ids:
            update = by_project.get(project_id)
            if update is None:
                continue
            updates: dict[str, object] = {}
            what_changed = list(update.what_changed)
            if artifact.after_summary not in what_changed:
                what_changed.append(artifact.after_summary)
            updates["what_changed"] = what_changed

            why = list(update.why_it_changed)
            for summary in root_summaries.values():
                if summary not in why:
                    why.append(summary)
            updates["why_it_changed"] = why

            if field:
                entries = list(getattr(update, field))
                if change.materiality.explanation not in entries:
                    entries.append(change.materiality.explanation)
                updates[field] = entries

            if artifact.decision_required:
                decisions = list(update.decisions_required)
                statement = (
                    f"Decision required on {artifact.artifact_id}."
                )
                if statement not in decisions:
                    decisions.append(statement)
                updates["decisions_required"] = decisions

            evidence = list(update.evidence_ids)
            for eid in artifact.evidence_ids:
                if eid not in evidence:
                    evidence.append(eid)
            updates["evidence_ids"] = evidence

            by_project[project_id] = update.model_copy(update=updates)

    for project_id, update in by_project.items():
        if not update.what_changed:
            by_project[project_id] = update.model_copy(update={
                "no_action_required": [
                    f"{project_id}: state refreshed, nothing material "
                    f"changed."
                ],
            })

    updates = [by_project[p] for p in sorted(by_project)]
    for project_id in quiet_projects:
        updates.append(
            ProjectIntelligenceUpdate(
                project_id=project_id,
                revision_id=revision_id,
                no_action_required=[
                    f"{project_id}: unaffected by this change."
                ],
            )
        )
    return updates


def _build_portfolio_update(
    *,
    artifacts: list[RecomputedArtifact],
    changes: list[CausalIntelligenceChange],
    revision_id: str,
    affected_project_ids: list[str],
    quiet_projects: list[str],
) -> PortfolioIntelligenceUpdate | None:
    portfolio_artifacts = [
        a for a in artifacts if a.artifact_type in PORTFOLIO_ARTIFACT_TYPES
    ]
    if not portfolio_artifacts:
        return None

    change_by_artifact = {c.artifact_id: c for c in changes}
    update = PortfolioIntelligenceUpdate(
        revision_id=revision_id,
        affected_project_ids=list(affected_project_ids),
        projects_needing_no_action=list(quiet_projects),
    )
    updates: dict[str, object] = {}
    for artifact in portfolio_artifacts:
        change = change_by_artifact.get(artifact.artifact_id)
        evidence = list(updates.get("evidence_ids", update.evidence_ids))
        for eid in artifact.evidence_ids:
            if eid not in evidence:
                evidence.append(eid)
        updates["evidence_ids"] = evidence

        if artifact.artifact_type == A.PORTFOLIO_BOTTLENECKS:
            key = (
                "resolved_bottlenecks" if "resolved" in
                artifact.after_summary.lower() else "new_bottlenecks"
            )
            entries = list(updates.get(key, getattr(update, key)))
            entries.append(artifact.after_summary)
            updates[key] = entries
        elif artifact.artifact_type == A.PORTFOLIO_SCENARIO:
            entries = list(
                updates.get(
                    "scenario_distribution_changes",
                    update.scenario_distribution_changes,
                )
            )
            entries.append(artifact.after_summary)
            updates["scenario_distribution_changes"] = entries
        elif artifact.artifact_type == A.PORTFOLIO_RECOMMENDATION:
            entries = list(
                updates.get("priority_changes", update.priority_changes)
            )
            entries.append(artifact.after_summary)
            updates["priority_changes"] = entries
            if artifact.decision_required:
                decisions = list(
                    updates.get(
                        "decisions_required", update.decisions_required,
                    )
                )
                decisions.append(
                    f"Allocate the contended resource: {artifact.artifact_id}."
                )
                updates["decisions_required"] = decisions
        if change is not None and change.materiality.material:
            collisions = list(
                updates.get("collision_changes", update.collision_changes)
            )
            if change.materiality.explanation not in collisions:
                collisions.append(change.materiality.explanation)
            updates["collision_changes"] = collisions

    return update.model_copy(update=updates)


def _apply_states(
    *,
    store: RuntimeStateStore,
    artifacts: list[RecomputedArtifact],
    revision_id: str,
    now: datetime,
    affected_project_ids: list[str],
    material: bool,
) -> RuntimeStateStore:
    for project_id in affected_project_ids:
        project_artifacts = [
            a for a in artifacts
            if project_id in a.project_ids
            and a.artifact_type not in PORTFOLIO_ARTIFACT_TYPES
        ]
        state = store.project_states.get(project_id) or (
            ProjectIntelligenceRuntimeState(project_id=project_id)
        )
        store = store.with_project_state(
            apply_artifacts_to_project_state(
                state=state,
                artifacts=project_artifacts,
                revision_id=revision_id,
                now=now,
                material=material,
            )
        )
    portfolio_artifacts = [
        a for a in artifacts if a.artifact_type in PORTFOLIO_ARTIFACT_TYPES
    ]
    if portfolio_artifacts:
        store = store.with_portfolio_state(
            apply_artifacts_to_portfolio_state(
                state=store.portfolio_state,
                artifacts=portfolio_artifacts,
                revision_id=revision_id,
                now=now,
                affected_project_ids=affected_project_ids,
            )
        )
    return store


def _blocked_autonomous_actions(failures: list) -> list[str]:
    """A failed safety-critical artifact blocks autonomous action.

    Deciding against a scenario that did not build is exactly the kind of
    confident mistake this system exists to avoid.
    """
    return sorted({
        f"{f.artifact_type.value} unavailable; autonomous finalization is "
        f"blocked until it recomputes."
        for f in failures if f.is_safety_critical
    })


def _quiet_result(
    *,
    store: RuntimeStateStore,
    events_processed: int,
    events_deduplicated: int,
    reason: NoActionReason,
    lineage_only_event_ids: list[str] | None = None,
    contested: list[str] | None = None,
) -> tuple[LiveOrchestrationResult, RuntimeStateStore]:
    """A successful pass that produced no work and no interruption."""
    # Logged at info because silence is a decision the system made, and
    # "why did nothing happen?" is the question an operator asks first.
    logger.info(
        "orchestration pass took no action",
        extra={
            "organization_id": store.organization_id,
            "events_processed": events_processed,
            "events_deduplicated": events_deduplicated,
            "no_action_reason": reason.value,
        },
    )
    return (
        LiveOrchestrationResult(
            revision_id=(
                store.current_revision.id if store.current_revision else None
            ),
            events_processed=events_processed,
            events_deduplicated=events_deduplicated,
            complete=True,
            attention_actions=[RuntimeAttentionAction.SILENT],
            no_action_reasons=[reason],
            lineage_only_event_ids=list(lineage_only_event_ids or []),
            contested_subject_keys=list(contested or []),
            metrics=store.metrics or RuntimeMetrics(),
        ),
        store,
    )
