"""Decision lifecycle logic: the latest conclusive decision beats the
latest message.

A conclusive decision is only displaced by an explicit superseding
decision or an explicit reopening — never by a newer chat opinion.
"""

from __future__ import annotations

from datetime import datetime

from lltpm.coordination.models import (
    OrganizationalDecisionRecord,
    OrganizationalDecisionStatus,
)

_UNRESOLVED_STATUSES = {
    OrganizationalDecisionStatus.PROPOSED,
    OrganizationalDecisionStatus.UNDER_DISCUSSION,
    OrganizationalDecisionStatus.TENTATIVE,
    OrganizationalDecisionStatus.REOPENED,
}


def decision_touches_workstream(
    decision: OrganizationalDecisionRecord, workstream_id: str
) -> bool:
    return (
        decision.workstream_id == workstream_id
        or workstream_id in decision.affected_workstream_ids
    )


def is_decision_unresolved(decision: OrganizationalDecisionRecord) -> bool:
    return decision.status in _UNRESOLVED_STATUSES


def get_latest_conclusive_decision(
    *,
    decisions: list[OrganizationalDecisionRecord],
    workstream_id: str,
    as_of: datetime,
) -> OrganizationalDecisionRecord | None:
    """Select the authoritative decision for a workstream.

    Rules: future decisions are ignored; proposed/tentative discussion is
    ignored; superseded decisions are ignored unless their superseding
    decision has been reopened (which voids the supersession while the
    question is open); the most recent effective conclusive record wins.
    A newer chat statement never overrides a conclusive decision — chat
    is evidence, not a decision record.
    """
    by_id = {d.id: d for d in decisions}
    candidates: list[OrganizationalDecisionRecord] = []

    for decision in decisions:
        if not decision_touches_workstream(decision, workstream_id):
            continue
        effective = decision.effective_at or decision.decided_at
        if effective is None or effective > as_of:
            continue
        if decision.status == OrganizationalDecisionStatus.CONCLUSIVE:
            if decision.superseded_by_decision_id is not None:
                superseder = by_id.get(decision.superseded_by_decision_id)
                if superseder is None or superseder.status != (
                    OrganizationalDecisionStatus.REOPENED
                ):
                    continue  # stays superseded
                # Superseder reopened → supersession void; original stands.
            candidates.append(decision)
        elif (
            decision.status == OrganizationalDecisionStatus.SUPERSEDED
            and decision.conclusion
            and decision.superseded_by_decision_id is not None
        ):
            superseder = by_id.get(decision.superseded_by_decision_id)
            if superseder is not None and superseder.status == (
                OrganizationalDecisionStatus.REOPENED
            ):
                # The decision that displaced this one is open again —
                # the prior conclusive record stands until re-decided.
                candidates.append(decision)

    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda d: ((d.effective_at or d.decided_at), d.id),
        reverse=True,
    )[0]


def supersede_decision(
    *,
    old: OrganizationalDecisionRecord,
    new: OrganizationalDecisionRecord,
) -> tuple[OrganizationalDecisionRecord, OrganizationalDecisionRecord]:
    """Link an explicit superseding decision. Returns updated copies —
    the old record survives as history, never deleted."""
    updated_old = old.model_copy(
        update={
            "status": OrganizationalDecisionStatus.SUPERSEDED,
            "superseded_by_decision_id": new.id,
        }
    )
    updated_new = new.model_copy(update={"supersedes_decision_id": old.id})
    return updated_old, updated_new


def reopen_decision(
    *,
    decision: OrganizationalDecisionRecord,
    reason: str,
    evidence_ids: list[str] | None = None,
) -> OrganizationalDecisionRecord:
    """Explicitly reopen a decision — the question is open again. The
    prior conclusion is preserved in the record for history."""
    return decision.model_copy(
        update={
            "status": OrganizationalDecisionStatus.REOPENED,
            "consequence_summary": (
                f"Reopened: {reason}"
                if not decision.consequence_summary
                else f"{decision.consequence_summary} Reopened: {reason}"
            ),
            "evidence_ids": decision.evidence_ids + list(evidence_ids or []),
        }
    )
