"""Decision lineage tracing.

Answers "why is this the authoritative decision right now?" by walking
the supersession and reopen chain rather than trusting a single flag.

Multi-hop supersession is inspected, not assumed: if A was superseded by
B, B by C, and C is then reopened, the chain is re-examined rather than
blindly restoring B — B's own supersession of A may have been made under
a scope that no longer holds.
"""

from __future__ import annotations

from datetime import datetime

from tpllm.temporal.models import (
    DecisionLineage,
    DecisionLineageNode,
    SupersessionCycleError,
)

_CONCLUSIVE = "conclusive"
_SUPERSEDED = "superseded"
_REOPENED = "reopened"


def detect_supersession_cycle(nodes: list[DecisionLineageNode]) -> list[str]:
    """Return the first supersession cycle found, or an empty list.

    Cycles are illegal: a reversal must use explicit reopen semantics.
    """
    edges = {
        n.decision_id: n.superseded_by
        for n in nodes
        if n.superseded_by is not None
    }
    for start in sorted(edges):
        seen: list[str] = []
        current: str | None = start
        while current is not None and current in edges:
            if current in seen:
                return seen[seen.index(current):] + [current]
            seen.append(current)
            current = edges[current]
    return []


def validate_lineage(nodes: list[DecisionLineageNode]) -> None:
    cycle = detect_supersession_cycle(nodes)
    if cycle:
        raise SupersessionCycleError(
            f"Illegal supersession cycle: {' -> '.join(cycle)}. Express a "
            f"reversal with reopen semantics instead."
        )


def trace_decision_lineage(
    *,
    nodes: list[DecisionLineageNode],
    as_of: datetime | None = None,
    root_decision_id: str | None = None,
) -> DecisionLineage:
    """Resolve which decision is authoritative and explain why."""
    validate_lineage(nodes)
    by_id = {n.decision_id: n for n in nodes}

    def visible(node: DecisionLineageNode) -> bool:
        if as_of is None:
            return True
        return node.effective_from is None or node.effective_from <= as_of

    visible_nodes = [n for n in nodes if visible(n)]

    root = root_decision_id
    if root is None:
        roots = [
            n.decision_id for n in nodes
            if n.parent_decision_id is None
            and not any(m.superseded_by == n.decision_id for m in nodes)
        ]
        root = sorted(roots)[0] if roots else (
            sorted(n.decision_id for n in nodes)[0] if nodes else ""
        )

    def is_reopened(node: DecisionLineageNode) -> bool:
        if node.status == _REOPENED:
            return True
        return node.reopened_at is not None and (
            as_of is None or node.reopened_at <= as_of
        )

    candidates: list[tuple[DecisionLineageNode, str]] = []
    for node in visible_nodes:
        if is_reopened(node):
            continue  # an open question is not authoritative

        if node.status == _CONCLUSIVE and node.superseded_by is None:
            candidates.append((node, "conclusive and never superseded"))
            continue

        if node.superseded_by is not None:
            superseder = by_id.get(node.superseded_by)
            if superseder is None:
                continue
            if not is_reopened(superseder):
                continue  # genuinely superseded
            # The superseder is reopened. Walk further: only restore this
            # node if nothing later in the chain still holds authority.
            chain_open = True
            walker = superseder
            guard = 0
            while walker is not None and guard < len(nodes) + 1:
                guard += 1
                if walker.status == _CONCLUSIVE and not is_reopened(walker):
                    chain_open = False
                    break
                walker = (
                    by_id.get(walker.superseded_by)
                    if walker.superseded_by
                    else None
                )
            if chain_open and node.status in (_CONCLUSIVE, _SUPERSEDED) and (
                node.selected_option_id is not None
                or node.status == _CONCLUSIVE
            ):
                candidates.append((
                    node,
                    f"restored: its superseder {superseder.decision_id} was "
                    f"reopened and no later decision holds authority",
                ))

    if not candidates:
        return DecisionLineage(
            root_decision_id=root,
            nodes=list(nodes),
            current_authoritative_decision_id=None,
            explanation=(
                "No decision is currently authoritative: every candidate is "
                "reopened, superseded, or not yet effective."
            ),
        )

    def sort_key(pair: tuple[DecisionLineageNode, str]):
        node = pair[0]
        return (
            node.effective_from or datetime.min,
            node.decision_id,
        )

    winner, reason = sorted(candidates, key=sort_key)[-1]
    return DecisionLineage(
        root_decision_id=root,
        nodes=list(nodes),
        current_authoritative_decision_id=winner.decision_id,
        explanation=(
            f"{winner.decision_id} is authoritative because it is {reason}"
            + (
                f"; origin {winner.decision_origin}"
                if winner.decision_origin != "unknown"
                else ""
            )
            + "."
        ),
    )


def why_is_this_currently_authoritative(
    *,
    nodes: list[DecisionLineageNode],
    as_of: datetime | None = None,
) -> str:
    """Reproducible explanation of the current authority."""
    return trace_decision_lineage(nodes=nodes, as_of=as_of).explanation
