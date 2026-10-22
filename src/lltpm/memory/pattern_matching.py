"""Structural problem signatures and pattern matching.

Signatures emphasize dependency shape, authority gaps, expertise gaps,
cross-team boundaries, decision type, and KPI relation — never who the
people involved were. Matching is transparent and deterministic.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lltpm.coordination.models import (
    ActionRequest,
    OrganizationalDecisionRecord,
    WorkstreamState,
)
from lltpm.intelligence.kpi import KPIHealthStatus, KPITrend
from lltpm.memory.resolution_patterns import ResolutionPattern


def build_problem_signature(
    *,
    workstream: WorkstreamState,
    request: ActionRequest | None,
    unresolved_decisions: list[OrganizationalDecisionRecord],
    kpi_states: list[KPITrend],
    structural_signals: list[object],
) -> str:
    """Deterministic structural signature. Tokens describe the shape of
    the problem — request type, authority gap, cross-team boundary,
    decision load, KPI health, dependency signals — never actor
    identity."""
    tokens: set[str] = set()

    tokens.add(f"ws_type:{workstream.workstream_type.value}")
    tokens.add(f"health:{workstream.current_health.value}")

    if request is not None:
        tokens.add(f"request:{request.request_type.value}")
        if request.required_authority_relation is not None:
            tokens.add(
                f"needs_authority:{request.required_authority_relation.value}"
            )
        if request.preferred_recipient is None:
            tokens.add("authority_gap")
        requester_team = request.requested_by.team_id
        recipient_team = (
            request.preferred_recipient.team_id
            if request.preferred_recipient is not None
            else None
        )
        if requester_team and recipient_team and requester_team != recipient_team:
            tokens.add("cross_team")
        if request.required_expertise_topics:
            tokens.add("needs_expertise")

    if unresolved_decisions:
        bucket = "many" if len(unresolved_decisions) > 2 else str(
            len(unresolved_decisions)
        )
        tokens.add(f"unresolved_decisions:{bucket}")

    for trend in kpi_states:
        if trend.health in (KPIHealthStatus.BREACHED, KPIHealthStatus.CRITICAL):
            tokens.add("kpi:breached")
        elif trend.health == KPIHealthStatus.STALE:
            tokens.add("kpi:stale")

    for signal in structural_signals:
        kind = str(
            getattr(signal, "signal_kind", None)
            or getattr(signal, "kind", "")
            or ""
        )
        if kind:
            tokens.add(f"signal:{kind}")

    if workstream.blocker_ids:
        tokens.add("blocked_asks")

    return "|".join(sorted(tokens))


class ResolutionPatternMatch(BaseModel):
    pattern_id: str
    structural_similarity: float
    contextual_similarity: float
    applicable: bool
    mismatched_conditions: list[str] = Field(default_factory=list)
    explanation: str


_MIN_STRUCTURAL_SIMILARITY = 0.6


def match_resolution_patterns(
    *,
    problem_signature: str,
    context_conditions: list[str],
    patterns: list[ResolutionPattern],
    min_similarity: float = _MIN_STRUCTURAL_SIMILARITY,
) -> list[ResolutionPatternMatch]:
    """Transparent token-overlap matching (Jaccard on signature tokens)
    plus condition checks. Deterministic ordering: best matches first."""
    problem_tokens = set(problem_signature.split("|")) - {""}
    condition_set = set(context_conditions)
    matches: list[ResolutionPatternMatch] = []

    for pattern in patterns:
        pattern_tokens = set(pattern.problem_signature.split("|")) - {""}
        union = problem_tokens | pattern_tokens
        structural = (
            len(problem_tokens & pattern_tokens) / len(union) if union else 0.0
        )
        mismatched = [
            c for c in pattern.applicable_conditions if c not in condition_set
        ]
        contextual = (
            (len(pattern.applicable_conditions) - len(mismatched))
            / len(pattern.applicable_conditions)
            if pattern.applicable_conditions
            else 1.0
        )
        applicable = structural >= min_similarity and not mismatched
        matches.append(
            ResolutionPatternMatch(
                pattern_id=pattern.id,
                structural_similarity=round(structural, 4),
                contextual_similarity=round(contextual, 4),
                applicable=applicable,
                mismatched_conditions=mismatched,
                explanation=(
                    f"Structural similarity {structural:.2f}; "
                    + (
                        f"unmet conditions: {', '.join(mismatched)}."
                        if mismatched
                        else "all applicable conditions met."
                    )
                ),
            )
        )

    matches.sort(
        key=lambda m: (
            not m.applicable,
            -m.structural_similarity,
            -m.contextual_similarity,
            m.pattern_id,
        )
    )
    return matches


class CoordinationPlaybookSuggestion(BaseModel):
    """Advisory only: recommends a sequence, evidence to collect, and
    authority to identify. It never sends messages and never approves
    decisions."""
    id: str
    project_id: str
    workstream_id: str
    source_pattern_id: str
    problem_summary: str
    recommended_steps: list[str] = Field(default_factory=list)
    evidence_to_collect: list[str] = Field(default_factory=list)
    authority_to_identify: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)


def build_playbook_suggestion(
    *,
    workstream: WorkstreamState,
    pattern: ResolutionPattern,
    match: ResolutionPatternMatch,
    problem_summary: str,
) -> CoordinationPlaybookSuggestion:
    if not match.applicable:
        raise ValueError(
            "A playbook suggestion requires an applicable pattern match."
        )
    return CoordinationPlaybookSuggestion(
        id=f"playbook-{workstream.workstream_id}-{pattern.id}",
        project_id=workstream.project_id,
        workstream_id=workstream.workstream_id,
        source_pattern_id=pattern.id,
        problem_summary=problem_summary,
        recommended_steps=list(pattern.recommended_sequence),
        evidence_to_collect=list(pattern.evidence_requirements),
        authority_to_identify=list(pattern.authority_requirements),
        warnings=list(pattern.known_failure_conditions),
        confidence=min(pattern.confidence, match.structural_similarity),
        evidence_ids=list(workstream.evidence_ids),
    )
