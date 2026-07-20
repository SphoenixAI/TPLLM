"""Evidence-backed project understanding engine (Commit 11).

Turns ontology/meeting/chat/doc/ticket/code evidence into a durable,
technical, evidence-backed understanding of every project: what it is, why
it exists, which company objective it serves, how it technically works,
what success and failure mean, what is assumed, what is contested, and
what is still unknown.

The intelligence chain built here is analytical only:

    raw records → normalized ProjectEvidence → UnderstandingClaims →
    purpose / significance / technical mechanism / architecture map /
    glossary / assumptions → contradictions → unknowns → research tasks →
    ProjectUnderstandingSnapshot

Non-negotiable invariants:
- Every substantive claim is evidence-bound; inference never silently
  becomes fact (SUPPORTED requires evidence, INFERRED requires an
  explanation, UNKNOWN stays near-zero confidence).
- Unknown is a first-class state — gaps are surfaced, never papered over
  with plausible prose.
- Conflicts survive summarization: contradictory claims both remain,
  both become CONTESTED, and a ClaimContradiction record is emitted.
  No winner is chosen automatically.
- Evidence freshness matters; every item and claim carries timestamps.
- This layer never imports from or mutates the application layer,
  governance records, or ranker state. It models projects and systems —
  never individual workers.

Deterministic builders read structured claim tags (extractors — LLM or
ontology adapters behind the ProjectClaimExtractor protocol — attach
them):

    subject:<slug>, predicate:<value>   contradiction detection
    beneficiary:<name>                  purpose beneficiaries
    goal:<name>, impact_type:<t>        company alignment
    step_order:<n>, input:<e>, output:<e>  mechanism steps
    component:<name>, component_type:<t>, owner:<id>  architecture
    relation:<src>=><dst>=<type>        architecture relations
    term:<term>, alias:<name>           glossary
    explicit, validated, if_wrong:<txt> assumptions
    observability                       observability signals
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────

class EvidenceSourceType(str, Enum):
    ONTOLOGY = "ontology"
    MEETING = "meeting"
    CHAT = "chat"
    MESSAGE = "message"
    DOCUMENT = "document"
    TICKET = "ticket"
    CODE = "code"
    REPOSITORY = "repository"
    DECISION_LOG = "decision_log"
    OUTCOME_LOG = "outcome_log"
    EXTERNAL_RESEARCH = "external_research"
    HUMAN_CLARIFICATION = "human_clarification"


class UnderstandingClaimType(str, Enum):
    PROJECT_PURPOSE = "project_purpose"
    PROBLEM_STATEMENT = "problem_statement"
    COMPANY_ALIGNMENT = "company_alignment"
    CUSTOMER_IMPACT = "customer_impact"
    SUCCESS_CRITERION = "success_criterion"
    NON_GOAL = "non_goal"
    SCOPE = "scope"
    TECHNICAL_MECHANISM = "technical_mechanism"
    ARCHITECTURE_COMPONENT = "architecture_component"
    INTERFACE = "interface"
    DEPENDENCY = "dependency"
    OWNERSHIP = "ownership"
    ASSUMPTION = "assumption"
    FAILURE_MODE = "failure_mode"
    OPERATIONAL_REQUIREMENT = "operational_requirement"
    TERM_DEFINITION = "term_definition"


class UnderstandingClaimStatus(str, Enum):
    SUPPORTED = "supported"
    INFERRED = "inferred"
    CONTESTED = "contested"
    UNKNOWN = "unknown"
    STALE = "stale"


class ProjectUnknownKind(str, Enum):
    MISSING_FACT = "missing_fact"
    AMBIGUOUS_TERM = "ambiguous_term"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    MISSING_OWNER = "missing_owner"
    MISSING_DEPENDENCY = "missing_dependency"
    MISSING_SUCCESS_CRITERION = "missing_success_criterion"
    MISSING_TECHNICAL_MECHANISM = "missing_technical_mechanism"
    MISSING_COMPANY_ALIGNMENT = "missing_company_alignment"
    STALE_UNDERSTANDING = "stale_understanding"


class ResearchPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ArchitectureRelationType(str, Enum):
    DEPENDS_ON = "depends_on"
    CALLS = "calls"
    EMITS_TO = "emits_to"
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    AUTHORIZES = "authorizes"
    BLOCKS = "blocks"
    OWNED_BY = "owned_by"
    REPLACES = "replaces"
    MIGRATES_TO = "migrates_to"


# ── Evidence models ──────────────────────────────────────────────────

class EvidenceReference(BaseModel):
    id: str
    project_id: str
    source_type: EvidenceSourceType
    source_id: str
    title: Optional[str] = None
    excerpt: Optional[str] = None
    observed_at: datetime
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    author_or_system: Optional[str] = None
    reliability: float = 0.5
    relevance: float = 0.5
    ontology_object_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_bounds(self) -> "EvidenceReference":
        if not (0.0 <= self.reliability <= 1.0):
            raise ValueError("reliability must be within [0, 1].")
        if not (0.0 <= self.relevance <= 1.0):
            raise ValueError("relevance must be within [0, 1].")
        return self


class ProjectEvidence(BaseModel):
    id: str
    project_id: str
    content: str
    source: EvidenceReference
    tags: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    normalized_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ── Claim models ─────────────────────────────────────────────────────

class UnderstandingClaim(BaseModel):
    id: str
    project_id: str
    claim_type: UnderstandingClaimType
    statement: str
    status: UnderstandingClaimStatus
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    inferred: bool = False
    inference_explanation: Optional[str] = None
    first_observed_at: Optional[datetime] = None
    last_supported_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_claim(self) -> "UnderstandingClaim":
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within [0, 1].")
        if self.status == UnderstandingClaimStatus.SUPPORTED and not self.evidence_ids:
            raise ValueError(
                "SUPPORTED claim requires at least one evidence reference — "
                "no substantive claim without evidence."
            )
        if self.status == UnderstandingClaimStatus.INFERRED and not self.inference_explanation:
            raise ValueError(
                "INFERRED claim requires an inference_explanation — "
                "inference must never silently become fact."
            )
        if (
            self.status == UnderstandingClaimStatus.CONTESTED
            and not self.contradicting_evidence_ids
        ):
            raise ValueError(
                "CONTESTED claim requires contradicting evidence or a "
                "contradiction marker."
            )
        if self.status == UnderstandingClaimStatus.UNKNOWN and self.confidence > 0.2:
            raise ValueError("UNKNOWN claim must keep confidence near zero (<= 0.2).")
        return self


class ClaimContradiction(BaseModel):
    id: str
    project_id: str
    claim_a_id: str
    claim_b_id: str
    contradiction_type: str
    severity: str
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str
    resolved: bool = False
    resolution_claim_id: Optional[str] = None


# ── Project understanding models ─────────────────────────────────────

class ProjectPurposeModel(BaseModel):
    project_id: str
    one_sentence_purpose: Optional[str] = None
    problem_being_solved: Optional[str] = None
    primary_users_or_beneficiaries: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    scope_summary: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class CompanyGoalLink(BaseModel):
    id: str
    project_id: str
    company_goal_id: Optional[str] = None
    company_goal_name: str
    connection_mechanism: str
    expected_impact: str
    impact_type: str
    impact_horizon: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ProjectSignificanceModel(BaseModel):
    project_id: str
    company_goal_links: list[CompanyGoalLink] = Field(default_factory=list)
    direct_impact: list[str] = Field(default_factory=list)
    second_order_impact: list[str] = Field(default_factory=list)
    consequence_if_delayed: list[str] = Field(default_factory=list)
    consequence_if_failed: list[str] = Field(default_factory=list)
    strategic_leverage_summary: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class TechnicalMechanismStep(BaseModel):
    order: int
    name: str
    description: str
    input_entities: list[str] = Field(default_factory=list)
    output_entities: list[str] = Field(default_factory=list)
    component_ids: list[str] = Field(default_factory=list)
    interface_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class TechnicalMechanismModel(BaseModel):
    project_id: str
    mechanism_summary: Optional[str] = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    steps: list[TechnicalMechanismStep] = Field(default_factory=list)
    algorithms_or_protocols: list[str] = Field(default_factory=list)
    data_contracts: list[str] = Field(default_factory=list)
    runtime_dependencies: list[str] = Field(default_factory=list)
    observability_signals: list[str] = Field(default_factory=list)
    known_failure_modes: list[str] = Field(default_factory=list)
    unresolved_mechanism_questions: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ArchitectureComponent(BaseModel):
    id: str
    project_id: str
    name: str
    component_type: str
    purpose: Optional[str] = None
    owner_ids: list[str] = Field(default_factory=list)
    input_contracts: list[str] = Field(default_factory=list)
    output_contracts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ArchitectureRelation(BaseModel):
    id: str
    project_id: str
    source_component_id: str
    target_component_id: str
    relation_type: ArchitectureRelationType
    description: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ProjectArchitectureMap(BaseModel):
    project_id: str
    components: list[ArchitectureComponent] = Field(default_factory=list)
    relations: list[ArchitectureRelation] = Field(default_factory=list)
    architecture_summary: Optional[str] = None
    critical_paths: list[list[str]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ProjectGlossaryEntry(BaseModel):
    id: str
    project_id: str
    term: str
    canonical_definition: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    domain: Optional[str] = None
    ambiguity_notes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ProjectAssumption(BaseModel):
    id: str
    project_id: str
    statement: str
    explicit: bool = False
    validated: bool = False
    contested: bool = False
    consequence_if_wrong: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ProjectUnknown(BaseModel):
    id: str
    project_id: str
    kind: ProjectUnknownKind
    question: str
    why_it_matters: str
    affected_claim_ids: list[str] = Field(default_factory=list)
    priority: ResearchPriority
    resolvable_from_internal_evidence: bool = True
    human_decision_required: bool = False
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ProjectResearchTask(BaseModel):
    id: str
    project_id: str
    unknown_id: str
    query: str
    priority: ResearchPriority
    preferred_source_types: list[EvidenceSourceType] = Field(default_factory=list)
    completion_criteria: str
    max_attempts: int = 3
    status: str = "pending"


class UnderstandingCoverage(BaseModel):
    purpose_coverage: float = 0.0
    significance_coverage: float = 0.0
    technical_mechanism_coverage: float = 0.0
    architecture_coverage: float = 0.0
    dependency_coverage: float = 0.0
    ownership_coverage: float = 0.0
    success_criteria_coverage: float = 0.0
    glossary_coverage: float = 0.0
    overall_coverage: float = 0.0
    evidence_freshness: float = 0.0
    contradiction_penalty: float = 0.0

    @model_validator(mode="after")
    def _check_bounds(self) -> "UnderstandingCoverage":
        for name in type(self).model_fields:
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be within [0, 1], got {value}.")
        return self


class ProjectUnderstandingSnapshot(BaseModel):
    id: str
    project_id: str
    as_of: datetime
    purpose: ProjectPurposeModel
    significance: ProjectSignificanceModel
    technical_mechanism: TechnicalMechanismModel
    architecture: ProjectArchitectureMap
    glossary: list[ProjectGlossaryEntry] = Field(default_factory=list)
    assumptions: list[ProjectAssumption] = Field(default_factory=list)
    claims: list[UnderstandingClaim] = Field(default_factory=list)
    contradictions: list[ClaimContradiction] = Field(default_factory=list)
    unknowns: list[ProjectUnknown] = Field(default_factory=list)
    research_tasks: list[ProjectResearchTask] = Field(default_factory=list)
    coverage: UnderstandingCoverage
    evidence_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ── Claim extraction port ────────────────────────────────────────────

class ProjectClaimExtractor(Protocol):
    """Port for claim extraction. The domain layer never hardwires an LLM."""

    def extract_claims(
        self,
        *,
        project_id: str,
        evidence: list[ProjectEvidence],
        as_of: datetime,
    ) -> list[UnderstandingClaim]:
        ...


# ── Helpers ──────────────────────────────────────────────────────────

FRESHNESS_HALF_LIFE_DAYS = 45.0
STALE_MECHANISM_AGE_DAYS = 90


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _norm_statement(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _tag_values(claim: UnderstandingClaim, prefix: str) -> list[str]:
    return [t[len(prefix):] for t in claim.tags if t.startswith(prefix)]


def _tag_value(claim: UnderstandingClaim, prefix: str) -> str | None:
    values = _tag_values(claim, prefix)
    return values[0] if values else None


def _usable(claim: UnderstandingClaim) -> bool:
    """Claims usable for canonical model fields: supported, or explicitly
    labeled inferred. Contested/unknown claims never quietly become facts."""
    return claim.status in (
        UnderstandingClaimStatus.SUPPORTED,
        UnderstandingClaimStatus.INFERRED,
    )


def _best_claim(claims: list[UnderstandingClaim]) -> UnderstandingClaim | None:
    """Prefer supported over inferred, then higher confidence. Deterministic."""
    usable = [c for c in claims if _usable(c)]
    if not usable:
        return None
    return sorted(
        usable,
        key=lambda c: (
            c.status != UnderstandingClaimStatus.SUPPORTED,  # supported first
            -c.confidence,
            c.id,
        ),
    )[0]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ── Record accessors (duck-typed) ────────────────────────────────────

def get_record_id(record: object) -> str | None:
    return (
        getattr(record, "id", None)
        or getattr(record, "record_id", None)
        or getattr(record, "source_id", None)
    )


def get_record_content(record: object) -> str:
    return str(
        getattr(record, "content", None)
        or getattr(record, "text", None)
        or getattr(record, "body", None)
        or getattr(record, "summary", None)
        or ""
    )


def get_record_observed_at(record: object) -> datetime | None:
    return (
        getattr(record, "observed_at", None)
        or getattr(record, "timestamp", None)
        or getattr(record, "created_at", None)
    )


def get_record_source_type(record: object) -> EvidenceSourceType:
    raw = getattr(record, "source_type", None) or getattr(record, "type", None)
    value = getattr(raw, "value", raw)
    try:
        return EvidenceSourceType(value)
    except (ValueError, TypeError):
        return EvidenceSourceType.DOCUMENT


def get_record_title(record: object) -> str | None:
    return (
        getattr(record, "title", None)
        or getattr(record, "name", None)
        or getattr(record, "subject", None)
    )


def get_record_metadata(record: object) -> dict:
    meta = getattr(record, "metadata", None)
    return dict(meta) if isinstance(meta, dict) else {}


# ── Evidence normalization ───────────────────────────────────────────

def normalize_project_evidence(
    *,
    project_id: str,
    records: list[object],
    as_of: datetime,
) -> list[ProjectEvidence]:
    """Normalize raw records into ProjectEvidence.

    Conservative rules: records observed after ``as_of`` are excluded (no
    future leakage); empty content is excluded; a missing observed_at
    keeps the record but marks it low-reliability; exact duplicates are
    deduplicated; semantically different statements are never merged.
    """
    seen: set[tuple[str, str, str]] = set()
    evidence: list[ProjectEvidence] = []

    for record in records:
        content = get_record_content(record).strip()
        if not content:
            continue

        observed_at = get_record_observed_at(record)
        if observed_at is not None and observed_at > as_of:
            continue

        if observed_at is None:
            observed_at = as_of
            reliability = 0.2
        else:
            reliability = float(getattr(record, "reliability", 0.6))

        source_type = get_record_source_type(record)
        record_id = get_record_id(record) or hashlib.sha256(
            content.encode()
        ).hexdigest()[:12]

        key = (source_type.value, str(record_id), content)
        if key in seen:
            continue
        seen.add(key)

        entity_ids = list(
            getattr(record, "ontology_object_ids", None)
            or getattr(record, "entity_ids", None)
            or []
        )

        reference = EvidenceReference(
            id=f"ref-{source_type.value}-{record_id}",
            project_id=project_id,
            source_type=source_type,
            source_id=str(record_id),
            title=get_record_title(record),
            excerpt=content[:300],
            observed_at=observed_at,
            ingested_at=as_of,
            author_or_system=getattr(record, "author", None),
            reliability=_clamp01(reliability),
            relevance=_clamp01(float(getattr(record, "relevance", 0.6))),
            ontology_object_ids=entity_ids,
            metadata=get_record_metadata(record),
        )
        evidence.append(
            ProjectEvidence(
                id=f"pev-{source_type.value}-{record_id}",
                project_id=project_id,
                content=content,
                source=reference,
                tags=list(getattr(record, "tags", []) or []),
                entity_ids=entity_ids,
                normalized_at=as_of,
            )
        )
    return evidence


# ── Claim consolidation ──────────────────────────────────────────────

def consolidate_claims(
    *,
    claims: list[UnderstandingClaim],
    evidence_by_id: dict[str, ProjectEvidence],
) -> list[UnderstandingClaim]:
    """Merge exact duplicate claims (same type + normalized statement),
    union their evidence, and recompute confidence from evidence weight.

    Opposing claims have different statements and therefore never merge.
    Confidence: 1 - exp(-Σ reliability·relevance), clamped to [0, 0.99] —
    never 1.0.
    """
    grouped: dict[tuple[str, str], list[UnderstandingClaim]] = {}
    order: list[tuple[str, str]] = []
    for claim in claims:
        key = (claim.claim_type.value, _norm_statement(claim.statement))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(claim)

    consolidated: list[UnderstandingClaim] = []
    for key in order:
        group = grouped[key]
        base = group[0]

        evidence_ids: list[str] = []
        contradicting: list[str] = []
        tags: list[str] = []
        for claim in group:
            for eid in claim.evidence_ids:
                if eid not in evidence_ids:
                    evidence_ids.append(eid)
            for eid in claim.contradicting_evidence_ids:
                if eid not in contradicting:
                    contradicting.append(eid)
            for tag in claim.tags:
                if tag not in tags:
                    tags.append(tag)

        statuses = {c.status for c in group}
        if UnderstandingClaimStatus.CONTESTED in statuses:
            status = UnderstandingClaimStatus.CONTESTED
        elif UnderstandingClaimStatus.SUPPORTED in statuses and evidence_ids:
            status = UnderstandingClaimStatus.SUPPORTED
        else:
            status = base.status

        weighted_support = sum(
            evidence_by_id[eid].source.reliability
            * evidence_by_id[eid].source.relevance
            for eid in evidence_ids
            if eid in evidence_by_id
        )
        confidence = min(0.99, 1.0 - math.exp(-weighted_support))
        if status == UnderstandingClaimStatus.UNKNOWN:
            confidence = min(confidence, 0.1)

        observed_times = [
            evidence_by_id[eid].source.observed_at
            for eid in evidence_ids
            if eid in evidence_by_id
        ]
        last_supported = max(observed_times) if observed_times else base.last_supported_at
        first_observed = min(observed_times) if observed_times else base.first_observed_at

        consolidated.append(
            base.model_copy(
                update={
                    "evidence_ids": evidence_ids,
                    "contradicting_evidence_ids": contradicting,
                    "tags": tags,
                    "status": status,
                    "confidence": confidence,
                    "last_supported_at": last_supported,
                    "first_observed_at": first_observed,
                }
            )
        )
    return consolidated


# ── Contradiction detection ──────────────────────────────────────────

_OPPOSING_PREDICATES: set[frozenset[str]] = {
    frozenset({"included", "excluded"}),
    frozenset({"required", "non_goal"}),
    frozenset({"synchronous", "asynchronous"}),
    frozenset({"replaces", "coexists"}),
}

_EXCLUSIVE_PREFIXES = ("owner=", "deadline=", "interface=")


def _predicates(claim: UnderstandingClaim) -> list[str]:
    return _tag_values(claim, "predicate:")


def _predicates_conflict(pred_a: str, pred_b: str) -> tuple[bool, str]:
    if frozenset({pred_a, pred_b}) in _OPPOSING_PREDICATES:
        return True, f"{pred_a}_vs_{pred_b}"
    for prefix in _EXCLUSIVE_PREFIXES:
        if (
            pred_a.startswith(prefix)
            and pred_b.startswith(prefix)
            and pred_a != pred_b
        ):
            return True, f"conflicting_{prefix.rstrip('=')}"
    return False, ""


def detect_claim_contradictions(
    *,
    claims: list[UnderstandingClaim],
) -> tuple[list[UnderstandingClaim], list[ClaimContradiction]]:
    """Deterministic contradiction detection via shared subject tags and
    opposing predicate values.

    When a contradiction exists: both claims remain, both become
    CONTESTED, a contradiction record is emitted. No winner is chosen.
    """
    contradictions: list[ClaimContradiction] = []
    contested_updates: dict[str, list[str]] = {}

    by_subject: dict[str, list[UnderstandingClaim]] = {}
    for claim in claims:
        subject = _tag_value(claim, "subject:")
        if subject:
            by_subject.setdefault(subject, []).append(claim)

    for subject, group in sorted(by_subject.items()):
        for i, claim_a in enumerate(group):
            for claim_b in group[i + 1:]:
                for pred_a in _predicates(claim_a):
                    for pred_b in _predicates(claim_b):
                        conflict, kind = _predicates_conflict(pred_a, pred_b)
                        if not conflict:
                            continue
                        severity = (
                            "high"
                            if UnderstandingClaimType.SCOPE
                            in (claim_a.claim_type, claim_b.claim_type)
                            else "medium"
                        )
                        contradictions.append(
                            ClaimContradiction(
                                id=f"contra-{claim_a.id}-{claim_b.id}",
                                project_id=claim_a.project_id,
                                claim_a_id=claim_a.id,
                                claim_b_id=claim_b.id,
                                contradiction_type=kind,
                                severity=severity,
                                evidence_ids=(
                                    claim_a.evidence_ids + claim_b.evidence_ids
                                ),
                                explanation=(
                                    f"Subject '{subject}': "
                                    f"'{claim_a.statement}' conflicts with "
                                    f"'{claim_b.statement}' ({kind})."
                                ),
                            )
                        )
                        contested_updates.setdefault(claim_a.id, []).extend(
                            claim_b.evidence_ids or [f"claim:{claim_b.id}"]
                        )
                        contested_updates.setdefault(claim_b.id, []).extend(
                            claim_a.evidence_ids or [f"claim:{claim_a.id}"]
                        )

    updated: list[UnderstandingClaim] = []
    for claim in claims:
        if claim.id in contested_updates:
            extra = [
                eid
                for eid in contested_updates[claim.id]
                if eid not in claim.contradicting_evidence_ids
            ]
            updated.append(
                claim.model_copy(
                    update={
                        "status": UnderstandingClaimStatus.CONTESTED,
                        "contradicting_evidence_ids": (
                            claim.contradicting_evidence_ids + extra
                        ),
                    }
                )
            )
        else:
            updated.append(claim)
    return updated, contradictions


# ── Purpose construction ─────────────────────────────────────────────

def build_project_purpose_model(
    *,
    project_id: str,
    claims: list[UnderstandingClaim],
) -> ProjectPurposeModel:
    """Purpose from supported (or explicitly inferred) claims only.

    A missing supported purpose stays unresolved — never synthesized.
    """
    def of_type(t: UnderstandingClaimType) -> list[UnderstandingClaim]:
        return [c for c in claims if c.claim_type == t]

    purpose_claim = _best_claim(of_type(UnderstandingClaimType.PROJECT_PURPOSE))
    problem_claim = _best_claim(of_type(UnderstandingClaimType.PROBLEM_STATEMENT))
    scope_claims = [c for c in of_type(UnderstandingClaimType.SCOPE) if _usable(c)]
    success_claims = [
        c for c in of_type(UnderstandingClaimType.SUCCESS_CRITERION) if _usable(c)
    ]
    non_goal_claims = [c for c in of_type(UnderstandingClaimType.NON_GOAL) if _usable(c)]
    impact_claims = [
        c for c in of_type(UnderstandingClaimType.CUSTOMER_IMPACT) if _usable(c)
    ]

    beneficiaries: list[str] = []
    for claim in impact_claims:
        for value in _tag_values(claim, "beneficiary:"):
            if value not in beneficiaries:
                beneficiaries.append(value)

    used = (
        ([purpose_claim] if purpose_claim else [])
        + ([problem_claim] if problem_claim else [])
        + scope_claims + success_claims + non_goal_claims + impact_claims
    )
    evidence_ids: list[str] = []
    for claim in used:
        for eid in claim.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    return ProjectPurposeModel(
        project_id=project_id,
        one_sentence_purpose=purpose_claim.statement if purpose_claim else None,
        problem_being_solved=problem_claim.statement if problem_claim else None,
        primary_users_or_beneficiaries=beneficiaries,
        success_criteria=[c.statement for c in success_claims],
        non_goals=[c.statement for c in non_goal_claims],
        scope_summary=(
            "; ".join(c.statement for c in scope_claims) if scope_claims else None
        ),
        evidence_ids=evidence_ids,
        confidence=purpose_claim.confidence if purpose_claim else 0.0,
    )


# ── Significance construction ────────────────────────────────────────

_MIN_MECHANISM_WORDS = 6


def build_project_significance_model(
    *,
    project_id: str,
    claims: list[UnderstandingClaim],
) -> ProjectSignificanceModel:
    """Company alignment must explain the mechanism. Vague slogans
    ('supports growth') do not qualify as a goal link."""
    alignment_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.COMPANY_ALIGNMENT and _usable(c)
    ]

    goal_links: list[CompanyGoalLink] = []
    for claim in alignment_claims:
        if len(claim.statement.split()) < _MIN_MECHANISM_WORDS:
            continue  # no mechanism explained — not a usable alignment link
        goal_links.append(
            CompanyGoalLink(
                id=f"goal-link-{claim.id}",
                project_id=project_id,
                company_goal_id=_tag_value(claim, "goal_id:"),
                company_goal_name=_tag_value(claim, "goal:") or "unspecified-goal",
                connection_mechanism=claim.statement,
                expected_impact=_tag_value(claim, "impact:") or claim.statement,
                impact_type=_tag_value(claim, "impact_type:") or "strategic",
                impact_horizon=_tag_value(claim, "horizon:"),
                evidence_ids=claim.evidence_ids,
                confidence=claim.confidence,
            )
        )

    impact_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.CUSTOMER_IMPACT and _usable(c)
    ]
    delayed = [
        v for c in alignment_claims for v in _tag_values(c, "if_delayed:")
    ]
    failed = [v for c in alignment_claims for v in _tag_values(c, "if_failed:")]

    evidence_ids: list[str] = []
    for claim in alignment_claims + impact_claims:
        for eid in claim.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    return ProjectSignificanceModel(
        project_id=project_id,
        company_goal_links=goal_links,
        direct_impact=[c.statement for c in impact_claims],
        second_order_impact=[
            v for c in alignment_claims for v in _tag_values(c, "second_order:")
        ],
        consequence_if_delayed=delayed,
        consequence_if_failed=failed,
        strategic_leverage_summary=(
            goal_links[0].connection_mechanism if goal_links else None
        ),
        evidence_ids=evidence_ids,
        confidence=max((l.confidence for l in goal_links), default=0.0),
    )


# ── Technical mechanism construction ─────────────────────────────────

def build_technical_mechanism_model(
    *,
    project_id: str,
    claims: list[UnderstandingClaim],
) -> TechnicalMechanismModel:
    """Ordered mechanism steps come only from claims carrying an explicit
    ``step_order:`` tag. Unordered mechanism claims are preserved as
    unresolved questions — ordering is never invented."""
    mechanism_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.TECHNICAL_MECHANISM and _usable(c)
    ]
    failure_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.FAILURE_MODE and _usable(c)
    ]
    dependency_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.DEPENDENCY and _usable(c)
    ]
    interface_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.INTERFACE and _usable(c)
    ]
    operational_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.OPERATIONAL_REQUIREMENT
        and _usable(c)
    ]

    steps: list[TechnicalMechanismStep] = []
    unresolved: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []

    for claim in mechanism_claims:
        order_value = _tag_value(claim, "step_order:")
        step_inputs = _tag_values(claim, "input:")
        step_outputs = _tag_values(claim, "output:")
        for entity in step_inputs:
            if entity not in inputs:
                inputs.append(entity)
        for entity in step_outputs:
            if entity not in outputs:
                outputs.append(entity)

        if order_value is None:
            unresolved.append(
                f"Mechanism detail with unresolved ordering: {claim.statement}"
            )
            continue
        steps.append(
            TechnicalMechanismStep(
                order=int(order_value),
                name=claim.statement[:80],
                description=claim.statement,
                input_entities=step_inputs,
                output_entities=step_outputs,
                component_ids=[
                    f"comp-{_slug(v)}" for v in _tag_values(claim, "component:")
                ],
                evidence_ids=claim.evidence_ids,
                confidence=claim.confidence,
            )
        )

    steps.sort(key=lambda s: s.order)

    evidence_ids: list[str] = []
    for claim in (
        mechanism_claims + failure_claims + dependency_claims + interface_claims
    ):
        for eid in claim.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    return TechnicalMechanismModel(
        project_id=project_id,
        mechanism_summary=(
            " → ".join(s.name for s in steps) if steps else None
        ),
        inputs=inputs,
        outputs=outputs,
        steps=steps,
        algorithms_or_protocols=[
            v for c in mechanism_claims for v in _tag_values(c, "protocol:")
        ],
        data_contracts=[c.statement for c in interface_claims],
        runtime_dependencies=[c.statement for c in dependency_claims],
        observability_signals=[
            c.statement for c in operational_claims if "observability" in c.tags
        ],
        known_failure_modes=[c.statement for c in failure_claims],
        unresolved_mechanism_questions=unresolved,
        evidence_ids=evidence_ids,
        confidence=(
            sum(s.confidence for s in steps) / len(steps) if steps else 0.0
        ),
    )


# ── Architecture map construction ────────────────────────────────────

_RELATION_TAG = re.compile(r"^relation:(?P<src>[^=]+)=>(?P<dst>[^=]+)=(?P<type>.+)$")


def build_project_architecture_map(
    *,
    project_id: str,
    claims: list[UnderstandingClaim],
) -> ProjectArchitectureMap:
    """A semantic architecture view built from claims — never the live
    structural graph from Commit 2, and never mutating it."""
    component_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.ARCHITECTURE_COMPONENT
        and _usable(c)
    ]

    components: dict[str, ArchitectureComponent] = {}
    for claim in component_claims:
        name = _tag_value(claim, "component:") or claim.statement[:60]
        comp_id = f"comp-{_slug(name)}"
        owners = _tag_values(claim, "owner:")
        if comp_id in components:
            existing = components[comp_id]
            merged_owners = existing.owner_ids + [
                o for o in owners if o not in existing.owner_ids
            ]
            merged_evidence = existing.evidence_ids + [
                e for e in claim.evidence_ids if e not in existing.evidence_ids
            ]
            components[comp_id] = existing.model_copy(
                update={
                    "owner_ids": merged_owners,
                    "evidence_ids": merged_evidence,
                    "confidence": max(existing.confidence, claim.confidence),
                }
            )
        else:
            components[comp_id] = ArchitectureComponent(
                id=comp_id,
                project_id=project_id,
                name=name,
                component_type=_tag_value(claim, "component_type:") or "service",
                purpose=claim.statement,
                owner_ids=owners,
                evidence_ids=claim.evidence_ids,
                confidence=claim.confidence,
            )

    relations: dict[tuple[str, str, str], ArchitectureRelation] = {}
    for claim in claims:
        if not _usable(claim):
            continue
        for tag in claim.tags:
            match = _RELATION_TAG.match(tag)
            if not match:
                continue
            src_id = f"comp-{_slug(match.group('src'))}"
            dst_id = f"comp-{_slug(match.group('dst'))}"
            try:
                rel_type = ArchitectureRelationType(match.group("type"))
            except ValueError:
                rel_type = ArchitectureRelationType.DEPENDS_ON
            key = (src_id, dst_id, rel_type.value)
            if key in relations:
                existing = relations[key]
                relations[key] = existing.model_copy(
                    update={
                        "evidence_ids": existing.evidence_ids
                        + [
                            e
                            for e in claim.evidence_ids
                            if e not in existing.evidence_ids
                        ],
                        "confidence": max(existing.confidence, claim.confidence),
                    }
                )
            else:
                relations[key] = ArchitectureRelation(
                    id=f"rel-{src_id}-{dst_id}-{rel_type.value}",
                    project_id=project_id,
                    source_component_id=src_id,
                    target_component_id=dst_id,
                    relation_type=rel_type,
                    description=claim.statement,
                    evidence_ids=claim.evidence_ids,
                    confidence=claim.confidence,
                )

    component_list = [components[k] for k in sorted(components)]
    relation_list = [relations[k] for k in sorted(relations)]

    evidence_ids: list[str] = []
    for item in list(component_list) + list(relation_list):
        for eid in item.evidence_ids:
            if eid not in evidence_ids:
                evidence_ids.append(eid)

    return ProjectArchitectureMap(
        project_id=project_id,
        components=component_list,
        relations=relation_list,
        architecture_summary=(
            f"{len(component_list)} component(s), {len(relation_list)} relation(s)."
            if component_list
            else None
        ),
        evidence_ids=evidence_ids,
        confidence=(
            sum(c.confidence for c in component_list) / len(component_list)
            if component_list
            else 0.0
        ),
    )


def find_missing_architecture_references(
    architecture: ProjectArchitectureMap,
) -> list[str]:
    """Component ids referenced by relations but never defined."""
    defined = {c.id for c in architecture.components}
    missing: list[str] = []
    for relation in architecture.relations:
        for comp_id in (relation.source_component_id, relation.target_component_id):
            if comp_id not in defined and comp_id not in missing:
                missing.append(comp_id)
    return missing


# ── Glossary construction ────────────────────────────────────────────

def build_project_glossary(
    *,
    project_id: str,
    claims: list[UnderstandingClaim],
) -> list[ProjectGlossaryEntry]:
    """One entry per term; conflicting definitions are preserved as
    ambiguity notes and no canonical definition is chosen."""
    term_claims = [
        c
        for c in claims
        if c.claim_type == UnderstandingClaimType.TERM_DEFINITION and _usable(c)
    ]

    by_term: dict[str, list[UnderstandingClaim]] = {}
    order: list[str] = []
    for claim in term_claims:
        term = _tag_value(claim, "term:")
        if not term:
            continue
        if term not in by_term:
            by_term[term] = []
            order.append(term)
        by_term[term].append(claim)

    entries: list[ProjectGlossaryEntry] = []
    for term in order:
        group = by_term[term]
        definitions: list[str] = []
        for claim in group:
            normalized = _norm_statement(claim.statement)
            if normalized not in [_norm_statement(d) for d in definitions]:
                definitions.append(claim.statement)
        aliases: list[str] = []
        evidence_ids: list[str] = []
        for claim in group:
            for alias in _tag_values(claim, "alias:"):
                if alias not in aliases:
                    aliases.append(alias)
            for eid in claim.evidence_ids:
                if eid not in evidence_ids:
                    evidence_ids.append(eid)

        contested = any(
            c.status == UnderstandingClaimStatus.CONTESTED for c in group
        )
        if len(definitions) == 1 and not contested:
            canonical: str | None = definitions[0]
            ambiguity: list[str] = []
        else:
            canonical = None
            ambiguity = [
                f"Definition candidate: {d}" for d in definitions
            ] or ["Term is contested."]

        entries.append(
            ProjectGlossaryEntry(
                id=f"gloss-{_slug(term)}",
                project_id=project_id,
                term=term,
                canonical_definition=canonical,
                aliases=aliases,
                domain=_tag_value(group[0], "domain:"),
                ambiguity_notes=ambiguity,
                evidence_ids=evidence_ids,
                confidence=max((c.confidence for c in group), default=0.0),
            )
        )
    return entries


# ── Assumption detection ─────────────────────────────────────────────

def build_project_assumptions(
    *,
    project_id: str,
    claims: list[UnderstandingClaim],
) -> list[ProjectAssumption]:
    assumption_claims = [
        c for c in claims if c.claim_type == UnderstandingClaimType.ASSUMPTION
    ]
    assumptions: list[ProjectAssumption] = []
    for claim in assumption_claims:
        if claim.status == UnderstandingClaimStatus.UNKNOWN:
            continue
        assumptions.append(
            ProjectAssumption(
                id=f"assume-{claim.id}",
                project_id=project_id,
                statement=claim.statement,
                explicit=(
                    "explicit" in claim.tags
                    or claim.status == UnderstandingClaimStatus.SUPPORTED
                ),
                validated="validated" in claim.tags,
                contested=claim.status == UnderstandingClaimStatus.CONTESTED,
                consequence_if_wrong=_tag_value(claim, "if_wrong:"),
                evidence_ids=claim.evidence_ids,
                confidence=claim.confidence,
            )
        )
    return assumptions


# ── Unknown detection ────────────────────────────────────────────────

def identify_project_unknowns(
    *,
    project_id: str,
    purpose: ProjectPurposeModel,
    significance: ProjectSignificanceModel,
    technical_mechanism: TechnicalMechanismModel,
    architecture: ProjectArchitectureMap,
    glossary: list[ProjectGlossaryEntry],
    claims: list[UnderstandingClaim],
    contradictions: list[ClaimContradiction],
    as_of: datetime,
) -> list[ProjectUnknown]:
    """Surface what the system does not know as first-class unknowns."""
    unknowns: list[ProjectUnknown] = []
    claim_types = {c.id: c.claim_type for c in claims}

    def add(
        kind: ProjectUnknownKind,
        question: str,
        why: str,
        priority: ResearchPriority,
        affected: list[str] | None = None,
        human: bool = False,
    ) -> None:
        unknowns.append(
            ProjectUnknown(
                id=f"unk-{project_id}-{kind.value}-{len(unknowns)}",
                project_id=project_id,
                kind=kind,
                question=question,
                why_it_matters=why,
                affected_claim_ids=affected or [],
                priority=priority,
                human_decision_required=human,
                created_at=as_of,
            )
        )

    if purpose.one_sentence_purpose is None:
        add(
            ProjectUnknownKind.MISSING_FACT,
            "What is the purpose of this project?",
            "Without an evidenced purpose, scope, priority, and launch "
            "planning cannot be validated.",
            ResearchPriority.CRITICAL,
        )

    if not significance.company_goal_links:
        add(
            ProjectUnknownKind.MISSING_COMPANY_ALIGNMENT,
            "Which company objective does this project serve, and through "
            "what mechanism?",
            "Alignment determines priority and the consequence of delay.",
            ResearchPriority.HIGH,
        )

    if not purpose.success_criteria:
        add(
            ProjectUnknownKind.MISSING_SUCCESS_CRITERION,
            "What are the explicit success criteria for this project?",
            "Launch readiness cannot be evaluated without success criteria.",
            ResearchPriority.HIGH,
        )

    if not technical_mechanism.steps:
        add(
            ProjectUnknownKind.MISSING_TECHNICAL_MECHANISM,
            "How does the project technically work — inputs, processing "
            "steps, and outputs?",
            "Technical risk cannot be assessed without the mechanism.",
            ResearchPriority.HIGH,
        )

    if technical_mechanism.steps and not technical_mechanism.known_failure_modes:
        add(
            ProjectUnknownKind.MISSING_FACT,
            "What are the known failure modes of the mechanism?",
            "Failure-mode understanding is required for launch safety.",
            ResearchPriority.MEDIUM,
        )

    for missing_id in find_missing_architecture_references(architecture):
        add(
            ProjectUnknownKind.MISSING_DEPENDENCY,
            f"Architecture relations reference undefined component "
            f"'{missing_id}'. What is it and who owns it?",
            "An unresolved component can hide a critical dependency.",
            ResearchPriority.HIGH,
        )

    for component in architecture.components:
        if not component.owner_ids:
            add(
                ProjectUnknownKind.MISSING_OWNER,
                f"Who owns component '{component.name}'?",
                "Unowned components stall decisions and launch escalation.",
                ResearchPriority.HIGH,
            )

    for entry in glossary:
        if entry.canonical_definition is None and entry.ambiguity_notes:
            add(
                ProjectUnknownKind.AMBIGUOUS_TERM,
                f"The term '{entry.term}' has conflicting or unresolved "
                f"definitions. Which is canonical?",
                "Ambiguous terminology corrupts every downstream claim.",
                ResearchPriority.MEDIUM,
            )

    for contradiction in contradictions:
        if contradiction.resolved:
            continue
        involved_types = {
            claim_types.get(contradiction.claim_a_id),
            claim_types.get(contradiction.claim_b_id),
        }
        priority = (
            ResearchPriority.CRITICAL
            if UnderstandingClaimType.SCOPE in involved_types
            or contradiction.severity == "high"
            else ResearchPriority.HIGH
        )
        add(
            ProjectUnknownKind.CONFLICTING_EVIDENCE,
            f"Evidence conflicts: {contradiction.explanation} Which is correct?",
            "Contradictory evidence can invalidate launch scope or planning.",
            priority,
            affected=[contradiction.claim_a_id, contradiction.claim_b_id],
        )

    mechanism_times = [
        c.last_supported_at
        for c in claims
        if c.claim_type == UnderstandingClaimType.TECHNICAL_MECHANISM
        and c.last_supported_at is not None
    ]
    if mechanism_times and all(
        (as_of - t).days > STALE_MECHANISM_AGE_DAYS for t in mechanism_times
    ):
        add(
            ProjectUnknownKind.STALE_UNDERSTANDING,
            "The technical mechanism understanding has not been supported "
            f"by evidence in over {STALE_MECHANISM_AGE_DAYS} days. Is it "
            "still accurate?",
            "Stale mechanism understanding misleads risk and readiness.",
            ResearchPriority.MEDIUM,
        )

    return unknowns


# ── Research task generation ─────────────────────────────────────────

_INTERNAL_SOURCE_ORDER: list[EvidenceSourceType] = [
    EvidenceSourceType.ONTOLOGY,
    EvidenceSourceType.DOCUMENT,
    EvidenceSourceType.MEETING,
    EvidenceSourceType.CHAT,
    EvidenceSourceType.MESSAGE,
    EvidenceSourceType.TICKET,
    EvidenceSourceType.CODE,
    EvidenceSourceType.REPOSITORY,
]


def generate_project_research_tasks(
    *,
    unknowns: list[ProjectUnknown],
) -> list[ProjectResearchTask]:
    """One research task per unknown. Internal evidence is always searched
    before human clarification unless the unknown is explicitly a human
    decision (strategic / permission / human intent)."""
    tasks: list[ProjectResearchTask] = []
    for unknown in unknowns:
        if unknown.human_decision_required:
            sources = [EvidenceSourceType.HUMAN_CLARIFICATION]
        else:
            sources = list(_INTERNAL_SOURCE_ORDER)
            sources.append(EvidenceSourceType.EXTERNAL_RESEARCH)
            sources.append(EvidenceSourceType.HUMAN_CLARIFICATION)
        tasks.append(
            ProjectResearchTask(
                id=f"rt-{unknown.id}",
                project_id=unknown.project_id,
                unknown_id=unknown.id,
                query=unknown.question,
                priority=unknown.priority,
                preferred_source_types=sources,
                completion_criteria=(
                    "Evidence-backed answer with source references, or an "
                    "explicit contested/unresolved determination."
                ),
            )
        )
    return tasks


# ── Coverage calculation ─────────────────────────────────────────────

def compute_understanding_coverage(
    *,
    purpose: ProjectPurposeModel,
    significance: ProjectSignificanceModel,
    technical_mechanism: TechnicalMechanismModel,
    architecture: ProjectArchitectureMap,
    glossary: list[ProjectGlossaryEntry],
    claims: list[UnderstandingClaim],
    contradictions: list[ClaimContradiction],
    evidence: list[ProjectEvidence],
    as_of: datetime,
) -> UnderstandingCoverage:
    """Coverage measures whether the domain is understood, not whether the
    prose is long. Contradictions lower coverage without deleting claims."""
    purpose_cov = sum(
        [
            0.25 if purpose.one_sentence_purpose else 0.0,
            0.25 if purpose.problem_being_solved else 0.0,
            0.25 if purpose.success_criteria else 0.0,
            0.25 if purpose.scope_summary else 0.0,
        ]
    )
    significance_cov = (
        0.7 if significance.company_goal_links else 0.0
    ) + (0.3 if significance.direct_impact else 0.0)
    technical_cov = (
        min(1.0, len(technical_mechanism.steps) / 3.0) * 0.6
        + (0.2 if technical_mechanism.known_failure_modes else 0.0)
        + (
            0.2
            if technical_mechanism.inputs and technical_mechanism.outputs
            else 0.0
        )
    )
    architecture_cov = (
        min(1.0, len(architecture.components) / 2.0) * 0.5
        + min(1.0, len(architecture.relations) / 2.0) * 0.5
    )
    dependency_cov = min(
        1.0,
        (
            len(architecture.relations)
            + len(technical_mechanism.runtime_dependencies)
        )
        / 2.0,
    )
    owned = sum(1 for c in architecture.components if c.owner_ids)
    ownership_cov = (
        owned / len(architecture.components) if architecture.components else 0.0
    )
    success_cov = 1.0 if purpose.success_criteria else 0.0
    resolved_terms = sum(1 for g in glossary if g.canonical_definition)
    glossary_cov = resolved_terms / len(glossary) if glossary else 0.0

    if evidence:
        freshness = sum(
            math.exp(
                -max(0.0, (as_of - e.source.observed_at).days)
                / FRESHNESS_HALF_LIFE_DAYS
            )
            for e in evidence
        ) / len(evidence)
    else:
        freshness = 0.0

    contradiction_penalty = min(1.0, 0.15 * len(contradictions))

    component_values = [
        purpose_cov,
        significance_cov,
        technical_cov,
        architecture_cov,
        dependency_cov,
        ownership_cov,
        success_cov,
        glossary_cov,
    ]
    overall = _clamp01(
        (sum(component_values) / len(component_values))
        * (1.0 - contradiction_penalty)
    )

    return UnderstandingCoverage(
        purpose_coverage=_clamp01(purpose_cov),
        significance_coverage=_clamp01(significance_cov),
        technical_mechanism_coverage=_clamp01(technical_cov),
        architecture_coverage=_clamp01(architecture_cov),
        dependency_coverage=_clamp01(dependency_cov),
        ownership_coverage=_clamp01(ownership_cov),
        success_criteria_coverage=_clamp01(success_cov),
        glossary_coverage=_clamp01(glossary_cov),
        overall_coverage=overall,
        evidence_freshness=_clamp01(freshness),
        contradiction_penalty=_clamp01(contradiction_penalty),
    )


# ── Snapshot orchestrator ────────────────────────────────────────────

def build_project_understanding_snapshot(
    *,
    project_id: str,
    records: list[object],
    claim_extractor: ProjectClaimExtractor,
    as_of: datetime,
) -> ProjectUnderstandingSnapshot:
    """Normalize → extract → consolidate → contradict → build models →
    unknowns → research tasks → coverage → immutable snapshot.

    Deterministic for the same inputs; source records are never mutated.
    """
    evidence = normalize_project_evidence(
        project_id=project_id, records=records, as_of=as_of
    )
    evidence_by_id = {e.id: e for e in evidence}

    raw_claims = claim_extractor.extract_claims(
        project_id=project_id, evidence=evidence, as_of=as_of
    )
    claims = consolidate_claims(claims=raw_claims, evidence_by_id=evidence_by_id)
    claims, contradictions = detect_claim_contradictions(claims=claims)

    purpose = build_project_purpose_model(project_id=project_id, claims=claims)
    significance = build_project_significance_model(
        project_id=project_id, claims=claims
    )
    technical_mechanism = build_technical_mechanism_model(
        project_id=project_id, claims=claims
    )
    architecture = build_project_architecture_map(
        project_id=project_id, claims=claims
    )
    glossary = build_project_glossary(project_id=project_id, claims=claims)
    assumptions = build_project_assumptions(project_id=project_id, claims=claims)

    unknowns = identify_project_unknowns(
        project_id=project_id,
        purpose=purpose,
        significance=significance,
        technical_mechanism=technical_mechanism,
        architecture=architecture,
        glossary=glossary,
        claims=claims,
        contradictions=contradictions,
        as_of=as_of,
    )
    research_tasks = generate_project_research_tasks(unknowns=unknowns)

    coverage = compute_understanding_coverage(
        purpose=purpose,
        significance=significance,
        technical_mechanism=technical_mechanism,
        architecture=architecture,
        glossary=glossary,
        claims=claims,
        contradictions=contradictions,
        evidence=evidence,
        as_of=as_of,
    )

    return ProjectUnderstandingSnapshot(
        id=f"pus-{project_id}-{as_of.isoformat()}",
        project_id=project_id,
        as_of=as_of,
        purpose=purpose,
        significance=significance,
        technical_mechanism=technical_mechanism,
        architecture=architecture,
        glossary=glossary,
        assumptions=assumptions,
        claims=claims,
        contradictions=contradictions,
        unknowns=unknowns,
        research_tasks=research_tasks,
        coverage=coverage,
        evidence_ids=[e.id for e in evidence],
        generated_at=as_of,
    )


# ── Snapshot refresh ─────────────────────────────────────────────────

def refresh_project_understanding_snapshot(
    *,
    previous_snapshot: ProjectUnderstandingSnapshot,
    new_records: list[object],
    claim_extractor: ProjectClaimExtractor,
    as_of: datetime,
) -> ProjectUnderstandingSnapshot:
    """Build a new snapshot; carry forward prior claims that no longer
    reappear (marked STALE — or kept CONTESTED, never silently
    overwritten). The previous snapshot is left unchanged."""
    fresh = build_project_understanding_snapshot(
        project_id=previous_snapshot.project_id,
        records=new_records,
        claim_extractor=claim_extractor,
        as_of=as_of,
    )

    fresh_keys = {
        (c.claim_type.value, _norm_statement(c.statement)) for c in fresh.claims
    }
    carried: list[UnderstandingClaim] = []
    for old in previous_snapshot.claims:
        key = (old.claim_type.value, _norm_statement(old.statement))
        if key in fresh_keys:
            continue
        if old.status == UnderstandingClaimStatus.CONTESTED:
            carried.append(old)  # contested claims are never quietly dropped
        else:
            carried.append(
                old.model_copy(update={"status": UnderstandingClaimStatus.STALE})
            )

    fresh_contra_ids = {c.id for c in fresh.contradictions}
    carried_contra = [
        c for c in previous_snapshot.contradictions if c.id not in fresh_contra_ids
    ]

    evidence_ids = list(fresh.evidence_ids)
    for eid in previous_snapshot.evidence_ids:
        if eid not in evidence_ids:
            evidence_ids.append(eid)

    return fresh.model_copy(
        update={
            "claims": fresh.claims + carried,
            "contradictions": fresh.contradictions + carried_contra,
            "evidence_ids": evidence_ids,
        }
    )
