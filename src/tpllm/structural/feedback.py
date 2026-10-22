"""Flight Data Recorder — human decision capture.

Records what the agent recommended, what the human chose, and why. This is
the input side of the feedback loop: pure observation, no auto-tuning.

The ``HumanDecisionRecord`` is the atomic unit of feedback — one human
response to one ``AgentRecommendation`` or ``InterventionOption``. It
captures the decision type (accept / reject / override / defer / escalate),
the override reason (deterministic keyword parser, swappable for LLM later),
and optional free-text notes.

``post_recommendation_hook`` is called from ``StructuralEngine``, NEVER from
the validation gate. The gate is sacrosanct.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from tpllm.ontology.core import SourceRef


class HumanDecisionType(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    OVERRIDDEN = "overridden"
    DEFERRED = "deferred"
    ESCALATED = "escalated"


class OverrideReason(str, Enum):
    WRONG_PRIORITY = "wrong_priority"
    MISSING_CONTEXT = "missing_context"
    ALREADY_HANDLED = "already_handled"
    TOO_DISRUPTIVE = "too_disruptive"
    INCORRECT_DIAGNOSIS = "incorrect_diagnosis"
    TIMING_NOT_RIGHT = "timing_not_right"
    ORGANIZATIONAL_CONSTRAINT = "organizational_constraint"
    OTHER = "other"


_KEYWORD_MAP: dict[str, OverrideReason] = {
    "priority": OverrideReason.WRONG_PRIORITY,
    "context": OverrideReason.MISSING_CONTEXT,
    "handled": OverrideReason.ALREADY_HANDLED,
    "already": OverrideReason.ALREADY_HANDLED,
    "disruptive": OverrideReason.TOO_DISRUPTIVE,
    "disrupt": OverrideReason.TOO_DISRUPTIVE,
    "wrong": OverrideReason.INCORRECT_DIAGNOSIS,
    "incorrect": OverrideReason.INCORRECT_DIAGNOSIS,
    "misdiagnos": OverrideReason.INCORRECT_DIAGNOSIS,
    "timing": OverrideReason.TIMING_NOT_RIGHT,
    "not now": OverrideReason.TIMING_NOT_RIGHT,
    "later": OverrideReason.TIMING_NOT_RIGHT,
    "org": OverrideReason.ORGANIZATIONAL_CONSTRAINT,
    "political": OverrideReason.ORGANIZATIONAL_CONSTRAINT,
    "reorg": OverrideReason.ORGANIZATIONAL_CONSTRAINT,
}


class ParsedOverrideReason(BaseModel):
    """Result of parsing a free-text override reason into a structured enum."""
    reason: OverrideReason = OverrideReason.OTHER
    raw_text: str = ""
    confidence: float = 0.0
    parser_version: str = "keyword_v1"


def parse_override_reason(text: str) -> ParsedOverrideReason:
    """Deterministic keyword parser for override reasons.

    Scans the text for known keywords and returns the first match. The
    interface is designed for a future LLM swap — change the body, keep
    the signature and return type.
    """
    if not text:
        return ParsedOverrideReason(raw_text=text)

    lower = text.lower()
    for keyword, reason in _KEYWORD_MAP.items():
        if keyword in lower:
            return ParsedOverrideReason(
                reason=reason,
                raw_text=text,
                confidence=0.8,
            )
    return ParsedOverrideReason(
        reason=OverrideReason.OTHER,
        raw_text=text,
        confidence=0.3,
    )


class HumanDecisionRecord(BaseModel):
    """One human response to one agent recommendation.

    The atomic unit of the Flight Data Recorder. Captures what the agent
    recommended, what the human chose, and why — without modifying any
    agent state.
    """
    id: str = ""
    recommendation_id: str = ""
    intervention_option_id: str = ""
    signal_id: str = ""
    program_id: str = ""
    decision_type: HumanDecisionType = HumanDecisionType.ACCEPTED
    override_reason: ParsedOverrideReason | None = None
    human_notes: str = ""
    decided_by_id: str = ""
    decided_at: datetime = Field(default_factory=datetime.utcnow)
    agent_predicted_risk_reduction: float = 0.0
    agent_confidence: float = 0.0
    agent_severity: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)


def record_human_decision(
    recommendation_id: str,
    decision_type: HumanDecisionType,
    *,
    intervention_option_id: str = "",
    signal_id: str = "",
    program_id: str = "",
    override_text: str = "",
    human_notes: str = "",
    decided_by_id: str = "",
    agent_predicted_risk_reduction: float = 0.0,
    agent_confidence: float = 0.0,
    agent_severity: str = "",
) -> HumanDecisionRecord:
    """Create a HumanDecisionRecord from the decision parameters.

    Parses the override text into a structured reason when the decision
    is an override or rejection.
    """
    parsed = None
    if decision_type in (HumanDecisionType.OVERRIDDEN, HumanDecisionType.REJECTED):
        parsed = parse_override_reason(override_text or human_notes)

    return HumanDecisionRecord(
        id=f"hdr-{recommendation_id}-{int(datetime.utcnow().timestamp())}",
        recommendation_id=recommendation_id,
        intervention_option_id=intervention_option_id,
        signal_id=signal_id,
        program_id=program_id,
        decision_type=decision_type,
        override_reason=parsed,
        human_notes=human_notes,
        decided_by_id=decided_by_id,
        agent_predicted_risk_reduction=agent_predicted_risk_reduction,
        agent_confidence=agent_confidence,
        agent_severity=agent_severity,
    )


def post_recommendation_hook(
    recommendation_id: str,
    intervention_option_id: str = "",
    signal_id: str = "",
    agent_predicted_risk_reduction: float = 0.0,
    agent_confidence: float = 0.0,
    agent_severity: str = "",
) -> dict:
    """Snapshot what the agent saw at recommendation time.

    Called from ``StructuralEngine``, never from the validation gate.
    Returns a dict suitable for persisting via ArtifactStore — the
    "before" picture that outcome measurement compares against.
    """
    return {
        "recommendation_id": recommendation_id,
        "intervention_option_id": intervention_option_id,
        "signal_id": signal_id,
        "agent_predicted_risk_reduction": agent_predicted_risk_reduction,
        "agent_confidence": agent_confidence,
        "agent_severity": agent_severity,
        "snapshot_at": datetime.utcnow().isoformat(),
    }
