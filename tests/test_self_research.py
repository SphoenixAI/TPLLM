"""Tests for Commit 13 — Self-research & clarification gate.

Internal evidence is searched before humans; research is evidence-bound;
conflicts stay contested; questions are precise, necessary, and never
repeated; the layer touches no application authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lltpm.intelligence import (
    AIQualityRiskType,
    ClarificationNecessity,
    EvidenceReference,
    EvidenceSourceType,
    GapResolutionStatus,
    ProjectArchitectureMap,
    ProjectEvidence,
    ProjectPurposeModel,
    ProjectSignificanceModel,
    ProjectUnderstandingSnapshot,
    ProjectUnknown,
    ProjectUnknownKind,
    QuestionHistoryRecord,
    ResearchPriority,
    ResearchResolution,
    ResearchStage,
    SelfResearchEngine,
    SelfResearchPolicy,
    TechnicalMechanismModel,
    UnderstandingCoverage,
    UnderstandingGap,
    UnderstandingGapType,
    build_clarification_question,
    build_question_signature,
    determine_clarification_necessity,
    execute_self_research,
    is_repeated_question,
    plan_research_queries,
    reconcile_research_evidence,
)

import pytest

_AS_OF = datetime(2026, 7, 1, 12, 0, 0)
_PROJECT = "atlas"


# ── Fixtures ─────────────────────────────────────────────────────────

def _pe(eid: str, content: str, source_type: str = "document",
        source_id: str | None = None, days_ago: int = 1) -> ProjectEvidence:
    reference = EvidenceReference(
        id=f"ref-{eid}",
        project_id=_PROJECT,
        source_type=EvidenceSourceType(source_type),
        source_id=source_id or eid,
        observed_at=_AS_OF - timedelta(days=days_ago),
        ingested_at=_AS_OF,
        reliability=0.8,
        relevance=0.8,
    )
    return ProjectEvidence(
        id=eid, project_id=_PROJECT, content=content, source=reference,
        normalized_at=_AS_OF,
    )


class FakeProvider:
    def __init__(self, provider_id: str, stage: ResearchStage,
                 results: list[ProjectEvidence]):
        self.provider_id = provider_id
        self.stage = stage
        self._results = results
        self.calls = 0

    def search(self, *, project_id, query, as_of, max_results):
        self.calls += 1
        return list(self._results)


class FakeSynthesis:
    """Deterministic: contested when the evidence carries both sides of
    the mobile conflict; otherwise resolves with confidence proportional
    to independent evidence count."""

    def synthesize(self, *, gap, evidence):
        if not evidence:
            return ResearchResolution(
                id=f"res-{gap.id}", gap_id=gap.id,
                status=GapResolutionStatus.UNRESOLVED,
                confidence=0.0, explanation="No evidence found.",
            )
        texts = " ".join(e.content.lower() for e in evidence)
        if "all clients" in texts and ("deferred" in texts or "legacy path" in texts):
            supporting = [
                e.id for e in evidence
                if "deferred" in e.content.lower()
                or "legacy path" in e.content.lower()
            ]
            contradicting = [
                e.id for e in evidence if "all clients" in e.content.lower()
            ]
            return ResearchResolution(
                id=f"res-{gap.id}", gap_id=gap.id,
                status=GapResolutionStatus.CONTESTED,
                confidence=0.4,
                evidence_ids=supporting,
                contradicting_evidence_ids=contradicting,
                remaining_ambiguity=(
                    "Implementation sources defer mobile while the launch "
                    "document requires all clients on the graph service."
                ),
                explanation="Authoritative sources conflict.",
            )
        return ResearchResolution(
            id=f"res-{gap.id}", gap_id=gap.id,
            status=GapResolutionStatus.RESOLVED,
            resolved_statement=evidence[0].content,
            confidence=min(0.99, 0.3 * len(evidence)),
            evidence_ids=[e.id for e in evidence],
            explanation="Internal evidence resolves the gap.",
        )


def _unknown(
    kind=ProjectUnknownKind.MISSING_FACT,
    question="Is the billing contract resolved?",
    priority=ResearchPriority.CRITICAL,
    human=False,
) -> ProjectUnknown:
    return ProjectUnknown(
        id=f"unk-{kind.value}",
        project_id=_PROJECT,
        kind=kind,
        question=question,
        why_it_matters="It gates launch scope and the target-date forecast.",
        priority=priority,
        human_decision_required=human,
        created_at=_AS_OF,
    )


def _understanding(unknowns=()) -> ProjectUnderstandingSnapshot:
    return ProjectUnderstandingSnapshot(
        id="pus-sr", project_id=_PROJECT, as_of=_AS_OF,
        purpose=ProjectPurposeModel(project_id=_PROJECT),
        significance=ProjectSignificanceModel(project_id=_PROJECT),
        technical_mechanism=TechnicalMechanismModel(project_id=_PROJECT),
        architecture=ProjectArchitectureMap(project_id=_PROJECT),
        unknowns=list(unknowns),
        coverage=UnderstandingCoverage(),
        generated_at=_AS_OF,
    )


def _gap(
    question="Is mobile included in Atlas v1?",
    gap_type=UnderstandingGapType.FACTUAL,
    impact=0.9,
    human=False,
) -> UnderstandingGap:
    return UnderstandingGap(
        id="gap-1", project_id=_PROJECT, gap_type=gap_type,
        question=question,
        why_it_matters="Changes required launch scope and the forecast.",
        impact_score=impact, current_confidence=0.0,
        human_decision_required=human, created_at=_AS_OF,
    )


def _run(gap, providers, policy=None):
    policy = policy or SelfResearchPolicy()
    queries = plan_research_queries(
        gap=gap, understanding=_understanding(), policy=policy,
    )
    return execute_self_research(
        gap=gap, queries=queries, providers=providers,
        synthesis_provider=FakeSynthesis(), policy=policy, as_of=_AS_OF,
    )


# ── Test 1: Internal sources before human ────────────────────────────

class TestStageOrder:
    def test_ontology_searched_first_never_human(self):
        ontology = FakeProvider(
            "onto", ResearchStage.ONTOLOGY,
            [_pe("e1", "graph service entity", "ontology")],
        )
        docs = FakeProvider(
            "docs", ResearchStage.INTERNAL_DOCUMENTS,
            [_pe("e2", "launch plan detail")],
        )
        resolution, attempts, evidence = _run(_gap(), [docs, ontology])
        assert attempts[0].stage == ResearchStage.ONTOLOGY
        assert all(
            a.stage != ResearchStage.HUMAN_CLARIFICATION for a in attempts
        )


# ── Test 2–3: Resolution and early stop ──────────────────────────────

class TestResolution:
    def test_internal_documents_resolve_gap(self):
        docs = FakeProvider(
            "docs", ResearchStage.INTERNAL_DOCUMENTS,
            [
                _pe("e1", "Mobile migration is scheduled post-launch"),
                _pe("e2", "Roadmap confirms mobile timeline"),
                _pe("e3", "Ticket confirms the mobile rollout order"),
            ],
        )
        resolution, attempts, evidence = _run(_gap(), [docs])
        assert resolution.status == GapResolutionStatus.RESOLVED
        assert resolution.confidence >= 0.7
        assert resolution.evidence_ids

    def test_search_stops_after_threshold(self):
        ontology = FakeProvider(
            "onto", ResearchStage.ONTOLOGY,
            [
                _pe("e1", "answer part one", "ontology"),
                _pe("e2", "answer part two", "ontology"),
                _pe("e3", "answer part three", "ontology"),
            ],
        )
        tickets = FakeProvider("tickets", ResearchStage.TICKETS,
                               [_pe("e4", "ticket noise", "ticket")])
        resolution, attempts, _ = _run(_gap(), [ontology, tickets])
        assert resolution.status == GapResolutionStatus.RESOLVED
        assert tickets.calls == 0  # later stages never ran


# ── Test 4: Duplicates do not inflate confidence ─────────────────────

class TestDeduplication:
    def test_shared_origin_copies_do_not_inflate(self):
        copies = FakeProvider(
            "docs", ResearchStage.INTERNAL_DOCUMENTS,
            [
                _pe("e1", "Mobile is deferred to phase two", source_id="doc-9"),
                _pe("e2", "Mobile is deferred to phase two", source_id="doc-9"),
                _pe("e3", "Mobile is deferred to phase two", source_id="doc-9"),
            ],
        )
        resolution, _, evidence = _run(_gap(), [copies])
        # Three copies of one origin count once: confidence 0.3, not 0.9.
        assert resolution.confidence == pytest.approx(0.3)


# ── Test 5: Conflicting evidence stays contested ─────────────────────

class TestConflict:
    def test_conflict_produces_contested(self):
        docs = FakeProvider(
            "docs", ResearchStage.INTERNAL_DOCUMENTS,
            [
                _pe("e-launch", "All clients must use the graph service"),
                _pe("e-ticket", "Mobile migration deferred to post-launch",
                    "ticket"),
            ],
        )
        resolution, _, _ = _run(_gap(), [docs])
        assert resolution.status == GapResolutionStatus.CONTESTED
        assert resolution.contradicting_evidence_ids
        assert resolution.human_question_recommended is True


# ── Test 6: External research policy ─────────────────────────────────

class TestExternalPolicy:
    def test_external_skipped_when_disallowed(self):
        external = FakeProvider(
            "web", ResearchStage.APPROVED_EXTERNAL_RESEARCH,
            [_pe("e1", "external result", "external_research")],
        )
        policy = SelfResearchPolicy(allow_external_research=False)
        resolution, attempts, _ = _run(_gap(), [external], policy=policy)
        assert external.calls == 0
        assert resolution.status == GapResolutionStatus.UNRESOLVED


# ── Test 7–9: Clarification necessity ────────────────────────────────

class TestNecessity:
    def test_resolved_gap_needs_no_clarification(self):
        docs = FakeProvider(
            "docs", ResearchStage.INTERNAL_DOCUMENTS,
            [_pe(f"e{i}", f"independent confirmation {i}") for i in range(3)],
        )
        gap = _gap()
        resolution, _, _ = _run(gap, [docs])
        necessity = determine_clarification_necessity(
            gap=gap, resolution=resolution, policy=SelfResearchPolicy(),
        )
        assert necessity == ClarificationNecessity.NOT_NEEDED

    def test_high_impact_unresolved_requires_clarification(self):
        gap = _gap(impact=0.9)
        resolution, _, _ = _run(gap, [])
        necessity = determine_clarification_necessity(
            gap=gap, resolution=resolution, policy=SelfResearchPolicy(),
        )
        assert necessity == ClarificationNecessity.REQUIRED

    def test_strategic_intent_requires_decision(self):
        gap = _gap(
            question="Should mobile be descoped from v1?",
            gap_type=UnderstandingGapType.STRATEGIC_INTENT,
            human=True,
        )
        resolution, _, _ = _run(gap, [])
        assert resolution.status == GapResolutionStatus.HUMAN_DECISION_REQUIRED
        necessity = determine_clarification_necessity(
            gap=gap, resolution=resolution, policy=SelfResearchPolicy(),
        )
        assert necessity == ClarificationNecessity.STRATEGIC_DECISION_REQUIRED


# ── Test 10–11: Question construction ────────────────────────────────

class TestQuestionConstruction:
    def _contested_setup(self, question="Is mobile included in Atlas v1?"):
        evidence = {
            "e-launch": _pe("e-launch", "All clients must use the graph service"),
            "e-ticket": _pe("e-ticket", "Mobile migration deferred to post-launch",
                            "ticket"),
        }
        gap = _gap(question=question)
        resolution = reconcile_research_evidence(
            gap=gap, evidence=list(evidence.values()),
            synthesis_provider=FakeSynthesis(),
        )
        return gap, resolution, evidence

    def test_broad_question_rewritten_when_evidence_exists(self):
        gap, resolution, evidence = self._contested_setup(
            question="What is Atlas?"
        )
        question = build_clarification_question(
            gap=gap, resolution=resolution, evidence_by_id=evidence,
            necessity=ClarificationNecessity.REQUIRED, now=_AS_OF,
        )
        assert not question.question.lower().startswith("what is")
        assert "evidence found" in question.question.lower()

    def test_question_includes_findings_and_ambiguity(self):
        gap, resolution, evidence = self._contested_setup()
        question = build_clarification_question(
            gap=gap, resolution=resolution, evidence_by_id=evidence,
            necessity=ClarificationNecessity.REQUIRED, now=_AS_OF,
        )
        assert question.what_the_ai_already_found
        assert "deferred" in question.what_the_ai_already_found
        assert question.remaining_ambiguity
        assert question.why_the_answer_matters
        assert question.evidence_ids

    def test_not_needed_raises(self):
        gap, resolution, evidence = self._contested_setup()
        with pytest.raises(ValueError, match="not needed"):
            build_clarification_question(
                gap=gap, resolution=resolution, evidence_by_id=evidence,
                necessity=ClarificationNecessity.NOT_NEEDED, now=_AS_OF,
            )


# ── Test 12–14: Signature and repetition ─────────────────────────────

class TestQuestionRepetition:
    def test_signature_deterministic(self):
        a = build_question_signature(
            project_id=_PROJECT, gap_type=UnderstandingGapType.SCOPE,
            normalized_subject="mobile migration v1",
            unresolved_contrast="deferred vs required",
        )
        b = build_question_signature(
            project_id=_PROJECT, gap_type=UnderstandingGapType.SCOPE,
            normalized_subject="Mobile  Migration V1",
            unresolved_contrast="deferred vs required",
        )
        c = build_question_signature(
            project_id=_PROJECT, gap_type=UnderstandingGapType.SCOPE,
            normalized_subject="billing contract",
            unresolved_contrast="deferred vs required",
        )
        assert a == b
        assert a != c

    def _question(self):
        evidence = {
            "e-launch": _pe("e-launch", "All clients must use the graph service"),
            "e-ticket": _pe("e-ticket", "Mobile migration deferred", "ticket"),
        }
        gap = _gap()
        resolution = reconcile_research_evidence(
            gap=gap, evidence=list(evidence.values()),
            synthesis_provider=FakeSynthesis(),
        )
        return build_clarification_question(
            gap=gap, resolution=resolution, evidence_by_id=evidence,
            necessity=ClarificationNecessity.REQUIRED, now=_AS_OF,
        )

    def test_repeated_question_blocked(self):
        question = self._question()
        history = [
            QuestionHistoryRecord(
                id="h1", project_id=_PROJECT,
                question_signature=question.question_signature,
                question_id="old-q",
                asked_at=_AS_OF - timedelta(days=10),
                answered_at=_AS_OF - timedelta(days=9),
            )
        ]
        assert is_repeated_question(
            question=question, history=history, now=_AS_OF,
        ) is True

    def test_repeated_allowed_when_prior_answer_stale(self):
        question = self._question()
        history = [
            QuestionHistoryRecord(
                id="h1", project_id=_PROJECT,
                question_signature=question.question_signature,
                question_id="old-q",
                asked_at=_AS_OF - timedelta(days=250),
                answered_at=_AS_OF - timedelta(days=200),
            )
        ]
        assert is_repeated_question(
            question=question, history=history, now=_AS_OF,
        ) is False


# ── Test 15: Determinism ─────────────────────────────────────────────

class TestDeterminism:
    def _engine(self):
        docs = FakeProvider(
            "docs", ResearchStage.INTERNAL_DOCUMENTS,
            [
                _pe("e-launch", "All clients must use the graph service"),
                _pe("e-ticket", "Mobile migration deferred to post-launch",
                    "ticket"),
            ],
        )
        return SelfResearchEngine(
            providers=[docs], synthesis_provider=FakeSynthesis(),
            policy=SelfResearchPolicy(),
        )

    def _understanding_with_conflict(self):
        return _understanding(
            unknowns=[
                _unknown(
                    kind=ProjectUnknownKind.CONFLICTING_EVIDENCE,
                    question="Is mobile migration launch-blocking?",
                )
            ]
        )

    def test_same_inputs_same_outputs(self):
        run_a = self._engine().resolve_project_gaps(
            understanding=self._understanding_with_conflict(),
            readiness=None, question_history=[], as_of=_AS_OF,
        )
        run_b = self._engine().resolve_project_gaps(
            understanding=self._understanding_with_conflict(),
            readiness=None, question_history=[], as_of=_AS_OF,
        )
        assert [r.model_dump() for r in run_a[0]] == [
            r.model_dump() for r in run_b[0]
        ]
        assert [q.model_dump() for q in run_a[2]] == [
            q.model_dump() for q in run_b[2]
        ]


# ── Test 16: Guardrails ──────────────────────────────────────────────

class TestGuardrails:
    def test_no_application_authority_imports(self):
        import inspect
        import lltpm.intelligence.self_research as sr
        import lltpm.intelligence.ai_quality as aq

        for mod in (sr, aq):
            source = inspect.getsource(mod)
            for pattern in (
                "lltpm.application",
                "apply_calibration",
                "register_active_calibration",
                "transition_status",
                "issue_approval",
            ):
                assert pattern not in source, (
                    f"{mod.__name__} must not reference '{pattern}'."
                )


# ── End-to-end: research before asking one precise question ──────────

class TestEndToEnd:
    def test_self_research_then_one_precise_question(self):
        docs = FakeProvider(
            "docs", ResearchStage.INTERNAL_DOCUMENTS,
            [_pe("e-launch", "All clients must use the graph service")],
        )
        tickets = FakeProvider(
            "tickets", ResearchStage.TICKETS,
            [_pe("e-ticket", "Mobile migration deferred to post-launch",
                 "ticket")],
        )
        meetings = FakeProvider(
            "meetings", ResearchStage.MEETINGS,
            [_pe("e-meeting", "Mobile remains on the legacy path during "
                              "phase one", "meeting")],
        )
        engine = SelfResearchEngine(
            providers=[docs, tickets, meetings],
            synthesis_provider=FakeSynthesis(),
            policy=SelfResearchPolicy(),
        )
        understanding = _understanding(
            unknowns=[
                _unknown(
                    kind=ProjectUnknownKind.CONFLICTING_EVIDENCE,
                    question="Is mobile included in Atlas v1?",
                )
            ]
        )
        resolutions, attempts, questions, flags = engine.resolve_project_gaps(
            understanding=understanding, readiness=None,
            question_history=[], as_of=_AS_OF,
        )

        # Research ran internally across stages before any question.
        assert attempts
        assert all(
            a.stage != ResearchStage.HUMAN_CLARIFICATION for a in attempts
        )

        # The conflict stays contested — never averaged away.
        assert resolutions[0].status == GapResolutionStatus.CONTESTED

        # Exactly one precise, evidence-grounded question.
        assert len(questions) == 1
        question = questions[0]
        assert question.question != "What is Atlas?"
        assert "deferred" in question.what_the_ai_already_found
        assert "all clients" in question.what_the_ai_already_found.lower()
        assert question.necessity == ClarificationNecessity.REQUIRED
        assert question.evidence_ids
        assert not flags
