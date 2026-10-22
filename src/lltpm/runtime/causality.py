"""Causal change tracking and notification compression.

One incident in Platform Architecture can move a KPI, two scenarios, a
portfolio bottleneck, a project recommendation, and a portfolio priority.
Reporting that as five alerts is technically complete and practically
useless — the reader has to reconstruct the single cause that produced
all of it.

This module keeps the ancestry attached to every derived change and then
compresses changes that share a cause into one bundle. Grouping is by
causal identity only: the same causal event, shared root evidence, or a
parent/child link. Never by timestamp — two unrelated things happening
in the same minute are two things.
"""

from __future__ import annotations

from lltpm.runtime.dependency_graph import describe_dependency_edges
from lltpm.runtime.models import (
    CausalIntelligenceChange,
    CausalUpdateBundle,
    IntelligenceArtifactType,
    IntelligenceMaterialityAssessment,
    MaterialityReason,
    RecomputedArtifact,
    RuntimeEvidenceEvent,
)

_SEVERITY_ORDER = ["none", "normal", "elevated", "severe", "critical"]

_SEVERE_REASONS = frozenset({
    MaterialityReason.SEVERE_FAILURE_RISK_CHANGE,
    MaterialityReason.NEW_PORTFOLIO_BOTTLENECK,
    MaterialityReason.AUTONOMOUS_ACTION_BLOCKED,
})


def build_causal_change(
    *,
    artifact: RecomputedArtifact,
    materiality: IntelligenceMaterialityAssessment,
    causal_event_id: str,
    root_evidence_ids: list[str],
    parent_change_ids: list[str] | None = None,
    origin_artifact_type: IntelligenceArtifactType | None = None,
) -> CausalIntelligenceChange:
    """Record one change with the chain that produced it.

    ``dependency_edges`` names the path the invalidation actually
    travelled, so "why did LLTPM recompute this?" has a structural
    answer rather than a plausible story.
    """
    edges: list[str] = []
    if origin_artifact_type is not None:
        edges = describe_dependency_edges(
            source=origin_artifact_type, target=artifact.artifact_type,
        )
    return CausalIntelligenceChange(
        id=f"change-{artifact.revision_id}-{artifact.artifact_id}",
        causal_event_id=causal_event_id,
        root_evidence_ids=sorted(set(root_evidence_ids)),
        artifact_type=artifact.artifact_type,
        artifact_id=artifact.artifact_id,
        before_summary=artifact.before_summary,
        after_summary=artifact.after_summary,
        materiality=materiality,
        parent_change_ids=list(parent_change_ids or []),
        affected_project_ids=list(artifact.project_ids),
        revision_id=artifact.revision_id,
        dependency_edges=edges,
    )


def _severity_of(change: CausalIntelligenceChange) -> str:
    if any(r in _SEVERE_REASONS for r in change.materiality.reasons):
        return "severe"
    if change.materiality.material:
        return "elevated"
    return "normal"


def _highest_severity(changes: list[CausalIntelligenceChange]) -> str:
    severities = [_severity_of(c) for c in changes]
    if not severities:
        return "none"
    return max(severities, key=_SEVERITY_ORDER.index)


class _Groups:
    """Union-find over causal identity."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, key: str) -> None:
        self.parent.setdefault(key, key)

    def find(self, key: str) -> str:
        self.add(key)
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[max(root_a, root_b)] = min(root_a, root_b)


def bundle_causally_related_changes(
    changes: list[CausalIntelligenceChange],
    *,
    root_summaries: dict[str, str] | None = None,
) -> list[CausalUpdateBundle]:
    """Compress derived changes into one bundle per root cause.

    Changes join the same bundle when they share a causal event, share
    root evidence, or are linked by ancestry. Simultaneity is not a
    relationship — unrelated events stay in separate bundles no matter
    how close their timestamps are.
    """
    if not changes:
        return []
    root_summaries = root_summaries or {}

    groups = _Groups()
    by_id = {c.id: c for c in changes}
    for change in changes:
        groups.add(change.id)
    evidence_owner: dict[str, str] = {}
    causal_owner: dict[str, str] = {}

    for change in sorted(changes, key=lambda c: c.id):
        owner = causal_owner.setdefault(change.causal_event_id, change.id)
        groups.union(owner, change.id)
        for evidence_id in change.root_evidence_ids:
            evidence_group = evidence_owner.setdefault(evidence_id, change.id)
            groups.union(evidence_group, change.id)
        for parent_id in change.parent_change_ids:
            if parent_id in by_id:
                groups.union(parent_id, change.id)

    clustered: dict[str, list[CausalIntelligenceChange]] = {}
    for change in changes:
        clustered.setdefault(groups.find(change.id), []).append(change)

    bundles: list[CausalUpdateBundle] = []
    for root_key in sorted(clustered):
        cluster = sorted(clustered[root_key], key=lambda c: c.id)
        material = [c for c in cluster if c.materiality.material]
        causal_event_ids = sorted({c.causal_event_id for c in cluster})
        evidence_ids = sorted({
            e for c in cluster for e in c.root_evidence_ids
        })
        project_ids = sorted({
            p for c in cluster for p in c.affected_project_ids
        })
        decision_required = any(
            MaterialityReason.DECISION_REQUIRED in c.materiality.reasons
            for c in cluster
        )
        bundles.append(
            CausalUpdateBundle(
                id=f"bundle-{causal_event_ids[0]}",
                causal_event_ids=causal_event_ids,
                root_summary=_root_summary(
                    causal_event_ids=causal_event_ids,
                    root_summaries=root_summaries,
                    cluster=cluster,
                ),
                material_changes=material,
                affected_project_ids=project_ids,
                highest_severity=_highest_severity(material),
                decision_required=decision_required,
                human_attention_required=any(
                    c.materiality.human_attention_required for c in cluster
                ),
                root_evidence_ids=evidence_ids,
                all_change_ids=[c.id for c in cluster],
                low_confidence_qualifier=any(
                    c.materiality.low_confidence_qualifier for c in cluster
                ),
            )
        )
    return sorted(bundles, key=lambda b: b.id)


def _root_summary(
    *,
    causal_event_ids: list[str],
    root_summaries: dict[str, str],
    cluster: list[CausalIntelligenceChange],
) -> str:
    for causal_event_id in causal_event_ids:
        summary = root_summaries.get(causal_event_id)
        if summary:
            return summary
    return (
        f"One root cause produced {len(cluster)} derived change(s) across "
        f"{len({p for c in cluster for p in c.affected_project_ids})} "
        f"project(s)."
    )


def build_root_summaries(
    events: list[RuntimeEvidenceEvent],
) -> dict[str, str]:
    """A short human sentence per causal event, for the bundle header."""
    summaries: dict[str, str] = {}
    for event in events:
        summaries.setdefault(
            event.causal_event_id,
            f"{event.event_class.replace('_', ' ').capitalize()} at "
            f"{event.occurred_at.isoformat()}"
            + (
                f" affecting {', '.join(sorted(event.shared_resource_ids))}"
                if event.shared_resource_ids else ""
            )
            + ".",
        )
    return summaries


def changes_for_project(
    *, bundle: CausalUpdateBundle, project_id: str,
) -> list[CausalIntelligenceChange]:
    return [
        c for c in bundle.material_changes
        if project_id in c.affected_project_ids
    ]


def causal_ancestry(
    *,
    change_id: str,
    changes: list[CausalIntelligenceChange],
) -> list[str]:
    """Walk parent links back to the root, nearest ancestor first."""
    by_id = {c.id: c for c in changes}
    ancestry: list[str] = []
    frontier = list(by_id.get(change_id).parent_change_ids) if (
        change_id in by_id
    ) else []
    seen: set[str] = set()
    while frontier:
        current = frontier.pop(0)
        if current in seen or current not in by_id:
            continue
        seen.add(current)
        ancestry.append(current)
        frontier.extend(by_id[current].parent_change_ids)
    return ancestry


class RuntimeChangeExplanation(dict):
    """A plain mapping so callers can render or serialize it freely."""


def explain_runtime_change(
    change_id: str,
    *,
    changes: list[CausalIntelligenceChange],
    bundles: list[CausalUpdateBundle] | None = None,
    root_summaries: dict[str, str] | None = None,
) -> RuntimeChangeExplanation:
    """Answer, for one surfaced change, why it exists.

    Which event caused it, which dependency edges carried it, what
    changed, why it was judged material, and who was asked to look.
    """
    by_id = {c.id: c for c in changes}
    change = by_id.get(change_id)
    if change is None:
        return RuntimeChangeExplanation({
            "change_id": change_id,
            "found": False,
            "explanation": "No such runtime change was recorded.",
        })

    bundle = next(
        (
            b for b in (bundles or [])
            if change_id in b.all_change_ids
        ),
        None,
    )
    root_summary = (root_summaries or {}).get(change.causal_event_id)

    return RuntimeChangeExplanation({
        "change_id": change_id,
        "found": True,
        "artifact_type": change.artifact_type.value,
        "artifact_id": change.artifact_id,
        "revision_id": change.revision_id,
        "causal_event_id": change.causal_event_id,
        "root_evidence_ids": list(change.root_evidence_ids),
        "root_summary": root_summary or (bundle.root_summary if bundle else ""),
        "dependency_edges": list(change.dependency_edges),
        "ancestry": causal_ancestry(change_id=change_id, changes=changes),
        "before_summary": change.before_summary,
        "after_summary": change.after_summary,
        "material": change.materiality.material,
        "materiality_reasons": [
            r.value for r in change.materiality.reasons
        ],
        "materiality_explanation": change.materiality.explanation,
        "human_attention_required": (
            change.materiality.human_attention_required
        ),
        "affected_project_ids": list(change.affected_project_ids),
        "bundle_id": bundle.id if bundle else None,
        "explanation": (
            f"{change.artifact_type.value} was recomputed because of "
            f"{change.causal_event_id}"
            + (
                f", propagating via {' -> '.join(change.dependency_edges)}"
                if change.dependency_edges else ""
            )
            + f". {change.materiality.explanation}"
        ),
    })
