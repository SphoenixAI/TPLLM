"""Procedural coordination memory — observable event traces (Commit 17).

TPLLM remembers *how* organizational problems were resolved: problem
detected → context assembled → ask routed → decision requested →
decision made → action taken → outcome observed. These are explicit,
observable procedural events with evidence — never hidden model
chain-of-thought.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from lltpm.coordination.models import OrganizationalActorRef


class CoordinationEventType(str, Enum):
    PROBLEM_DETECTED = "problem_detected"
    CONTEXT_ASSEMBLED = "context_assembled"
    ASK_ROUTED = "ask_routed"
    DISCUSSION_HELD = "discussion_held"
    DECISION_REQUESTED = "decision_requested"
    DECISION_MADE = "decision_made"
    ACTION_TAKEN = "action_taken"
    BLOCKER_REMOVED = "blocker_removed"
    KPI_RECOVERED = "kpi_recovered"
    MILESTONE_ADVANCED = "milestone_advanced"
    OUTCOME_OBSERVED = "outcome_observed"


class CoordinationTraceEvent(BaseModel):
    """One observable procedural event. Every event describes something
    that happened in the organization, with evidence."""
    id: str
    trace_id: str
    event_type: CoordinationEventType
    occurred_at: datetime
    description: str
    actor_refs: list[OrganizationalActorRef] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    request_ids: list[str] = Field(default_factory=list)
    kpi_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "CoordinationTraceEvent":
        if not self.description.strip():
            raise ValueError(
                "A trace event must describe an observable occurrence."
            )
        return self


class CoordinationTrace(BaseModel):
    id: str
    project_id: str
    problem_signature: str
    initial_problem: str
    structural_context: dict[str, Any] = Field(default_factory=dict)
    events: list[CoordinationTraceEvent] = Field(default_factory=list)
    final_outcome: str | None = None
    successful: bool | None = None
    outcome_evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime


def append_trace_event(
    *,
    trace: CoordinationTrace,
    event: CoordinationTraceEvent,
) -> CoordinationTrace:
    """Append-only by copy: the original trace is never mutated."""
    if event.trace_id != trace.id:
        raise ValueError("Event trace_id does not match the trace.")
    return trace.model_copy(update={"events": [*trace.events, event]})


def complete_trace(
    *,
    trace: CoordinationTrace,
    final_outcome: str,
    successful: bool,
    outcome_evidence_ids: list[str],
) -> CoordinationTrace:
    """Close a trace with its observed outcome and evidence."""
    if not outcome_evidence_ids:
        raise ValueError("An outcome must carry evidence.")
    return trace.model_copy(
        update={
            "final_outcome": final_outcome,
            "successful": successful,
            "outcome_evidence_ids": list(outcome_evidence_ids),
        }
    )
