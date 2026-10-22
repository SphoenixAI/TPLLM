"""Coalesced incremental recomputation.

The naive live system is: event arrives, rerun everything, send a
notification. It is correct and unusable — one shared incident touching
fourteen projects becomes fourteen independent refresh chains, fourteen
portfolio rebuilds, and fourteen alerts about one thing.

This module does the opposite. It works out what each event actually
invalidates, propagates that through the dependency DAG, merges
duplicate and overlapping work, and produces one ordered plan for one
revision. Readiness is computed once. The scenario is computed once. The
portfolio is refreshed only if the topology says it must be.

Scope is the load-bearing idea: a KPI observation for one project must
not touch another project's decisions unless the topology proves a
shared dependency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from lltpm.runtime.dependency_graph import (
    build_intelligence_dependency_graph,
    downstream_artifacts,
    topological_order,
)
from lltpm.runtime.models import (
    PORTFOLIO_ARTIFACT_TYPES,
    ArtifactRecomputeRequest,
    IntelligenceArtifactType as A,
    RecomputeBatch,
    RecomputeFailure,
    RecomputeScope,
    RecomputedArtifact,
    RuntimeEvidenceEvent,
    RuntimeFanoutPolicy,
    RuntimeTopology,
)

# What each class of event directly invalidates, before any dependency
# propagation. Anything not listed invalidates nothing on its own — the
# default is to do no work, not to rebuild the organization.
DIRECT_INVALIDATIONS: dict[str, set[A]] = {
    "kpi_observation": {A.KPI_STATE},
    "kpi_definition_changed": {A.KPI_STATE, A.PROJECT_UNDERSTANDING},
    "decision_conclusive": {
        A.TEMPORAL_STATE, A.DECISION_STATE, A.WORKSTREAM_STATE,
    },
    "decision_proposed": {A.TEMPORAL_STATE, A.DECISION_STATE},
    "decision_reopened": {A.TEMPORAL_STATE, A.DECISION_STATE},
    "architecture_evidence": {
        A.PROJECT_UNDERSTANDING, A.WORKSTREAM_STATE, A.SCENARIO_LATTICE,
    },
    "blocker_opened": {A.TEMPORAL_STATE, A.WORKSTREAM_STATE},
    "blocker_resolved": {A.TEMPORAL_STATE, A.WORKSTREAM_STATE},
    "capacity_changed": {A.WORKSTREAM_STATE, A.PORTFOLIO_GRAPH},
    "shared_service_incident": {A.WORKSTREAM_STATE, A.PORTFOLIO_GRAPH},
    "authority_changed": {A.DECISION_STATE, A.PORTFOLIO_GRAPH},
    "commitment_changed": {A.TEMPORAL_STATE, A.WORKSTREAM_STATE},
    "identity_linked": {A.IDENTITY},
    "meeting_transcript": {A.TEMPORAL_STATE, A.PROJECT_UNDERSTANDING},
    # Explicitly nothing: a duplicate or cosmetic event is not news.
    "duplicate_message": set(),
    "typo_correction": set(),
    "chat_message": set(),
    "supporting_evidence": set(),
}

# Event classes whose reach is the whole shared dependency, not one
# project — these force a portfolio refresh.
PORTFOLIO_WIDENING_EVENT_CLASSES = frozenset({
    "capacity_changed",
    "shared_service_incident",
    "authority_changed",
})


def determine_direct_invalidations(
    *, event: RuntimeEvidenceEvent,
) -> set[A]:
    """What this event makes stale by itself.

    An unrecognized event class invalidates nothing. Guessing would mean
    a live system that rebuilds itself whenever a tool adds a new webhook
    type.
    """
    return set(DIRECT_INVALIDATIONS.get(event.event_class, set()))


def build_event_scope(
    *,
    event: RuntimeEvidenceEvent,
    topology: RuntimeTopology | None = None,
) -> RecomputeScope:
    """How far this one event reaches.

    A shared resource pulls in every project that depends on it — that is
    the whole point of the portfolio layer — but a project-local KPI
    observation stays local.
    """
    topology = topology or RuntimeTopology()
    project_ids = set(event.project_ids)
    project_ids |= topology.projects_touching(
        resource_ids=event.shared_resource_ids,
        kpi_ids=event.kpi_ids,
        entity_ids=event.canonical_entity_ids,
    )
    portfolio_required = (
        event.event_class in PORTFOLIO_WIDENING_EVENT_CLASSES
        and bool(event.shared_resource_ids or len(project_ids) > 1)
    )
    return RecomputeScope(
        project_ids=project_ids,
        workstream_ids=set(event.workstream_ids),
        kpi_ids=set(event.kpi_ids),
        decision_ids=set(event.decision_ids),
        canonical_entity_ids=set(event.canonical_entity_ids),
        shared_resource_ids=set(event.shared_resource_ids),
        portfolio_refresh_required=portfolio_required,
    )


def merge_requests(
    requests: list[ArtifactRecomputeRequest],
) -> list[ArtifactRecomputeRequest]:
    """Collapse work that would otherwise run twice.

    Two events in one batch can both invalidate readiness — one through
    a KPI, one through a workstream. Readiness is still one artifact and
    is computed once, over the union of both scopes.
    """
    merged: dict[A, ArtifactRecomputeRequest] = {}
    for request in requests:
        existing = merged.get(request.artifact_type)
        if existing is None:
            merged[request.artifact_type] = request.model_copy(deep=True)
            continue
        merged[request.artifact_type] = existing.model_copy(update={
            "scope": existing.scope.merged_with(request.scope),
            "reasons": _ordered_union(existing.reasons, request.reasons),
            "source_event_ids": _ordered_union(
                existing.source_event_ids, request.source_event_ids,
            ),
            "causal_event_ids": _ordered_union(
                existing.causal_event_ids, request.causal_event_ids,
            ),
            "lineage_only": existing.lineage_only and request.lineage_only,
        })
    return [merged[k] for k in sorted(merged, key=lambda a: a.value)]


def _ordered_union(first: list[str], second: list[str]) -> list[str]:
    combined = list(first)
    for item in second:
        if item not in combined:
            combined.append(item)
    return combined


def apply_fanout_policy(
    *,
    project_ids: set[str],
    policy: RuntimeFanoutPolicy,
    high_priority_project_ids: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Split a blast radius into immediate and deferred work.

    Deferred is returned, never dropped. A project that quietly vanished
    from scope would be a correctness bug wearing a performance
    optimization's clothes.
    """
    priority = set(high_priority_project_ids or [])
    ordered = sorted(project_ids)
    if policy.allow_priority_tiering and policy.high_priority_first:
        ordered = sorted(
            project_ids, key=lambda p: (p not in priority, p),
        )
    limit = policy.maximum_projects_per_immediate_batch
    immediate = sorted(ordered[:limit])
    deferred = sorted(ordered[limit:])
    return immediate, deferred


def build_recompute_batch(
    *,
    events: list[RuntimeEvidenceEvent],
    dependency_graph: dict[A, set[A]] | None = None,
    current_runtime_state: object = None,
    revision_id: str,
    created_at: datetime,
    topology: RuntimeTopology | None = None,
    fanout_policy: RuntimeFanoutPolicy | None = None,
    lineage_only_event_ids: set[str] | None = None,
) -> RecomputeBatch:
    """Turn a set of ordered events into one coalesced, ordered plan.

    Collect affected entities, find the directly stale artifacts,
    propagate through the dependency DAG, merge duplicate requests and
    overlapping scopes, decide whether the portfolio is genuinely
    involved, and order the result topologically.
    """
    dependency_graph = dependency_graph or build_intelligence_dependency_graph()
    topology = topology or RuntimeTopology()
    fanout_policy = fanout_policy or RuntimeFanoutPolicy()
    lineage_only_event_ids = lineage_only_event_ids or set()

    raw_requests: list[ArtifactRecomputeRequest] = []
    all_projects: set[str] = set()
    portfolio_required = False

    for event in events:
        direct = determine_direct_invalidations(event=event)
        if not direct:
            continue
        scope = build_event_scope(event=event, topology=topology)
        all_projects |= scope.project_ids
        portfolio_required = portfolio_required or (
            scope.portfolio_refresh_required
        )
        affected = downstream_artifacts(
            artifact_types=direct, graph=dependency_graph,
        )
        lineage_only = event.envelope_id in lineage_only_event_ids
        if lineage_only:
            # A superseded historical event still belongs in the record,
            # but it must not drag present-tense artifacts along with it.
            affected = {A.TEMPORAL_STATE} & affected or {A.TEMPORAL_STATE}
        for artifact_type in sorted(affected, key=lambda a: a.value):
            if (
                artifact_type in PORTFOLIO_ARTIFACT_TYPES
                and not scope.portfolio_refresh_required
            ):
                continue
            raw_requests.append(
                ArtifactRecomputeRequest(
                    artifact_type=artifact_type,
                    scope=scope,
                    reasons=[
                        f"{event.event_class} at "
                        f"{event.occurred_at.isoformat()}"
                        + (" (historical lineage only)" if lineage_only else "")
                    ],
                    source_event_ids=[event.envelope_id],
                    causal_event_ids=[event.causal_event_id],
                    lineage_only=lineage_only,
                )
            )

    requests = merge_requests(raw_requests)
    immediate, deferred = apply_fanout_policy(
        project_ids=all_projects,
        policy=fanout_policy,
        high_priority_project_ids=topology.high_priority_project_ids,
    )
    if deferred:
        immediate_set = set(immediate)
        requests = [
            r.model_copy(update={
                "scope": r.scope.model_copy(update={
                    "project_ids": r.scope.project_ids & immediate_set,
                }),
            })
            for r in requests
        ]

    ordered = topological_order(
        artifact_types={r.artifact_type for r in requests},
        graph=dependency_graph,
    )

    return RecomputeBatch(
        id=f"batch-{revision_id}",
        revision_id=revision_id,
        root_event_ids=[e.envelope_id for e in events],
        causal_event_ids=_ordered_union(
            [], [e.causal_event_id for e in events],
        ),
        affected_project_ids=set(immediate),
        requests=requests,
        topological_order=ordered,
        created_at=created_at,
        deferred_project_ids=deferred,
        deferral_reason=(
            f"Fan-out budget of "
            f"{fanout_policy.maximum_projects_per_immediate_batch} project(s) "
            f"exceeded; {len(deferred)} project(s) deferred to a later pass "
            f"and are listed rather than dropped."
            if deferred else None
        ),
    )


# ── Execution ────────────────────────────────────────────────────────


class ArtifactRecomputeError(RuntimeError):
    """A recompute that failed in a way the runtime must record."""

    def __init__(
        self,
        message: str,
        *,
        error_class: str = "recompute_error",
        retryable: bool = False,
    ):
        super().__init__(message)
        self.error_class = error_class
        self.retryable = retryable


@runtime_checkable
class IntelligenceServices(Protocol):
    """The port the orchestrator recomputes through.

    Deliberately narrow. The runtime schedules and sequences; it does not
    know how a launch readiness scorecard is built, and a host can wire
    whichever engines it actually has.
    """

    def recompute_artifact(
        self,
        *,
        request: ArtifactRecomputeRequest,
        revision_id: str,
    ) -> RecomputedArtifact:
        ...


class RecomputeExecution:
    """Outcome of running one batch."""

    def __init__(
        self,
        *,
        artifacts: list[RecomputedArtifact],
        skipped: list[ArtifactRecomputeRequest],
        failures: list[RecomputeFailure],
    ):
        self.artifacts = artifacts
        self.skipped = skipped
        self.failures = failures

    @property
    def succeeded(self) -> bool:
        return not self.failures

    @property
    def safety_critical_failed(self) -> bool:
        return any(f.is_safety_critical for f in self.failures)

    @property
    def failed_artifact_types(self) -> set[A]:
        return {f.artifact_type for f in self.failures}

    def artifact_of(self, artifact_type: A) -> RecomputedArtifact | None:
        return next(
            (a for a in self.artifacts if a.artifact_type == artifact_type),
            None,
        )


def execute_recompute_batch(
    *,
    batch: RecomputeBatch,
    runtime_services: IntelligenceServices,
    current_state: object = None,
    known_fingerprints: dict[str, str] | None = None,
    skip_unchanged: bool = True,
    dependency_graph: dict[A, set[A]] | None = None,
) -> RecomputeExecution:
    """Run the plan in dependency order, skipping genuinely unchanged work.

    Two forms of restraint apply. A request whose input fingerprint
    matches what was last computed is skipped outright. And once an
    artifact fails, everything downstream of it is skipped rather than
    computed against a hole — publishing a recommendation derived from a
    scenario that did not build would be worse than publishing nothing.
    """
    dependency_graph = dependency_graph or build_intelligence_dependency_graph()
    known_fingerprints = dict(known_fingerprints or {})

    artifacts: list[RecomputedArtifact] = []
    skipped: list[ArtifactRecomputeRequest] = []
    failures: list[RecomputeFailure] = []
    blocked_types: set[A] = set()

    by_type: dict[A, list[ArtifactRecomputeRequest]] = {}
    for request in batch.requests:
        by_type.setdefault(request.artifact_type, []).append(request)

    for artifact_type in batch.topological_order:
        for request in by_type.get(artifact_type, []):
            if artifact_type in blocked_types:
                skipped.append(request)
                continue
            key = request.request_key()
            if (
                skip_unchanged
                and request.input_fingerprint
                and known_fingerprints.get(key) == request.input_fingerprint
            ):
                skipped.append(request)
                continue
            try:
                artifact = runtime_services.recompute_artifact(
                    request=request, revision_id=batch.revision_id,
                )
            except ArtifactRecomputeError as error:
                failures.append(
                    RecomputeFailure(
                        artifact_type=artifact_type,
                        scope=request.scope,
                        error_class=error.error_class,
                        retryable=error.retryable,
                        explanation=str(error),
                    )
                )
                blocked_types |= downstream_artifacts(
                    artifact_types={artifact_type},
                    graph=dependency_graph,
                    include_self=False,
                )
                continue
            except Exception as error:  # noqa: BLE001 — recorded, not hidden
                failures.append(
                    RecomputeFailure(
                        artifact_type=artifact_type,
                        scope=request.scope,
                        error_class=type(error).__name__,
                        retryable=False,
                        explanation=str(error),
                    )
                )
                blocked_types |= downstream_artifacts(
                    artifact_types={artifact_type},
                    graph=dependency_graph,
                    include_self=False,
                )
                continue
            artifacts.append(artifact)
            known_fingerprints[key] = artifact.state_fingerprint

    return RecomputeExecution(
        artifacts=artifacts, skipped=skipped, failures=failures,
    )


def coalesced_request_count(
    raw: list[ArtifactRecomputeRequest],
    merged: list[ArtifactRecomputeRequest],
) -> int:
    return max(0, len(raw) - len(merged))
