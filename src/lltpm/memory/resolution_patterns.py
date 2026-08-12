"""Resolution patterns — how similar coordination problems were resolved
(Commit 17).

Patterns are extracted from completed traces via a protocol (no
hardwired LLM), carry observed success/failure counts, and stay
advisory: a pattern may recommend a sequence; it never sends messages
or approves anything.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from lltpm.memory.coordination_traces import CoordinationTrace


class ResolutionPattern(BaseModel):
    id: str
    name: str
    problem_signature: str
    applicable_conditions: list[str] = Field(default_factory=list)
    recommended_sequence: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    authority_requirements: list[str] = Field(default_factory=list)
    known_failure_conditions: list[str] = Field(default_factory=list)
    source_trace_ids: list[str] = Field(default_factory=list)
    observed_success_count: int = 0
    observed_failure_count: int = 0
    confidence: float = 0.0

    @model_validator(mode="after")
    def _check(self) -> "ResolutionPattern":
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Pattern confidence must be within [0, 1].")
        if self.observed_success_count < 0 or self.observed_failure_count < 0:
            raise ValueError("Observed counts cannot be negative.")
        return self


class ResolutionPatternExtractor(Protocol):
    def extract_patterns(
        self,
        *,
        traces: list[CoordinationTrace],
    ) -> list[ResolutionPattern]:
        ...


_PATTERN_CONFIDENCE_CAP = 0.95


def record_pattern_outcome(
    *,
    pattern: ResolutionPattern,
    successful: bool,
    source_trace_id: str,
) -> ResolutionPattern:
    """Fold one observed outcome into the pattern's track record.
    Confidence is the observed success ratio, capped below certainty."""
    successes = pattern.observed_success_count + (1 if successful else 0)
    failures = pattern.observed_failure_count + (0 if successful else 1)
    total = successes + failures
    confidence = min(
        _PATTERN_CONFIDENCE_CAP, successes / total if total else 0.0
    )
    trace_ids = list(pattern.source_trace_ids)
    if source_trace_id not in trace_ids:
        trace_ids.append(source_trace_id)
    return pattern.model_copy(
        update={
            "observed_success_count": successes,
            "observed_failure_count": failures,
            "confidence": confidence,
            "source_trace_ids": trace_ids,
        }
    )
