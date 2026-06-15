"""Source-of-truth precedence rules.

When the same fact appears in multiple source systems (GitHub says "done",
Slack says "almost done", Linear says "in review"), precedence rules
determine which source wins.

Palantir's System Silos guidance: conflicting values need clear precedence
rules. A Slack message saying "I think we're done" should not equal a
merged PR, closed issue, and updated milestone.
"""

from __future__ import annotations

from enum import Enum

from lltpm.ontology.core import SourceSystem


class PrecedenceField(str, Enum):
    STATUS = "status"
    OWNERSHIP = "ownership"
    DECISION = "decision"
    DELIVERY_PROGRESS = "delivery_progress"
    TIMELINE = "timeline"
    PRIORITY = "priority"
    DESCRIPTION = "description"


# Higher index = higher trust.
# The agent resolves conflicts by picking the highest-precedence source
# that has a value for the field in question.

PRECEDENCE_RULES: dict[PrecedenceField, list[SourceSystem]] = {

    PrecedenceField.STATUS: [
        SourceSystem.AGENT,          # 1 — lowest: agent inference
        SourceSystem.SLACK,          # 2
        SourceSystem.ZOOM,           # 3
        SourceSystem.NOTION,         # 4
        SourceSystem.GITHUB,         # 5
        SourceSystem.JIRA,           # 6
        SourceSystem.LINEAR,         # 7
        SourceSystem.TASK_BOARD,     # 8
        SourceSystem.MANUAL,         # 9 — highest: explicit human input
    ],

    PrecedenceField.OWNERSHIP: [
        SourceSystem.AGENT,
        SourceSystem.SLACK,
        SourceSystem.ZOOM,
        SourceSystem.GITHUB,
        SourceSystem.NOTION,
        SourceSystem.LINEAR,
        SourceSystem.JIRA,
        SourceSystem.TASK_BOARD,
        SourceSystem.MANUAL,
    ],

    PrecedenceField.DECISION: [
        SourceSystem.AGENT,
        SourceSystem.SLACK,
        SourceSystem.ZOOM,
        SourceSystem.NOTION,
        SourceSystem.TASK_BOARD,
        SourceSystem.MANUAL,
    ],

    PrecedenceField.DELIVERY_PROGRESS: [
        SourceSystem.AGENT,
        SourceSystem.SLACK,
        SourceSystem.ZOOM,
        SourceSystem.NOTION,
        SourceSystem.TASK_BOARD,
        SourceSystem.JIRA,
        SourceSystem.LINEAR,
        SourceSystem.GITHUB,         # Merged PR = strongest delivery signal
        SourceSystem.MANUAL,
    ],

    PrecedenceField.TIMELINE: [
        SourceSystem.AGENT,
        SourceSystem.SLACK,
        SourceSystem.ZOOM,
        SourceSystem.NOTION,
        SourceSystem.GITHUB,
        SourceSystem.LINEAR,
        SourceSystem.JIRA,
        SourceSystem.TASK_BOARD,
        SourceSystem.MANUAL,
    ],

    PrecedenceField.PRIORITY: [
        SourceSystem.AGENT,
        SourceSystem.SLACK,
        SourceSystem.ZOOM,
        SourceSystem.GITHUB,
        SourceSystem.NOTION,
        SourceSystem.LINEAR,
        SourceSystem.JIRA,
        SourceSystem.TASK_BOARD,
        SourceSystem.MANUAL,
    ],

    PrecedenceField.DESCRIPTION: [
        SourceSystem.AGENT,
        SourceSystem.SLACK,
        SourceSystem.ZOOM,
        SourceSystem.GITHUB,
        SourceSystem.LINEAR,
        SourceSystem.JIRA,
        SourceSystem.NOTION,
        SourceSystem.TASK_BOARD,
        SourceSystem.MANUAL,
    ],
}


def get_precedence_rank(
    field: PrecedenceField,
    source: SourceSystem,
) -> int:
    """Return the trust rank for a source on a given field.

    Higher rank = more trusted. Returns -1 if the source is not
    in the precedence list for that field.
    """
    chain = PRECEDENCE_RULES.get(field, [])
    try:
        return chain.index(source)
    except ValueError:
        return -1


def resolve_conflict(
    field: PrecedenceField,
    candidates: list[tuple[SourceSystem, str]],
) -> tuple[SourceSystem, str] | None:
    """Given conflicting values from multiple sources, return the winner.

    Args:
        field: Which field is in conflict.
        candidates: List of (source_system, value) pairs.

    Returns:
        The (source, value) tuple with the highest precedence, or None
        if no candidates are provided.
    """
    if not candidates:
        return None

    best: tuple[SourceSystem, str] | None = None
    best_rank = -1

    for source, value in candidates:
        rank = get_precedence_rank(field, source)
        if rank > best_rank:
            best_rank = rank
            best = (source, value)

    return best


def explain_precedence(field: PrecedenceField) -> str:
    """Human-readable explanation of precedence for a field."""
    chain = PRECEDENCE_RULES.get(field, [])
    if not chain:
        return f"No precedence rules defined for {field.value}."

    lines = [f"Source-of-truth precedence for {field.value}:"]
    for rank, source in enumerate(reversed(chain), 1):
        lines.append(f"  {rank}. {source.value}")
    return "\n".join(lines)
