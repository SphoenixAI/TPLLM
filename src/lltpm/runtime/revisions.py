"""Intelligence revisions and the runtime state store.

A live system's worst failure mode is not being wrong. It is being
*partially right and presenting it as whole*: a KPI from the newest
computation shown beside a scenario from two computations ago, under one
confident heading.

A revision is the fix. Artifacts are built into a candidate, the
candidate is completed only when everything its plan required actually
succeeded, and only a complete revision is promoted to current. A failed
candidate changes nothing — the previous revision keeps answering, which
is the honest outcome.

The store is copy-on-write. No transactional database is required, and a
serverless invocation can load it, compute, write it back, and exit.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from lltpm.runtime.models import (
    IntelligenceArtifactType,
    IntelligenceRevision,
    PortfolioIntelligenceRuntimeState,
    ProjectIntelligenceRuntimeState,
    RecomputedArtifact,
    RuntimeMetrics,
    build_revision_fingerprint,
)


class RevisionIncompleteError(ValueError):
    """Raised when an incomplete candidate is offered as current."""


def build_candidate_revision(
    *,
    revision_index: int,
    evidence_cutoff_at: datetime,
    created_at: datetime,
    project_ids: list[str],
    root_evidence_ids: list[str],
    required_artifact_types: list[IntelligenceArtifactType],
    causal_event_ids: list[str] | None = None,
    parent_revision_id: str | None = None,
    organization_id: str | None = None,
) -> IntelligenceRevision:
    """Open a candidate. It is not the answer yet, and says so."""
    return IntelligenceRevision(
        id=f"revision-{revision_index}",
        organization_id=organization_id,
        project_ids=sorted(set(project_ids)),
        evidence_cutoff_at=evidence_cutoff_at,
        created_at=created_at,
        root_evidence_ids=sorted(set(root_evidence_ids)),
        parent_revision_id=parent_revision_id,
        state_fingerprint=build_revision_fingerprint(
            evidence_cutoff_at=evidence_cutoff_at,
            project_ids=project_ids,
            root_evidence_ids=root_evidence_ids,
            required_artifact_types=required_artifact_types,
        ),
        complete=False,
        causal_event_ids=sorted(set(causal_event_ids or [])),
        required_artifact_types=list(required_artifact_types),
        incomplete_reason="Candidate revision; artifacts not yet computed.",
    )


def complete_revision(
    *,
    revision: IntelligenceRevision,
    artifacts: list[RecomputedArtifact],
    failures: list[object] | None = None,
) -> IntelligenceRevision:
    """Mark a candidate complete only if it genuinely is.

    Every artifact the plan required must have been produced, and nothing
    may have failed. A revision that quietly omitted a required artifact
    would look coherent and be a lie.
    """
    produced = {a.artifact_type for a in artifacts}
    missing = [
        t for t in revision.required_artifact_types if t not in produced
    ]
    if failures:
        return revision.model_copy(update={
            "complete": False,
            "published_artifact_ids": sorted(a.artifact_id for a in artifacts),
            "incomplete_reason": (
                f"{len(failures)} artifact recomputation(s) failed; this "
                f"revision is not coherent and must not be published."
            ),
        })
    if missing:
        return revision.model_copy(update={
            "complete": False,
            "published_artifact_ids": sorted(a.artifact_id for a in artifacts),
            "incomplete_reason": (
                f"Required artifact(s) missing: "
                f"{sorted(t.value for t in missing)}."
            ),
        })
    return revision.model_copy(update={
        "complete": True,
        "published_artifact_ids": sorted(a.artifact_id for a in artifacts),
        "incomplete_reason": None,
    })


def artifacts_share_revision(
    artifacts: list[RecomputedArtifact],
    *,
    revision_id: str,
) -> bool:
    """Every published artifact must belong to the revision it is shown
    under."""
    return all(a.revision_id == revision_id for a in artifacts)


def mismatched_revision_artifacts(
    artifacts: list[RecomputedArtifact], *, revision_id: str,
) -> list[str]:
    return sorted(
        a.artifact_id for a in artifacts if a.revision_id != revision_id
    )


class RuntimeStateStore(BaseModel):
    """Everything the runtime remembers between invocations.

    Copy-on-write throughout: every mutation returns a new store, so a
    failed pass cannot leave the previous state half-modified.
    """
    organization_id: str | None = None
    current_revision: IntelligenceRevision | None = None
    revision_history: list[IntelligenceRevision] = Field(default_factory=list)
    project_states: dict[str, ProjectIntelligenceRuntimeState] = Field(
        default_factory=dict
    )
    portfolio_state: PortfolioIntelligenceRuntimeState | None = None
    processed_idempotency_keys: list[str] = Field(default_factory=list)
    artifact_fingerprints: dict[str, str] = Field(default_factory=dict)
    latest_occurred_at: datetime | None = None
    subject_latest_occurred_at: dict[str, datetime] = Field(
        default_factory=dict
    )
    contested_subject_keys: list[str] = Field(default_factory=list)
    research_rounds_by_causal_event: dict[str, int] = Field(
        default_factory=dict
    )
    metrics: RuntimeMetrics = Field(default_factory=RuntimeMetrics)
    revision_index: int = 0

    @property
    def processed_keys(self) -> set[str]:
        return set(self.processed_idempotency_keys)

    def promote(
        self, revision: IntelligenceRevision,
    ) -> "RuntimeStateStore":
        """Make a complete revision current.

        Refuses an incomplete candidate. The prior revision continues to
        answer, which is the safe outcome when a recompute failed.
        """
        if not revision.complete:
            raise RevisionIncompleteError(
                f"Revision {revision.id} is not complete "
                f"({revision.incomplete_reason}); the previous revision "
                f"remains current."
            )
        history = list(self.revision_history)
        if revision.id not in {r.id for r in history}:
            history.append(revision)
        return self.model_copy(update={
            "current_revision": revision,
            "revision_history": history,
        })

    def record_failed_candidate(
        self, revision: IntelligenceRevision,
    ) -> "RuntimeStateStore":
        """Keep the failed candidate visible without promoting it."""
        history = list(self.revision_history)
        if revision.id not in {r.id for r in history}:
            history.append(revision)
        return self.model_copy(update={
            "revision_history": history,
            "metrics": self.metrics.incremented(failed_revisions=1),
        })

    def with_processed_keys(self, keys: list[str]) -> "RuntimeStateStore":
        combined = list(self.processed_idempotency_keys)
        for key in keys:
            if key and key not in combined:
                combined.append(key)
        return self.model_copy(update={"processed_idempotency_keys": combined})

    def with_fingerprints(
        self, fingerprints: dict[str, str],
    ) -> "RuntimeStateStore":
        merged = dict(self.artifact_fingerprints)
        merged.update(fingerprints)
        return self.model_copy(update={"artifact_fingerprints": merged})

    def with_temporal_watermarks(
        self,
        *,
        latest_occurred_at: datetime | None,
        subject_latest: dict[str, datetime],
    ) -> "RuntimeStateStore":
        watermark = self.latest_occurred_at
        if latest_occurred_at is not None and (
            watermark is None or latest_occurred_at > watermark
        ):
            watermark = latest_occurred_at
        subjects = dict(self.subject_latest_occurred_at)
        for key, moment in subject_latest.items():
            current = subjects.get(key)
            if current is None or moment > current:
                subjects[key] = moment
        return self.model_copy(update={
            "latest_occurred_at": watermark,
            "subject_latest_occurred_at": subjects,
        })

    def with_project_state(
        self, state: ProjectIntelligenceRuntimeState,
    ) -> "RuntimeStateStore":
        states = dict(self.project_states)
        states[state.project_id] = state
        return self.model_copy(update={"project_states": states})

    def with_portfolio_state(
        self, state: PortfolioIntelligenceRuntimeState,
    ) -> "RuntimeStateStore":
        return self.model_copy(update={"portfolio_state": state})

    def with_metrics(self, **counters: int) -> "RuntimeStateStore":
        return self.model_copy(update={
            "metrics": self.metrics.incremented(**counters),
        })

    def next_revision_index(self) -> int:
        return self.revision_index + 1

    def with_revision_index(self, index: int) -> "RuntimeStateStore":
        return self.model_copy(update={"revision_index": index})

    def with_contested_subjects(
        self, keys: list[str],
    ) -> "RuntimeStateStore":
        combined = list(self.contested_subject_keys)
        for key in keys:
            if key not in combined:
                combined.append(key)
        return self.model_copy(update={"contested_subject_keys": combined})

    def with_research_round(self, causal_event_id: str) -> "RuntimeStateStore":
        rounds = dict(self.research_rounds_by_causal_event)
        rounds[causal_event_id] = rounds.get(causal_event_id, 0) + 1
        return self.model_copy(update={
            "research_rounds_by_causal_event": rounds,
            "metrics": self.metrics.incremented(
                self_research_rounds_triggered=1,
            ),
        })


def apply_artifacts_to_project_state(
    *,
    state: ProjectIntelligenceRuntimeState,
    artifacts: list[RecomputedArtifact],
    revision_id: str,
    now: datetime,
    material: bool = False,
) -> ProjectIntelligenceRuntimeState:
    """Point a project's runtime state at the newly published artifacts."""
    A = IntelligenceArtifactType
    updates: dict[str, object] = {
        "active_revision_id": revision_id,
        "last_refresh_at": now,
    }
    if material:
        updates["last_material_change_at"] = now

    singles = {
        A.TEMPORAL_STATE: "temporal_state_id",
        A.PROJECT_UNDERSTANDING: "understanding_snapshot_id",
        A.LAUNCH_READINESS: "launch_readiness_id",
        A.SCENARIO_LATTICE: "scenario_lattice_id",
        A.AI_QUALITY: "ai_quality_report_id",
    }
    multiples = {
        A.WORKSTREAM_STATE: "workstream_state_ids",
        A.KPI_STATE: "kpi_state_ids",
        A.DECISION_STATE: "active_decision_ids",
        A.DECISION_CAPSULE: "decision_capsule_ids",
        A.DECISION_RECOMMENDATION: "recommendation_ids",
        A.SCENARIO_DECISION_RECOMMENDATION: "scenario_recommendation_ids",
    }

    for artifact in artifacts:
        if artifact.artifact_type in singles:
            updates[singles[artifact.artifact_type]] = artifact.artifact_id
        elif artifact.artifact_type in multiples:
            field = multiples[artifact.artifact_type]
            current = list(updates.get(field, getattr(state, field)))
            if artifact.artifact_id not in current:
                current.append(artifact.artifact_id)
            updates[field] = current

    updated = state.model_copy(update=updates)
    return updated.model_copy(update={
        "state_fingerprint": _project_fingerprint(updated),
    })


def _project_fingerprint(state: ProjectIntelligenceRuntimeState) -> str:
    from lltpm.runtime.models import build_project_state_fingerprint

    return build_project_state_fingerprint(
        project_id=state.project_id,
        understanding_snapshot_id=state.understanding_snapshot_id,
        workstream_state_ids=state.workstream_state_ids,
        kpi_state_ids=state.kpi_state_ids,
        active_decision_ids=state.active_decision_ids,
        launch_readiness_id=state.launch_readiness_id,
    )


def apply_artifacts_to_portfolio_state(
    *,
    state: PortfolioIntelligenceRuntimeState | None,
    artifacts: list[RecomputedArtifact],
    revision_id: str,
    now: datetime,
    affected_project_ids: list[str],
) -> PortfolioIntelligenceRuntimeState:
    A = IntelligenceArtifactType
    state = state or PortfolioIntelligenceRuntimeState()
    updates: dict[str, object] = {
        "active_revision_id": revision_id,
        "last_refresh_at": now,
        "affected_project_ids": sorted(set(affected_project_ids)),
    }
    for artifact in artifacts:
        if artifact.artifact_type == A.PORTFOLIO_GRAPH:
            updates["portfolio_graph_id"] = artifact.artifact_id
        elif artifact.artifact_type == A.PORTFOLIO_BOTTLENECKS:
            updates["bottleneck_ids"] = [artifact.artifact_id]
        elif artifact.artifact_type == A.PORTFOLIO_SCENARIO:
            updates["portfolio_scenario_id"] = artifact.artifact_id
        elif artifact.artifact_type == A.PORTFOLIO_RECOMMENDATION:
            current = list(state.portfolio_recommendation_ids)
            if artifact.artifact_id not in current:
                current.append(artifact.artifact_id)
            updates["portfolio_recommendation_ids"] = current

    updated = state.model_copy(update=updates)
    from lltpm.runtime.models import build_portfolio_state_fingerprint

    return updated.model_copy(update={
        "state_fingerprint": build_portfolio_state_fingerprint(
            project_ids=updated.affected_project_ids,
            bottleneck_ids=updated.bottleneck_ids,
            capacity_signature=updated.portfolio_graph_id or "",
        ),
    })
