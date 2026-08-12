"""Procedural organizational memory (Commit 17).

Stores how coordination problems were resolved — observable event
traces, extracted resolution patterns with success/failure records, and
transparent structural matching — plus audience-aware context briefs.

Everything here is advisory memory: it never sends messages, never
approves decisions, and never touches application authority. Problem
signatures describe structure, never people.
"""

from lltpm.memory.context_briefs import (
    OrganizationalContextBrief,
    build_organizational_context_brief,
)
from lltpm.memory.coordination_traces import (
    CoordinationEventType,
    CoordinationTrace,
    CoordinationTraceEvent,
    append_trace_event,
    complete_trace,
)
from lltpm.memory.pattern_matching import (
    CoordinationPlaybookSuggestion,
    ResolutionPatternMatch,
    build_playbook_suggestion,
    build_problem_signature,
    match_resolution_patterns,
)
from lltpm.memory.resolution_patterns import (
    ResolutionPattern,
    ResolutionPatternExtractor,
    record_pattern_outcome,
)

__all__ = [
    "CoordinationEventType",
    "CoordinationPlaybookSuggestion",
    "CoordinationTrace",
    "CoordinationTraceEvent",
    "OrganizationalContextBrief",
    "ResolutionPattern",
    "ResolutionPatternExtractor",
    "ResolutionPatternMatch",
    "append_trace_event",
    "build_organizational_context_brief",
    "build_playbook_suggestion",
    "build_problem_signature",
    "complete_trace",
    "match_resolution_patterns",
    "record_pattern_outcome",
]
