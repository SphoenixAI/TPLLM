"""Workstream health computation and board projection.

Health is a projection of evidence — unresolved decisions, blocked asks,
missed commitments, contested scope, structural signals — never a color
a person paints by hand. Manual annotations can participate only as
evidence with provenance.
"""

from __future__ import annotations

from datetime import datetime

from tpllm.coordination.decisions import (
    decision_touches_workstream,
    get_latest_conclusive_decision,
    is_decision_unresolved,
)
from tpllm.coordination.models import (
    ActionRequest,
    OrganizationalActorRef,
    OrganizationalDecisionRecord,
    WorkstreamCommitment,
    WorkstreamHealth,
    WorkstreamKeyDate,
    WorkstreamObjective,
    WorkstreamState,
    WorkstreamType,
)
from tpllm.coordination.requests import (
    blocked_requests_for_workstream,
    is_overdue,
    open_requests_for_workstream,
)
from tpllm.intelligence.launch_readiness import LaunchReadinessSnapshot
from tpllm.intelligence.project_understanding import ProjectUnderstandingSnapshot


_HEALTH_RANK = {
    WorkstreamHealth.ON_TRACK: 0,
    WorkstreamHealth.WATCH: 1,
    WorkstreamHealth.AT_RISK: 2,
    WorkstreamHealth.BLOCKED: 3,
    WorkstreamHealth.UNKNOWN: 1,  # unknown ranks like watch for reporting
}

_NEGATIVE_SIGNAL_KINDS = {
    "integration_failure": 2.0,
    "kpi_breach": 2.0,
    "new_blocker": 2.0,
    "dependency_risk": 1.5,
    "missed_commitment": 2.0,
    "hard_blocker": 3.0,
    "contested_scope": 1.5,
}

_POSITIVE_SIGNAL_KINDS = {
    "blocker_resolved": 1.5,
    "positive_validation": 1.0,
    "kpi_recovered": 1.5,
}

_MET_COMMITMENT_STATUSES = {"met", "delivered", "resolved", "complete", "completed"}


def health_rank(health: WorkstreamHealth) -> int:
    """Ordering for 'worse than' comparisons in tests and reporting."""
    return _HEALTH_RANK[health]


def _signal_kind(signal: object) -> str:
    return str(
        getattr(signal, "signal_kind", None) or getattr(signal, "kind", "") or ""
    )


def compute_workstream_health(
    *,
    understanding: ProjectUnderstandingSnapshot,
    readiness: LaunchReadinessSnapshot | None,
    workstream_id: str,
    decisions: list[OrganizationalDecisionRecord],
    action_requests: list[ActionRequest],
    structural_signals: list[object],
    commitments: list[WorkstreamCommitment],
    as_of: datetime,
) -> tuple[WorkstreamHealth, float, str]:
    """Evidence-weighted health. Returns (health, confidence,
    key_status_signal). Deterministic for the same inputs."""
    risk = 0.0
    positive = 0.0
    weighted_signals: list[tuple[float, str]] = []
    hard_block = False
    evidence_inputs = 0

    unresolved = [
        d
        for d in decisions
        if decision_touches_workstream(d, workstream_id) and is_decision_unresolved(d)
    ]
    for decision in unresolved:
        evidence_inputs += 1
        risk += 2.0
        weighted_signals.append(
            (2.0, f"Unresolved decision: {decision.topic} — {decision.question}")
        )

    open_reqs = open_requests_for_workstream(action_requests, workstream_id)
    for request in blocked_requests_for_workstream(action_requests, workstream_id):
        evidence_inputs += 1
        risk += 2.0
        hard_block = hard_block or request.request_type.value in (
            "dependency",
            "approval",
            "decision",
        )
        weighted_signals.append(
            (2.0, f"Blocked ask: {request.requested_action} "
                  f"({request.blocking_reason or 'blocked'})")
        )
    for request in open_reqs:
        if is_overdue(request, as_of):
            evidence_inputs += 1
            risk += 1.5
            weighted_signals.append(
                (1.5, f"Overdue ask: {request.requested_action}")
            )

    for commitment in commitments:
        if commitment.workstream_id != workstream_id:
            continue
        evidence_inputs += 1
        if (
            commitment.target_date is not None
            and commitment.target_date < as_of
            and commitment.status.lower() not in _MET_COMMITMENT_STATUSES
        ):
            risk += 2.0
            weighted_signals.append(
                (2.0, f"Missed commitment: {commitment.description}")
            )

    for contradiction in understanding.contradictions:
        if contradiction.resolved:
            continue
        evidence_inputs += 1
        risk += 1.5
        weighted_signals.append(
            (1.5, f"Contested evidence: {contradiction.explanation}")
        )

    if readiness is not None:
        for blocker in readiness.scorecard.blockers:
            if blocker.resolved:
                continue
            if blocker.severity.value == "critical":
                evidence_inputs += 1
                risk += 1.0
                weighted_signals.append(
                    (1.0, f"Launch blocker: {blocker.title}")
                )

    for signal in structural_signals:
        kind = _signal_kind(signal)
        description = str(
            getattr(signal, "description", None)
            or getattr(signal, "rationale", None)
            or kind
        )
        if kind in _NEGATIVE_SIGNAL_KINDS:
            evidence_inputs += 1
            weight = _NEGATIVE_SIGNAL_KINDS[kind]
            risk += weight
            hard_block = hard_block or kind == "hard_blocker"
            weighted_signals.append((weight, description))
        elif kind in _POSITIVE_SIGNAL_KINDS:
            evidence_inputs += 1
            positive += _POSITIVE_SIGNAL_KINDS[kind]
            weighted_signals.append(
                (0.5, f"Improving: {description}")
            )

    if evidence_inputs == 0 and not understanding.evidence_ids:
        return (
            WorkstreamHealth.UNKNOWN,
            0.2,
            "No evidence available for this workstream yet.",
        )

    net = risk - positive
    if hard_block and net >= 2.0:
        health = WorkstreamHealth.BLOCKED
    elif net >= 3.0:
        health = WorkstreamHealth.AT_RISK
    elif net >= 1.0:
        health = WorkstreamHealth.WATCH
    else:
        health = WorkstreamHealth.ON_TRACK

    freshness = max(0.4, understanding.coverage.evidence_freshness)
    confidence = min(1.0, (0.4 + 0.06 * evidence_inputs) * freshness)

    if weighted_signals:
        weighted_signals.sort(key=lambda s: (-s[0], s[1]))
        key_signal = weighted_signals[0][1]
    else:
        key_signal = "No material risk signals; progress on track."

    return health, confidence, key_signal


def build_workstream_state(
    *,
    workstream_id: str,
    project_id: str,
    vertical: str,
    feature_or_scope: str,
    workstream_type: WorkstreamType,
    objective: WorkstreamObjective,
    key_dates: list[WorkstreamKeyDate],
    understanding: ProjectUnderstandingSnapshot,
    readiness: LaunchReadinessSnapshot | None,
    decisions: list[OrganizationalDecisionRecord],
    action_requests: list[ActionRequest],
    commitments: list[WorkstreamCommitment],
    structural_signals: list[object],
    kpi_ids: list[str],
    wins: list[str],
    primary_contact: OrganizationalActorRef | None,
    previous_state: WorkstreamState | None,
    as_of: datetime,
) -> WorkstreamState:
    """Board projection: vertical, feature, key dates, current and
    previous rating, key status signal, DRI, latest conclusive decision,
    product value, KPI links — all evidence-backed and deterministic."""
    health, confidence, key_signal = compute_workstream_health(
        understanding=understanding,
        readiness=readiness,
        workstream_id=workstream_id,
        decisions=decisions,
        action_requests=action_requests,
        structural_signals=structural_signals,
        commitments=commitments,
        as_of=as_of,
    )

    latest = get_latest_conclusive_decision(
        decisions=decisions, workstream_id=workstream_id, as_of=as_of
    )
    open_reqs = open_requests_for_workstream(action_requests, workstream_id)
    blocked = blocked_requests_for_workstream(action_requests, workstream_id)

    evidence_ids: list[str] = []
    for source in (
        objective.evidence_ids,
        *[d.evidence_ids for d in key_dates],
        *[d.evidence_ids for d in decisions
          if decision_touches_workstream(d, workstream_id)],
        *[r.evidence_ids for r in open_reqs],
        *[c.evidence_ids for c in commitments
          if c.workstream_id == workstream_id],
    ):
        for eid in source:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    return WorkstreamState(
        id=f"ws-state-{workstream_id}-{as_of.isoformat()}",
        project_id=project_id,
        workstream_id=workstream_id,
        vertical=vertical,
        feature_or_scope=feature_or_scope,
        workstream_type=workstream_type,
        objective=objective,
        key_dates=sorted(key_dates, key=lambda d: (d.date, d.id)),
        current_health=health,
        previous_health=(
            previous_state.current_health if previous_state else None
        ),
        health_confidence=confidence,
        key_status_signal=key_signal,
        primary_contact=primary_contact,
        latest_conclusive_decision_id=latest.id if latest else None,
        open_action_request_ids=[r.id for r in open_reqs],
        blocker_ids=[r.id for r in blocked],
        kpi_ids=list(kpi_ids),
        commitment_ids=[
            c.id for c in commitments if c.workstream_id == workstream_id
        ],
        wins=list(wins),
        product_value=objective.product_value,
        evidence_ids=evidence_ids,
        as_of=as_of,
    )
