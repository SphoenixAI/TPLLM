"""Temporal truth layer.

Reconstructs exactly what was known at an arbitrary point in time, so a
forecast can never accidentally use information that became known later.

The bitemporal distinction is the foundation of the whole scenario arc:
reality time (when a fact was true) is never conflated with knowledge
time (when the organization learned it).

This layer imports no other tpllm package — it sits beside core at the
bottom of the dependency graph.
"""

from tpllm.temporal.lineage import (
    detect_supersession_cycle,
    trace_decision_lineage,
    validate_lineage,
    why_is_this_currently_authoritative,
)
from tpllm.temporal.models import (
    DecisionLineage,
    DecisionLineageNode,
    ProjectTemporalState,
    SupersessionCycleError,
    TemporalEvent,
    TemporalEventType,
    TemporalFactWindow,
    build_state_fingerprint,
)
from tpllm.temporal.queries import (
    TemporalDelta,
    assert_no_future_leakage,
    what_changed_between,
    what_was_authoritative_at,
    what_was_known_at,
    what_was_true_at,
)
from tpllm.temporal.state import (
    events_known_at,
    events_true_at,
    reconstruct_project_state,
)

__all__ = [
    "DecisionLineage",
    "DecisionLineageNode",
    "ProjectTemporalState",
    "SupersessionCycleError",
    "TemporalDelta",
    "TemporalEvent",
    "TemporalEventType",
    "TemporalFactWindow",
    "assert_no_future_leakage",
    "build_state_fingerprint",
    "detect_supersession_cycle",
    "events_known_at",
    "events_true_at",
    "reconstruct_project_state",
    "trace_decision_lineage",
    "validate_lineage",
    "what_changed_between",
    "what_was_authoritative_at",
    "what_was_known_at",
    "what_was_true_at",
    "why_is_this_currently_authoritative",
]
