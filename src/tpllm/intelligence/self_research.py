"""Self-research & clarification gate.

Before asking engineers basic questions, the AI must: detect what it
does not understand, search existing ontology evidence and internal
project artifacts, reconcile conflicting evidence, assess whether the
remaining unknown is factual or strategic, and ask a human only when
necessary — arriving at conversations with a concrete project model.

Core invariants:
- Internal-first research: ontology, documents, meetings, chats,
  tickets, code, and decision logs are searched before any human is
  asked a factual question. External research (when the policy allows
  it) comes before human clarification, never before internal sources.
- Human questions are reserved for judgment, conflict between
  authoritative sources, or a missing source of truth.
- Research is evidence-bound: a resolution marked RESOLVED without
  evidence is downgraded.
- Shared-origin duplicate evidence is deduplicated — five copies of one
  statement never count as five independent sources.
- Question history matters: repeated questions are detected by
  deterministic signature and blocked unless the prior answer is stale.
- Broad questions ("What is this project?") are rewritten into precise
  ones whenever evidence exists.

This layer never imports from or mutates the application registry.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from tpllm.intelligence.project_understanding import (
    ProjectEvidence,
    ProjectUnderstandingSnapshot,
    ProjectUnknownKind,
    ResearchPriority,
)
from tpllm.intelligence.launch_readiness import (
    LaunchReadinessDimensionType,
    LaunchReadinessSnapshot,
)


# ── Enums ──────────────────────────────────────────────────────────────

class UnderstandingGapType(str, Enum):
    FACTUAL = "factual"
    TERMINOLOGY = "terminology"
    TECHNICAL_MECHANISM = "technical_mechanism"
    ARCHITECTURE = "architecture"
    OWNERSHIP = "ownership"
    DEPENDENCY = "dependency"
    SCOPE = "scope"
    SUCCESS_CRITERION = "success_criterion"
    STRATEGIC_INTENT = "strategic_intent"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    STALE_INFORMATION = "stale_information"


class ResearchStage(str, Enum):
    ONTOLOGY = "ontology"
    INTERNAL_DOCUMENTS = "internal_documents"
    MEETINGS = "meetings"
    COMMUNICATIONS = "communications"
    TICKETS = "tickets"
    CODE_AND_REPOSITORY = "code_and_repository"
    DECISION_AND_OUTCOME_LOGS = "decision_and_outcome_logs"
    APPROVED_EXTERNAL_RESEARCH = "approved_external_research"
    HUMAN_CLARIFICATION = "human_clarification"


class GapResolutionStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    CONTESTED = "contested"
    HUMAN_DECISION_REQUIRED = "human_decision_required"


class ClarificationNecessity(str, Enum):
    NOT_NEEDED = "not_needed"
    OPTIONAL = "optional"
    REQUIRED = "required"
    STRATEGIC_DECISION_REQUIRED = "strategic_decision_required"


class AIQualityRiskType(str, Enum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    LOW_EVIDENCE_COVERAGE = "low_evidence_coverage"
    REPEATED_QUESTION = "repeated_question"
    UNNECESSARY_QUESTION = "unnecessary_question"
    STALE_UNDERSTANDING = "stale_understanding"
    CONTRADICTION_IGNORED = "contradiction_ignored"
    SHALLOW_TECHNICAL_MODEL = "shallow_technical_model"
    OVERCONFIDENT_INFERENCE = "overconfident_inference"
    FORECAST_MISCALIBRATION = "forecast_miscalibration"


class AIQualityRiskFlag(BaseModel):
    id: str
    project_id: str
    risk_type: AIQualityRiskType
    severity: str
    claim_ids: list[str] = Field(default_factory=list)
    question_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


# ── Provider interfaces ──────────────────────────────────────────────

class EvidenceSearchProvider(Protocol):
    provider_id: str
    stage: ResearchStage

    def search(
        self,
        *,
        project_id: str,
        query: str,
        as_of: datetime,
        max_results: int,
    ) -> list[ProjectEvidence]:
        ...


class ResearchSynthesisProvider(Protocol):
    def synthesize(
        self,
        *,
        gap: "UnderstandingGap",
        evidence: list[ProjectEvidence],
    ) -> "ResearchResolution":
        ...


# ── Research policy ──────────────────────────────────────────────────

class SelfResearchPolicy(BaseModel):
    stage_order: list[ResearchStage] = Field(
        default_factory=lambda: [
            ResearchStage.ONTOLOGY,
            ResearchStage.INTERNAL_DOCUMENTS,
            ResearchStage.MEETINGS,
            ResearchStage.COMMUNICATIONS,
            ResearchStage.TICKETS,
            ResearchStage.CODE_AND_REPOSITORY,
            ResearchStage.DECISION_AND_OUTCOME_LOGS,
            ResearchStage.APPROVED_EXTERNAL_RESEARCH,
            ResearchStage.HUMAN_CLARIFICATION,
        ]
    )
    allow_external_research: bool = True
    max_queries_per_gap: int = 5
    max_results_per_query: int = 10
    minimum_resolution_confidence: float = 0.70
    human_question_impact_threshold: float = 0.60
    require_evidence_for_resolution: bool = True
    prohibit_broad_project_questions: bool = True


# ── Gap models ───────────────────────────────────────────────────────

class UnderstandingGap(BaseModel):
    id: str
    project_id: str
    gap_type: UnderstandingGapType
    question: str
    why_it_matters: str
    affected_claim_ids: list[str] = Field(default_factory=list)
    affected_readiness_dimensions: list[str] = Field(default_factory=list)
    impact_score: float
    current_confidence: float
    internal_research_possible: bool = True
    external_research_possible: bool = False
    human_decision_required: bool = False
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ResearchQuery(BaseModel):
    id: str
    gap_id: str
    stage: ResearchStage
    query: str
    rationale: str
    max_results: int = 10


class ResearchAttempt(BaseModel):
    id: str
    gap_id: str
    stage: ResearchStage
    query: str
    provider_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
    success: bool
    explanation: str


class ResearchResolution(BaseModel):
    id: str
    gap_id: str
    status: GapResolutionStatus
    resolved_statement: Optional[str] = None
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    remaining_ambiguity: Optional[str] = None
    human_question_recommended: bool = False
    explanation: str


# ── Clarification models ─────────────────────────────────────────────

class ClarificationQuestion(BaseModel):
    id: str
    project_id: str
    gap_id: str
    question: str
    what_the_ai_already_found: str
    remaining_ambiguity: str
    why_the_answer_matters: str
    requested_answer_type: str
    evidence_ids: list[str] = Field(default_factory=list)
    necessity: ClarificationNecessity
    question_signature: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class QuestionHistoryRecord(BaseModel):
    id: str
    project_id: str
    question_signature: str
    question_id: str
    asked_at: datetime
    answered_at: Optional[datetime] = None
    answer_evidence_id: Optional[str] = None
    resolution_claim_id: Optional[str] = None


# ── Gap detection ────────────────────────────────────────────────────

_UNKNOWN_GAP_MAP: dict[ProjectUnknownKind, UnderstandingGapType] = {
    ProjectUnknownKind.MISSING_FACT: UnderstandingGapType.FACTUAL,
    ProjectUnknownKind.AMBIGUOUS_TERM: UnderstandingGapType.TERMINOLOGY,
    ProjectUnknownKind.CONFLICTING_EVIDENCE: UnderstandingGapType.CONFLICTING_EVIDENCE,
    ProjectUnknownKind.MISSING_OWNER: UnderstandingGapType.OWNERSHIP,
    ProjectUnknownKind.MISSING_DEPENDENCY: UnderstandingGapType.DEPENDENCY,
    ProjectUnknownKind.MISSING_SUCCESS_CRITERION: UnderstandingGapType.SUCCESS_CRITERION,
    ProjectUnknownKind.MISSING_TECHNICAL_MECHANISM: UnderstandingGapType.TECHNICAL_MECHANISM,
    ProjectUnknownKind.MISSING_COMPANY_ALIGNMENT: UnderstandingGapType.FACTUAL,
    ProjectUnknownKind.STALE_UNDERSTANDING: UnderstandingGapType.STALE_INFORMATION,
}

_PRIORITY_IMPACT: dict[ResearchPriority, float] = {
    ResearchPriority.CRITICAL: 0.9,
    ResearchPriority.HIGH: 0.7,
    ResearchPriority.MEDIUM: 0.45,
    ResearchPriority.LOW: 0.25,
}

_DIMENSION_GAP_MAP: dict[LaunchReadinessDimensionType, UnderstandingGapType] = {
    LaunchReadinessDimensionType.SCOPE_CLARITY: UnderstandingGapType.SCOPE,
    LaunchReadinessDimensionType.TECHNICAL_CONFIDENCE: UnderstandingGapType.TECHNICAL_MECHANISM,
    LaunchReadinessDimensionType.OWNERSHIP_CLARITY: UnderstandingGapType.OWNERSHIP,
    LaunchReadinessDimensionType.DEPENDENCY_HEALTH: UnderstandingGapType.DEPENDENCY,
    LaunchReadinessDimensionType.VALIDATION_READINESS: UnderstandingGapType.SUCCESS_CRITERION,
}

_LOW_DIMENSION_CONFIDENCE = 0.35


def detect_understanding_gaps(
    *,
    understanding: ProjectUnderstandingSnapshot,
    readiness: LaunchReadinessSnapshot | None = None,
) -> list[UnderstandingGap]:
    """Map understanding unknowns (and low-confidence readiness dimensions)
    into researchable gaps with impact scores."""
    gaps: list[UnderstandingGap] = []

    dimension_lookup: dict[str, float] = {}
    if readiness is not None:
        dimension_lookup = {
            d.dimension.value: d.confidence
            for d in readiness.scorecard.dimensions
        }

    for unknown in understanding.unknowns:
        gap_type = (
            UnderstandingGapType.STRATEGIC_INTENT
            if unknown.human_decision_required
            else _UNKNOWN_GAP_MAP.get(unknown.kind, UnderstandingGapType.FACTUAL)
        )
        impact = _PRIORITY_IMPACT[unknown.priority]
        affected_dims = [
            name
            for name, gap_kind in (
                (d.value, _DIMENSION_GAP_MAP.get(d))
                for d in LaunchReadinessDimensionType
            )
            if gap_kind is not None and gap_kind == gap_type
        ]
        gaps.append(
            UnderstandingGap(
                id=f"gap-{unknown.id}",
                project_id=understanding.project_id,
                gap_type=gap_type,
                question=unknown.question,
                why_it_matters=unknown.why_it_matters,
                affected_claim_ids=unknown.affected_claim_ids,
                affected_readiness_dimensions=affected_dims,
                impact_score=min(1.0, impact + 0.05 * len(affected_dims)),
                current_confidence=0.0,
                internal_research_possible=unknown.resolvable_from_internal_evidence,
                human_decision_required=unknown.human_decision_required,
                created_at=unknown.created_at,
            )
        )

    covered_types = {g.gap_type for g in gaps}
    if readiness is not None:
        for dimension_value, confidence in sorted(dimension_lookup.items()):
            if confidence >= _LOW_DIMENSION_CONFIDENCE:
                continue
            try:
                dimension = LaunchReadinessDimensionType(dimension_value)
            except ValueError:
                continue
            gap_type = _DIMENSION_GAP_MAP.get(dimension)
            if gap_type is None or gap_type in covered_types:
                continue
            covered_types.add(gap_type)
            gaps.append(
                UnderstandingGap(
                    id=f"gap-dim-{dimension_value}",
                    project_id=understanding.project_id,
                    gap_type=gap_type,
                    question=(
                        f"Readiness dimension '{dimension_value}' has low "
                        f"evidence confidence. What evidence establishes it?"
                    ),
                    why_it_matters=(
                        "A low-confidence readiness dimension weakens the "
                        "launch forecast."
                    ),
                    affected_readiness_dimensions=[dimension_value],
                    impact_score=0.6,
                    current_confidence=confidence,
                    created_at=understanding.as_of,
                )
            )

    return gaps


# ── Query planning ───────────────────────────────────────────────────

_STOPWORDS = {
    "a", "an", "and", "are", "be", "been", "by", "can", "do", "does", "for",
    "from", "has", "have", "how", "in", "is", "it", "its", "of", "on", "or",
    "should", "that", "the", "their", "there", "this", "to", "was", "were",
    "what", "when", "where", "which", "who", "why", "will", "with",
}


def _keywords(text: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower())
    unique: list[str] = []
    for word in words:
        if word in _STOPWORDS or word in unique:
            continue
        unique.append(word)
        if len(unique) >= limit:
            break
    return unique


def plan_research_queries(
    *,
    gap: UnderstandingGap,
    understanding: ProjectUnderstandingSnapshot,
    policy: SelfResearchPolicy,
) -> list[ResearchQuery]:
    """Specific, entity-anchored queries — never 'What is X?' paraphrases
    with no information gain."""
    base_terms = [understanding.project_id] + _keywords(gap.question)
    base_query = " ".join(base_terms)

    candidates: list[tuple[str, str]] = [
        (base_query, "Entity-anchored search for the gap subject."),
    ]
    component_names = [c.name for c in understanding.architecture.components][:3]
    if component_names:
        candidates.append(
            (
                f"{base_query} {' '.join(component_names)}",
                "Scoped by known architecture components.",
            )
        )
    glossary_terms = [g.term for g in understanding.glossary][:2]
    if glossary_terms:
        candidates.append(
            (
                f"{base_query} {' '.join(glossary_terms)}",
                "Scoped by project glossary terms.",
            )
        )
    if gap.gap_type == UnderstandingGapType.CONFLICTING_EVIDENCE:
        candidates.append(
            (
                f"{base_query} latest decision current status",
                "Search for the most recent authoritative decision on the "
                "conflict.",
            )
        )

    queries: list[ResearchQuery] = []
    seen: set[str] = set()
    for text, rationale in candidates:
        if text in seen:
            continue
        seen.add(text)
        queries.append(
            ResearchQuery(
                id=f"q-{gap.id}-{len(queries) + 1}",
                gap_id=gap.id,
                stage=ResearchStage.ONTOLOGY,
                query=text,
                rationale=rationale,
                max_results=policy.max_results_per_query,
            )
        )
        if len(queries) >= policy.max_queries_per_gap:
            break
    return queries


# ── Evidence reconciliation ──────────────────────────────────────────

def _dedupe_evidence(evidence: list[ProjectEvidence]) -> list[ProjectEvidence]:
    """Shared-origin duplicates collapse — copies never inflate confidence."""
    deduped: list[ProjectEvidence] = []
    seen: set[tuple[str, str, str]] = set()
    for item in evidence:
        key = (
            item.source.source_type.value,
            item.source.source_id,
            item.content.strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def reconcile_research_evidence(
    *,
    gap: UnderstandingGap,
    evidence: list[ProjectEvidence],
    synthesis_provider: ResearchSynthesisProvider,
) -> ResearchResolution:
    """Dedupe shared-origin evidence, synthesize, then enforce the
    evidence-bound rules the synthesis provider cannot bypass."""
    deduped = _dedupe_evidence(evidence)
    resolution = synthesis_provider.synthesize(gap=gap, evidence=deduped)

    update: dict = {}
    if (
        gap.gap_type == UnderstandingGapType.STRATEGIC_INTENT
        or gap.human_decision_required
    ):
        update["status"] = GapResolutionStatus.HUMAN_DECISION_REQUIRED
        update["human_question_recommended"] = True
    elif resolution.contradicting_evidence_ids:
        update["status"] = GapResolutionStatus.CONTESTED
        update["human_question_recommended"] = True
    elif (
        resolution.status == GapResolutionStatus.RESOLVED
        and not resolution.evidence_ids
    ):
        update["status"] = GapResolutionStatus.UNRESOLVED
        update["confidence"] = min(resolution.confidence, 0.2)
        update["explanation"] = (
            resolution.explanation
            + " Downgraded: a resolution must be evidence-bound."
        )

    return resolution.model_copy(update=update) if update else resolution


# ── Self-research execution ──────────────────────────────────────────

def execute_self_research(
    *,
    gap: UnderstandingGap,
    queries: list[ResearchQuery],
    providers: list[EvidenceSearchProvider],
    synthesis_provider: ResearchSynthesisProvider,
    policy: SelfResearchPolicy,
    as_of: datetime,
) -> tuple[ResearchResolution, list[ResearchAttempt], list[ProjectEvidence]]:
    """Follow the policy stage order; stop when the confidence threshold
    is met with no material contradiction; never reach human
    clarification inside this function; never call external providers
    when the policy prohibits them."""
    attempts: list[ResearchAttempt] = []
    evidence: list[ProjectEvidence] = []
    seen: set[tuple[str, str, str]] = set()

    providers_by_stage: dict[ResearchStage, list[EvidenceSearchProvider]] = {}
    for provider in providers:
        providers_by_stage.setdefault(provider.stage, []).append(provider)

    resolution: ResearchResolution | None = None

    for stage in policy.stage_order:
        if stage == ResearchStage.HUMAN_CLARIFICATION:
            continue  # humans are never consulted from inside research
        if (
            stage == ResearchStage.APPROVED_EXTERNAL_RESEARCH
            and not policy.allow_external_research
        ):
            continue
        for provider in providers_by_stage.get(stage, []):
            for query in queries[: policy.max_queries_per_gap]:
                results = provider.search(
                    project_id=gap.project_id,
                    query=query.query,
                    as_of=as_of,
                    max_results=policy.max_results_per_query,
                )
                fresh: list[ProjectEvidence] = []
                for item in results:
                    key = (
                        item.source.source_type.value,
                        item.source.source_id,
                        item.content.strip().lower(),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    fresh.append(item)
                evidence.extend(fresh)
                attempts.append(
                    ResearchAttempt(
                        id=f"attempt-{gap.id}-{len(attempts) + 1}",
                        gap_id=gap.id,
                        stage=stage,
                        query=query.query,
                        provider_id=provider.provider_id,
                        evidence_ids=[e.id for e in fresh],
                        started_at=as_of,
                        completed_at=as_of,
                        success=bool(fresh),
                        explanation=(
                            f"{len(fresh)} new evidence item(s) from "
                            f"{provider.provider_id}."
                        ),
                    )
                )
        if evidence:
            resolution = reconcile_research_evidence(
                gap=gap, evidence=evidence, synthesis_provider=synthesis_provider
            )
            if (
                resolution.status == GapResolutionStatus.RESOLVED
                and resolution.confidence >= policy.minimum_resolution_confidence
                and not resolution.contradicting_evidence_ids
            ):
                return resolution, attempts, evidence

    if resolution is None:
        resolution = reconcile_research_evidence(
            gap=gap, evidence=evidence, synthesis_provider=synthesis_provider
        )
    return resolution, attempts, evidence


# ── Clarification gate ───────────────────────────────────────────────

def determine_clarification_necessity(
    *,
    gap: UnderstandingGap,
    resolution: ResearchResolution,
    policy: SelfResearchPolicy,
) -> ClarificationNecessity:
    if (
        resolution.status == GapResolutionStatus.HUMAN_DECISION_REQUIRED
        or gap.gap_type == UnderstandingGapType.STRATEGIC_INTENT
    ):
        return ClarificationNecessity.STRATEGIC_DECISION_REQUIRED
    if (
        resolution.status == GapResolutionStatus.RESOLVED
        and resolution.confidence >= policy.minimum_resolution_confidence
        and not resolution.contradicting_evidence_ids
    ):
        return ClarificationNecessity.NOT_NEEDED
    if resolution.status == GapResolutionStatus.CONTESTED:
        return ClarificationNecessity.REQUIRED
    if gap.impact_score >= policy.human_question_impact_threshold:
        return ClarificationNecessity.REQUIRED
    return ClarificationNecessity.OPTIONAL


_BROAD_QUESTION_PATTERNS = (
    re.compile(r"^\s*what\s+is\b", re.IGNORECASE),
    re.compile(r"^\s*how\s+does\s+(this|it)\s+work", re.IGNORECASE),
    re.compile(r"^\s*who\s+owns\s+this", re.IGNORECASE),
)


def build_question_signature(
    *,
    project_id: str,
    gap_type: UnderstandingGapType,
    normalized_subject: str,
    unresolved_contrast: str,
) -> str:
    payload = "|".join(
        [
            project_id.strip().lower(),
            gap_type.value,
            re.sub(r"\s+", " ", normalized_subject.strip().lower()),
            re.sub(r"\s+", " ", unresolved_contrast.strip().lower()),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def build_clarification_question(
    *,
    gap: UnderstandingGap,
    resolution: ResearchResolution,
    evidence_by_id: dict[str, ProjectEvidence],
    necessity: ClarificationNecessity,
    now: datetime | None = None,
) -> ClarificationQuestion:
    """Precise, evidence-grounded question: what was found, what remains
    unresolved, why it matters, and the exact question. Broad questions
    are rewritten into precise ones whenever evidence exists."""
    if necessity == ClarificationNecessity.NOT_NEEDED:
        raise ValueError(
            "A clarification question must not be built when clarification "
            "is not needed."
        )

    found_bits: list[str] = []
    used_evidence: list[str] = []
    for eid in resolution.evidence_ids[:3] + resolution.contradicting_evidence_ids[:3]:
        item = evidence_by_id.get(eid)
        if item is None:
            continue
        found_bits.append(
            f"[{item.source.source_type.value}] {item.content[:160]}"
        )
        used_evidence.append(eid)
    found_summary = (
        "; ".join(found_bits)
        if found_bits
        else "No internal evidence resolved this."
    )

    remaining = resolution.remaining_ambiguity or f"Still unresolved: {gap.question}"

    question_text = gap.question
    is_broad = any(p.match(question_text) for p in _BROAD_QUESTION_PATTERNS)
    if is_broad and found_bits:
        question_text = (
            f"Given the evidence found — {found_bits[0]} — {remaining} "
            f"Which interpretation is correct?"
        )

    signature = build_question_signature(
        project_id=gap.project_id,
        gap_type=gap.gap_type,
        normalized_subject=" ".join(_keywords(gap.question)),
        unresolved_contrast=" ".join(_keywords(remaining)),
    )

    return ClarificationQuestion(
        id=f"clarify-{gap.id}",
        project_id=gap.project_id,
        gap_id=gap.id,
        question=question_text,
        what_the_ai_already_found=found_summary,
        remaining_ambiguity=remaining,
        why_the_answer_matters=gap.why_it_matters,
        requested_answer_type=(
            "decision"
            if necessity == ClarificationNecessity.STRATEGIC_DECISION_REQUIRED
            else "fact"
        ),
        evidence_ids=used_evidence,
        necessity=necessity,
        question_signature=signature,
        created_at=now or datetime.now(timezone.utc),
    )


_ANSWER_STALENESS_DAYS = 90


def is_repeated_question(
    *,
    question: ClarificationQuestion,
    history: list[QuestionHistoryRecord],
    now: datetime | None = None,
    staleness_days: int = _ANSWER_STALENESS_DAYS,
) -> bool:
    """A question is repeated (and blocked) when its signature was asked
    before and the prior answer is still pending or still fresh. A stale
    prior answer permits re-asking."""
    matches = [
        h for h in history if h.question_signature == question.question_signature
    ]
    if not matches:
        return False
    reference = now or question.created_at
    for record in matches:
        if record.answered_at is None:
            return True  # already asked and still awaiting an answer
        if (reference - record.answered_at).days <= staleness_days:
            return True
    return False


# ── Orchestrator ─────────────────────────────────────────────────────

class SelfResearchEngine:
    """Detect gaps → research internally → reconcile → ask precisely,
    only when necessary, never repeating itself."""

    def __init__(
        self,
        *,
        providers: list[EvidenceSearchProvider],
        synthesis_provider: ResearchSynthesisProvider,
        policy: SelfResearchPolicy,
    ):
        self.providers = providers
        self.synthesis_provider = synthesis_provider
        self.policy = policy

    def resolve_project_gaps(
        self,
        *,
        understanding: ProjectUnderstandingSnapshot,
        readiness: LaunchReadinessSnapshot | None,
        question_history: list[QuestionHistoryRecord],
        as_of: datetime,
    ) -> tuple[
        list[ResearchResolution],
        list[ResearchAttempt],
        list[ClarificationQuestion],
        list[AIQualityRiskFlag],
    ]:
        gaps = detect_understanding_gaps(
            understanding=understanding, readiness=readiness
        )
        gaps.sort(key=lambda g: (-g.impact_score, g.id))

        resolutions: list[ResearchResolution] = []
        attempts: list[ResearchAttempt] = []
        questions: list[ClarificationQuestion] = []
        flags: list[AIQualityRiskFlag] = []
        evidence_pool: dict[str, ProjectEvidence] = {}

        for gap in gaps:
            queries = plan_research_queries(
                gap=gap, understanding=understanding, policy=self.policy
            )
            resolution, gap_attempts, evidence = execute_self_research(
                gap=gap,
                queries=queries,
                providers=self.providers,
                synthesis_provider=self.synthesis_provider,
                policy=self.policy,
                as_of=as_of,
            )
            resolutions.append(resolution)
            attempts.extend(gap_attempts)
            for item in evidence:
                evidence_pool[item.id] = item

            necessity = determine_clarification_necessity(
                gap=gap, resolution=resolution, policy=self.policy
            )
            if necessity not in (
                ClarificationNecessity.REQUIRED,
                ClarificationNecessity.STRATEGIC_DECISION_REQUIRED,
            ):
                continue

            question = build_clarification_question(
                gap=gap,
                resolution=resolution,
                evidence_by_id=evidence_pool,
                necessity=necessity,
                now=as_of,
            )
            if is_repeated_question(
                question=question, history=question_history, now=as_of
            ):
                flags.append(
                    AIQualityRiskFlag(
                        id=f"flag-repeat-{gap.id}",
                        project_id=gap.project_id,
                        risk_type=AIQualityRiskType.REPEATED_QUESTION,
                        severity="medium",
                        question_ids=[question.id],
                        explanation=(
                            f"Question '{question.question[:80]}' repeats a "
                            f"previously asked question whose answer is not "
                            f"stale. Blocked."
                        ),
                    )
                )
                continue
            questions.append(question)

        return resolutions, attempts, questions, flags
