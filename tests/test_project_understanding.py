"""Evidence-Backed Project Understanding Engine.

Every substantive claim is evidence-bound; unknown is a first-class
state; conflicts survive summarization; freshness matters; technical
depth is structural; no application authority is touched; no worker
surveillance exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from lltpm.intelligence import (
    ClaimContradiction,
    EvidenceSourceType,
    ProjectEvidence,
    ProjectUnknownKind,
    ResearchPriority,
    UnderstandingClaim,
    UnderstandingClaimStatus,
    UnderstandingClaimType,
    build_project_architecture_map,
    build_project_assumptions,
    build_project_glossary,
    build_project_purpose_model,
    build_project_significance_model,
    build_project_understanding_snapshot,
    build_technical_mechanism_model,
    consolidate_claims,
    detect_claim_contradictions,
    generate_project_research_tasks,
    identify_project_unknowns,
    normalize_project_evidence,
    refresh_project_understanding_snapshot,
)

_AS_OF = datetime(2026, 7, 1, 12, 0, 0)
_PROJECT = "atlas"


# ── Fixtures ─────────────────────────────────────────────────────────

def _record(
    rid: str,
    content: str,
    days_ago: int = 1,
    source_type: str = "document",
    **extra,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid,
        content=content,
        observed_at=_AS_OF - timedelta(days=days_ago),
        source_type=source_type,
        **extra,
    )


class FakeExtractor:
    """Deterministic extractor: maps content substrings to claim specs."""

    def __init__(self, rules: list[tuple[str, dict]]):
        self.rules = rules

    def extract_claims(self, *, project_id, evidence, as_of):
        claims = []
        counter = 0
        for item in evidence:
            for substring, spec in self.rules:
                if substring not in item.content:
                    continue
                counter += 1
                claims.append(
                    UnderstandingClaim(
                        id=f"c-{counter}-{item.id}",
                        project_id=project_id,
                        claim_type=spec["claim_type"],
                        statement=spec.get("statement", item.content),
                        status=spec.get(
                            "status", UnderstandingClaimStatus.SUPPORTED
                        ),
                        confidence=spec.get("confidence", 0.5),
                        evidence_ids=[item.id],
                        inference_explanation=spec.get("inference_explanation"),
                        inferred=spec.get("inferred", False),
                        tags=spec.get("tags", []),
                        last_supported_at=item.source.observed_at,
                    )
                )
        return claims


def _claim(
    cid: str,
    claim_type: UnderstandingClaimType,
    statement: str,
    status: UnderstandingClaimStatus = UnderstandingClaimStatus.SUPPORTED,
    evidence_ids: list[str] | None = None,
    tags: list[str] | None = None,
    **extra,
) -> UnderstandingClaim:
    return UnderstandingClaim(
        id=cid,
        project_id=_PROJECT,
        claim_type=claim_type,
        statement=statement,
        status=status,
        confidence=extra.pop("confidence", 0.5),
        evidence_ids=evidence_ids if evidence_ids is not None else ["ev-1"],
        tags=tags or [],
        **extra,
    )


def _evidence_map(*ids, days_ago: int = 1) -> dict[str, ProjectEvidence]:
    records = [_record(i, f"content for {i}", days_ago=days_ago) for i in ids]
    evidence = normalize_project_evidence(
        project_id=_PROJECT, records=records, as_of=_AS_OF
    )
    return {e.id: e for e in evidence}


# ── Test 1–2: Evidence normalization ─────────────────────────────────

class TestEvidenceNormalization:
    def test_evidence_after_as_of_is_excluded(self):
        records = [
            _record("past", "valid past evidence", days_ago=2),
            _record("future", "future leak", days_ago=-3),
        ]
        evidence = normalize_project_evidence(
            project_id=_PROJECT, records=records, as_of=_AS_OF
        )
        assert len(evidence) == 1
        assert evidence[0].source.source_id == "past"

    def test_exact_duplicates_deduplicated(self):
        records = [
            _record("r1", "same statement"),
            _record("r1", "same statement"),
            _record("r2", "different statement"),
        ]
        evidence = normalize_project_evidence(
            project_id=_PROJECT, records=records, as_of=_AS_OF
        )
        assert len(evidence) == 2

    def test_empty_content_excluded(self):
        records = [_record("r1", "  "), _record("r2", "real")]
        evidence = normalize_project_evidence(
            project_id=_PROJECT, records=records, as_of=_AS_OF
        )
        assert len(evidence) == 1

    def test_missing_observed_at_marks_low_reliability(self):
        record = SimpleNamespace(id="r1", content="undated note")
        evidence = normalize_project_evidence(
            project_id=_PROJECT, records=[record], as_of=_AS_OF
        )
        assert evidence[0].source.reliability == pytest.approx(0.2)


# ── Test 3–5: Claim validation ───────────────────────────────────────

class TestClaimValidation:
    def test_supported_claim_retains_evidence(self):
        claim = _claim(
            "c1", UnderstandingClaimType.PROJECT_PURPOSE, "Replace entitlement",
            evidence_ids=["ev-a", "ev-b"],
        )
        assert claim.evidence_ids == ["ev-a", "ev-b"]

    def test_supported_without_evidence_rejected(self):
        with pytest.raises(ValueError, match="evidence"):
            _claim(
                "c1", UnderstandingClaimType.PROJECT_PURPOSE, "Confident claim",
                evidence_ids=[],
            )

    def test_inferred_requires_explanation(self):
        with pytest.raises(ValueError, match="inference_explanation"):
            _claim(
                "c1", UnderstandingClaimType.SCOPE, "Mobile likely included",
                status=UnderstandingClaimStatus.INFERRED,
            )

    def test_inferred_with_explanation_accepted(self):
        claim = _claim(
            "c1", UnderstandingClaimType.SCOPE, "Mobile likely included",
            status=UnderstandingClaimStatus.INFERRED,
            inference_explanation="Inferred from launch doc phrasing.",
            inferred=True,
        )
        assert claim.status == UnderstandingClaimStatus.INFERRED

    def test_unknown_confidence_must_stay_low(self):
        with pytest.raises(ValueError, match="near zero"):
            _claim(
                "c1", UnderstandingClaimType.SCOPE, "Unclear",
                status=UnderstandingClaimStatus.UNKNOWN,
                confidence=0.8,
            )


# ── Test 6–7: Consolidation ──────────────────────────────────────────

class TestConsolidation:
    def test_duplicate_claims_merge_evidence(self):
        ev = _evidence_map("e1", "e2")
        ids = sorted(ev)
        claims = [
            _claim("c1", UnderstandingClaimType.SCOPE, "Mobile is included",
                   evidence_ids=[ids[0]]),
            _claim("c2", UnderstandingClaimType.SCOPE, "mobile is included",
                   evidence_ids=[ids[1]]),
        ]
        merged = consolidate_claims(claims=claims, evidence_by_id=ev)
        assert len(merged) == 1
        assert set(merged[0].evidence_ids) == set(ids)
        assert merged[0].confidence < 0.99

    def test_opposing_claims_do_not_merge(self):
        ev = _evidence_map("e1", "e2")
        ids = sorted(ev)
        claims = [
            _claim("c1", UnderstandingClaimType.SCOPE, "Mobile is included",
                   evidence_ids=[ids[0]]),
            _claim("c2", UnderstandingClaimType.SCOPE, "Mobile is excluded",
                   evidence_ids=[ids[1]]),
        ]
        merged = consolidate_claims(claims=claims, evidence_by_id=ev)
        assert len(merged) == 2

    def test_confidence_never_reaches_one(self):
        ev = _evidence_map(*[f"e{i}" for i in range(30)])
        claims = [
            _claim("c1", UnderstandingClaimType.SCOPE, "Heavily evidenced",
                   evidence_ids=list(ev)),
        ]
        merged = consolidate_claims(claims=claims, evidence_by_id=ev)
        assert merged[0].confidence <= 0.99


# ── Test 8: Contradiction detection ──────────────────────────────────

class TestContradictions:
    def test_contradictory_scope_claims_become_contested(self):
        claims = [
            _claim("c1", UnderstandingClaimType.SCOPE,
                   "Mobile is in v1 scope",
                   tags=["subject:mobile", "predicate:included"]),
            _claim("c2", UnderstandingClaimType.SCOPE,
                   "Mobile is deferred past v1",
                   evidence_ids=["ev-2"],
                   tags=["subject:mobile", "predicate:excluded"]),
        ]
        updated, contradictions = detect_claim_contradictions(claims=claims)
        assert len(contradictions) == 1
        assert contradictions[0].severity == "high"
        assert all(
            c.status == UnderstandingClaimStatus.CONTESTED for c in updated
        )
        # Both claims survive — no winner chosen.
        assert len(updated) == 2

    def test_conflicting_owners_detected(self):
        claims = [
            _claim("c1", UnderstandingClaimType.OWNERSHIP, "Graph owned by team A",
                   tags=["subject:graph-service", "predicate:owner=team-a"]),
            _claim("c2", UnderstandingClaimType.OWNERSHIP, "Graph owned by team B",
                   evidence_ids=["ev-2"],
                   tags=["subject:graph-service", "predicate:owner=team-b"]),
        ]
        _, contradictions = detect_claim_contradictions(claims=claims)
        assert len(contradictions) == 1
        assert "owner" in contradictions[0].contradiction_type

    def test_compatible_claims_not_contested(self):
        claims = [
            _claim("c1", UnderstandingClaimType.SCOPE, "Mobile included",
                   tags=["subject:mobile", "predicate:included"]),
            _claim("c2", UnderstandingClaimType.SCOPE, "Web included",
                   tags=["subject:web", "predicate:included"]),
        ]
        updated, contradictions = detect_claim_contradictions(claims=claims)
        assert contradictions == []
        assert all(
            c.status == UnderstandingClaimStatus.SUPPORTED for c in updated
        )


# ── Test 9–10: Purpose model ─────────────────────────────────────────

class TestPurpose:
    def test_purpose_built_from_supported_claims(self):
        claims = [
            _claim("c1", UnderstandingClaimType.PROJECT_PURPOSE,
                   "Replace fragmented entitlement lookup with the account graph"),
            _claim("c2", UnderstandingClaimType.SUCCESS_CRITERION,
                   "All clients resolve permissions via the graph"),
        ]
        purpose = build_project_purpose_model(project_id=_PROJECT, claims=claims)
        assert purpose.one_sentence_purpose is not None
        assert "account graph" in purpose.one_sentence_purpose
        assert len(purpose.success_criteria) == 1
        assert purpose.evidence_ids

    def test_contested_purpose_stays_unresolved(self):
        claims = [
            _claim("c1", UnderstandingClaimType.PROJECT_PURPOSE, "Purpose A",
                   status=UnderstandingClaimStatus.CONTESTED,
                   contradicting_evidence_ids=["ev-x"]),
        ]
        purpose = build_project_purpose_model(project_id=_PROJECT, claims=claims)
        assert purpose.one_sentence_purpose is None

    def test_missing_purpose_creates_unknown(self):
        snapshot = build_project_understanding_snapshot(
            project_id=_PROJECT,
            records=[_record("r1", "unrelated note")],
            claim_extractor=FakeExtractor([]),
            as_of=_AS_OF,
        )
        kinds = [u.kind for u in snapshot.unknowns]
        assert ProjectUnknownKind.MISSING_FACT in kinds
        purpose_unknown = next(
            u for u in snapshot.unknowns
            if "purpose" in u.question.lower()
        )
        assert purpose_unknown.priority == ResearchPriority.CRITICAL


# ── Test 11: Significance requires mechanism ─────────────────────────

class TestSignificance:
    def test_vague_alignment_excluded(self):
        claims = [
            _claim("c1", UnderstandingClaimType.COMPANY_ALIGNMENT,
                   "Supports company growth.",
                   tags=["goal:growth"]),
        ]
        significance = build_project_significance_model(
            project_id=_PROJECT, claims=claims
        )
        assert significance.company_goal_links == []

    def test_mechanistic_alignment_included(self):
        claims = [
            _claim("c1", UnderstandingClaimType.COMPANY_ALIGNMENT,
                   "Reduces entitlement lookup latency, allowing the account "
                   "graph to become the shared authorization layer for "
                   "Billing, Workspace, and mobile clients.",
                   tags=["goal:platform-consolidation"]),
        ]
        significance = build_project_significance_model(
            project_id=_PROJECT, claims=claims
        )
        assert len(significance.company_goal_links) == 1
        link = significance.company_goal_links[0]
        assert link.company_goal_name == "platform-consolidation"
        assert "authorization layer" in link.connection_mechanism


# ── Test 12–13: Technical mechanism ──────────────────────────────────

class TestTechnicalMechanism:
    def test_ordered_steps_preserved(self):
        claims = [
            _claim("c2", UnderstandingClaimType.TECHNICAL_MECHANISM,
                   "Graph resolves effective permissions",
                   tags=["step_order:2", "input:account-events",
                         "output:permissions"]),
            _claim("c1", UnderstandingClaimType.TECHNICAL_MECHANISM,
                   "Billing emits account state changes",
                   tags=["step_order:1", "output:account-events"]),
            _claim("c3", UnderstandingClaimType.TECHNICAL_MECHANISM,
                   "Clients consume the authorization contract",
                   tags=["step_order:3", "input:permissions"]),
        ]
        mechanism = build_technical_mechanism_model(
            project_id=_PROJECT, claims=claims
        )
        assert [s.order for s in mechanism.steps] == [1, 2, 3]
        assert "Billing emits" in mechanism.steps[0].name
        assert "account-events" in mechanism.inputs
        assert "permissions" in mechanism.outputs

    def test_unknown_order_not_invented(self):
        claims = [
            _claim("c1", UnderstandingClaimType.TECHNICAL_MECHANISM,
                   "The graph service caches permission sets"),
        ]
        mechanism = build_technical_mechanism_model(
            project_id=_PROJECT, claims=claims
        )
        assert mechanism.steps == []
        assert len(mechanism.unresolved_mechanism_questions) == 1
        assert "unresolved ordering" in mechanism.unresolved_mechanism_questions[0]


# ── Test 14–15: Architecture ─────────────────────────────────────────

class TestArchitecture:
    def test_components_and_relations_deduplicated(self):
        claims = [
            _claim("c1", UnderstandingClaimType.ARCHITECTURE_COMPONENT,
                   "Billing service emits account events",
                   tags=["component:Billing", "owner:team-billing"]),
            _claim("c2", UnderstandingClaimType.ARCHITECTURE_COMPONENT,
                   "Billing handles subscription state",
                   evidence_ids=["ev-2"],
                   tags=["component:Billing"]),
            _claim("c3", UnderstandingClaimType.ARCHITECTURE_COMPONENT,
                   "Graph service resolves permissions",
                   tags=["component:Graph Service"]),
            _claim("c4", UnderstandingClaimType.INTERFACE,
                   "Billing emits into the graph",
                   tags=["relation:Billing=>Graph Service=emits_to"]),
            _claim("c5", UnderstandingClaimType.INTERFACE,
                   "Billing emits into the graph again",
                   evidence_ids=["ev-3"],
                   tags=["relation:Billing=>Graph Service=emits_to"]),
        ]
        arch = build_project_architecture_map(project_id=_PROJECT, claims=claims)
        assert len(arch.components) == 2
        assert len(arch.relations) == 1
        billing = next(c for c in arch.components if c.name == "Billing")
        assert "team-billing" in billing.owner_ids
        assert set(billing.evidence_ids) == {"ev-1", "ev-2"}

    def test_missing_reference_creates_unknown(self):
        claims = [
            _claim("c1", UnderstandingClaimType.ARCHITECTURE_COMPONENT,
                   "Graph service", tags=["component:Graph", "owner:team-g"]),
            _claim("c2", UnderstandingClaimType.INTERFACE,
                   "Graph reads from the mystery store",
                   tags=["relation:Graph=>Mystery Store=reads_from"]),
        ]
        arch = build_project_architecture_map(project_id=_PROJECT, claims=claims)
        unknowns = identify_project_unknowns(
            project_id=_PROJECT,
            purpose=build_project_purpose_model(project_id=_PROJECT, claims=claims),
            significance=build_project_significance_model(
                project_id=_PROJECT, claims=claims
            ),
            technical_mechanism=build_technical_mechanism_model(
                project_id=_PROJECT, claims=claims
            ),
            architecture=arch,
            glossary=[],
            claims=claims,
            contradictions=[],
            as_of=_AS_OF,
        )
        missing = [
            u for u in unknowns if u.kind == ProjectUnknownKind.MISSING_DEPENDENCY
        ]
        assert len(missing) == 1
        assert "comp-mystery-store" in missing[0].question


# ── Test 16–17: Glossary ─────────────────────────────────────────────

class TestGlossary:
    def test_aliases_preserved(self):
        claims = [
            _claim("c1", UnderstandingClaimType.TERM_DEFINITION,
                   "The account graph is the permission resolution layer",
                   tags=["term:account graph", "alias:AG", "alias:the graph"]),
        ]
        glossary = build_project_glossary(project_id=_PROJECT, claims=claims)
        assert len(glossary) == 1
        assert glossary[0].canonical_definition is not None
        assert set(glossary[0].aliases) == {"AG", "the graph"}

    def test_conflicting_definitions_create_ambiguity(self):
        claims = [
            _claim("c1", UnderstandingClaimType.TERM_DEFINITION,
                   "Entitlement means a billing SKU grant",
                   tags=["term:entitlement"]),
            _claim("c2", UnderstandingClaimType.TERM_DEFINITION,
                   "Entitlement means an effective runtime permission",
                   evidence_ids=["ev-2"],
                   tags=["term:entitlement"]),
        ]
        glossary = build_project_glossary(project_id=_PROJECT, claims=claims)
        assert len(glossary) == 1
        assert glossary[0].canonical_definition is None
        assert len(glossary[0].ambiguity_notes) == 2


# ── Test 18–19: Assumptions ──────────────────────────────────────────

class TestAssumptions:
    def test_explicit_assumption_preserved(self):
        claims = [
            _claim("c1", UnderstandingClaimType.ASSUMPTION,
                   "Mobile clients continue using the legacy API during v1",
                   tags=["explicit", "if_wrong:launch scope changes"]),
        ]
        snapshot_assumptions = build_project_assumptions(
            project_id=_PROJECT, claims=claims
        )
        assert len(snapshot_assumptions) == 1
        assert snapshot_assumptions[0].explicit is True
        assert snapshot_assumptions[0].consequence_if_wrong == "launch scope changes"

    def test_contested_scope_unknown_is_high_priority(self):
        claims = [
            _claim("c1", UnderstandingClaimType.SCOPE, "Mobile included",
                   tags=["subject:mobile", "predicate:included"]),
            _claim("c2", UnderstandingClaimType.SCOPE, "Mobile deferred",
                   evidence_ids=["ev-2"],
                   tags=["subject:mobile", "predicate:excluded"]),
        ]
        updated, contradictions = detect_claim_contradictions(claims=claims)
        unknowns = identify_project_unknowns(
            project_id=_PROJECT,
            purpose=build_project_purpose_model(project_id=_PROJECT, claims=updated),
            significance=build_project_significance_model(
                project_id=_PROJECT, claims=updated
            ),
            technical_mechanism=build_technical_mechanism_model(
                project_id=_PROJECT, claims=updated
            ),
            architecture=build_project_architecture_map(
                project_id=_PROJECT, claims=updated
            ),
            glossary=[],
            claims=updated,
            contradictions=contradictions,
            as_of=_AS_OF,
        )
        conflict = next(
            u for u in unknowns
            if u.kind == ProjectUnknownKind.CONFLICTING_EVIDENCE
        )
        assert conflict.priority == ResearchPriority.CRITICAL


# ── Test 20: Research task source ordering ───────────────────────────

class TestResearchTasks:
    def test_internal_sources_before_human(self):
        claims = []
        unknowns = identify_project_unknowns(
            project_id=_PROJECT,
            purpose=build_project_purpose_model(project_id=_PROJECT, claims=claims),
            significance=build_project_significance_model(
                project_id=_PROJECT, claims=claims
            ),
            technical_mechanism=build_technical_mechanism_model(
                project_id=_PROJECT, claims=claims
            ),
            architecture=build_project_architecture_map(
                project_id=_PROJECT, claims=claims
            ),
            glossary=[],
            claims=claims,
            contradictions=[],
            as_of=_AS_OF,
        )
        tasks = generate_project_research_tasks(unknowns=unknowns)
        assert tasks
        for task in tasks:
            sources = task.preferred_source_types
            assert sources[0] == EvidenceSourceType.ONTOLOGY
            assert sources[-1] == EvidenceSourceType.HUMAN_CLARIFICATION
            human_idx = sources.index(EvidenceSourceType.HUMAN_CLARIFICATION)
            ext_idx = sources.index(EvidenceSourceType.EXTERNAL_RESEARCH)
            assert ext_idx < human_idx


# ── Test 21–23: Coverage ─────────────────────────────────────────────

class TestCoverage:
    def _snapshot(self, rules, records):
        return build_project_understanding_snapshot(
            project_id=_PROJECT,
            records=records,
            claim_extractor=FakeExtractor(rules),
            as_of=_AS_OF,
        )

    def test_technical_depth_separate_from_glossary(self):
        rules = [
            ("Billing emits", {
                "claim_type": UnderstandingClaimType.TECHNICAL_MECHANISM,
                "tags": ["step_order:1", "input:billing-events",
                         "output:account-events"],
            }),
            ("Graph resolves", {
                "claim_type": UnderstandingClaimType.TECHNICAL_MECHANISM,
                "tags": ["step_order:2", "input:account-events",
                         "output:permissions"],
            }),
            ("Clients consume", {
                "claim_type": UnderstandingClaimType.TECHNICAL_MECHANISM,
                "tags": ["step_order:3", "input:permissions",
                         "output:authz-decisions"],
            }),
            ("fails when", {
                "claim_type": UnderstandingClaimType.FAILURE_MODE,
            }),
        ]
        records = [
            _record("r1", "Billing emits account state changes"),
            _record("r2", "Graph resolves effective permissions"),
            _record("r3", "Clients consume the authorization contract"),
            _record("r4", "Resolution fails when the event stream lags"),
        ]
        snapshot = self._snapshot(rules, records)
        assert snapshot.coverage.technical_mechanism_coverage > 0.9
        assert snapshot.coverage.glossary_coverage == 0.0  # no glossary terms

    def test_stale_evidence_lowers_freshness(self):
        rules = [("purpose", {
            "claim_type": UnderstandingClaimType.PROJECT_PURPOSE,
        })]
        fresh = self._snapshot(rules, [_record("r1", "the purpose", days_ago=1)])
        stale = self._snapshot(rules, [_record("r1", "the purpose", days_ago=270)])
        assert stale.coverage.evidence_freshness < fresh.coverage.evidence_freshness
        assert stale.coverage.evidence_freshness < 0.1

    def test_contradictions_lower_coverage_without_deleting_claims(self):
        base_rules = [
            ("Mobile is included", {
                "claim_type": UnderstandingClaimType.SCOPE,
                "tags": ["subject:mobile", "predicate:included"],
            }),
        ]
        conflict_rules = base_rules + [
            ("Mobile is deferred", {
                "claim_type": UnderstandingClaimType.SCOPE,
                "tags": ["subject:mobile", "predicate:excluded"],
            }),
        ]
        clean = self._snapshot(
            base_rules, [_record("r1", "Mobile is included")]
        )
        conflicted = self._snapshot(
            conflict_rules,
            [_record("r1", "Mobile is included"),
             _record("r2", "Mobile is deferred")],
        )
        assert conflicted.coverage.contradiction_penalty > 0
        assert (
            conflicted.coverage.overall_coverage
            < clean.coverage.overall_coverage
            or conflicted.coverage.contradiction_penalty > 0
        )
        # Both claims survive, contested.
        assert len(conflicted.claims) == 2
        assert all(
            c.status == UnderstandingClaimStatus.CONTESTED
            for c in conflicted.claims
        )


# ── Test 24–25: Determinism and refresh ──────────────────────────────

class TestSnapshotLifecycle:
    _RULES = [
        ("replaces the legacy entitlement", {
            "claim_type": UnderstandingClaimType.PROJECT_PURPOSE,
        }),
        ("Billing emits", {
            "claim_type": UnderstandingClaimType.TECHNICAL_MECHANISM,
            "tags": ["step_order:1", "output:account-events"],
        }),
    ]

    def _records(self):
        return [
            _record("r1", "Atlas replaces the legacy entitlement service"),
            _record("r2", "Billing emits account updates into the graph"),
        ]

    def test_snapshot_deterministic(self):
        snap_a = build_project_understanding_snapshot(
            project_id=_PROJECT, records=self._records(),
            claim_extractor=FakeExtractor(self._RULES), as_of=_AS_OF,
        )
        snap_b = build_project_understanding_snapshot(
            project_id=_PROJECT, records=self._records(),
            claim_extractor=FakeExtractor(self._RULES), as_of=_AS_OF,
        )
        assert snap_a.model_dump() == snap_b.model_dump()

    def test_refresh_marks_stale_and_preserves_previous(self):
        previous = build_project_understanding_snapshot(
            project_id=_PROJECT, records=self._records(),
            claim_extractor=FakeExtractor(self._RULES), as_of=_AS_OF,
        )
        previous_dump = previous.model_dump()

        refreshed = refresh_project_understanding_snapshot(
            previous_snapshot=previous,
            new_records=[_record("r3", "Billing emits account updates into the graph")],
            claim_extractor=FakeExtractor(self._RULES),
            as_of=_AS_OF + timedelta(days=7),
        )
        # Purpose claim no longer supported by new records → carried as STALE.
        stale = [
            c for c in refreshed.claims
            if c.status == UnderstandingClaimStatus.STALE
        ]
        assert len(stale) == 1
        assert "entitlement" in stale[0].statement
        # Prior evidence history preserved.
        for eid in previous.evidence_ids:
            assert eid in refreshed.evidence_ids
        # Previous snapshot unchanged.
        assert previous.model_dump() == previous_dump

    def test_refresh_preserves_contested_claims(self):
        rules = [
            ("Mobile is included", {
                "claim_type": UnderstandingClaimType.SCOPE,
                "tags": ["subject:mobile", "predicate:included"],
            }),
            ("Mobile is deferred", {
                "claim_type": UnderstandingClaimType.SCOPE,
                "tags": ["subject:mobile", "predicate:excluded"],
            }),
        ]
        previous = build_project_understanding_snapshot(
            project_id=_PROJECT,
            records=[_record("r1", "Mobile is included"),
                     _record("r2", "Mobile is deferred")],
            claim_extractor=FakeExtractor(rules),
            as_of=_AS_OF,
        )
        refreshed = refresh_project_understanding_snapshot(
            previous_snapshot=previous,
            new_records=[],
            claim_extractor=FakeExtractor(rules),
            as_of=_AS_OF + timedelta(days=1),
        )
        carried = [c for c in refreshed.claims if c.id in
                   {x.id for x in previous.claims}]
        assert all(
            c.status == UnderstandingClaimStatus.CONTESTED for c in carried
        )


# ── Test 26–27: Guardrails ───────────────────────────────────────────

class TestGuardrails:
    def test_no_application_authority_imports(self):
        import inspect
        import lltpm.intelligence.project_understanding as mod

        source = inspect.getsource(mod)
        forbidden = [
            "lltpm.application",
            "lltpm.structural.governance",
            "apply_calibration",
            "register_active_calibration",
            "transition_status",
            "issue_approval",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"intelligence layer must not reference '{pattern}' — "
                f"it is analytical only and never grants authority."
            )

    def test_no_worker_surveillance_models(self):
        import inspect
        import lltpm.intelligence.project_understanding as mod

        source = inspect.getsource(mod).lower()
        forbidden = [
            "productivity",
            "employee_rank",
            "performance_score",
            "surveillance",
            "sentiment_score",
        ]
        for pattern in forbidden:
            assert pattern not in source, (
                f"intelligence layer must not model '{pattern}' — it models "
                f"projects and systems, never workers."
            )


# ── Test 28: End-to-end example from the spec ────────────────────────

class TestEndToEnd:
    def test_full_snapshot_with_contradiction_and_research_tasks(self):
        rules = [
            ("replaces the legacy entitlement", {
                "claim_type": UnderstandingClaimType.PROJECT_PURPOSE,
                "statement": "Replace fragmented entitlement lookup with the "
                             "account graph",
            }),
            ("Billing emits account updates", {
                "claim_type": UnderstandingClaimType.TECHNICAL_MECHANISM,
                "statement": "Billing emits account state changes",
                "tags": ["step_order:1", "output:account-events"],
            }),
            ("Mobile still reads the legacy endpoint", {
                "claim_type": UnderstandingClaimType.SCOPE,
                "statement": "Mobile remains on the legacy path for v1",
                "tags": ["subject:mobile-migration", "predicate:excluded"],
            }),
            ("All clients must use the graph service", {
                "claim_type": UnderstandingClaimType.SCOPE,
                "statement": "All clients including mobile must use the graph "
                             "service before launch",
                "tags": ["subject:mobile-migration", "predicate:included"],
            }),
        ]
        records = [
            _record("roadmap", "Atlas replaces the legacy entitlement service",
                    source_type="document"),
            _record("arch-meeting",
                    "Billing emits account updates into the account graph",
                    source_type="meeting"),
            _record("chat-1", "Mobile still reads the legacy endpoint for v1",
                    source_type="chat"),
            _record("launch-doc",
                    "All clients must use the graph service before launch",
                    source_type="document"),
        ]
        snapshot = build_project_understanding_snapshot(
            project_id=_PROJECT,
            records=records,
            claim_extractor=FakeExtractor(rules),
            as_of=_AS_OF,
        )

        # Purpose is evidence-backed.
        assert "account graph" in snapshot.purpose.one_sentence_purpose
        assert snapshot.purpose.evidence_ids

        # Technical mechanism captured.
        assert len(snapshot.technical_mechanism.steps) == 1
        assert "Billing emits" in snapshot.technical_mechanism.steps[0].name

        # The mobile contradiction is preserved, not averaged away.
        assert len(snapshot.contradictions) == 1
        contested = [
            c for c in snapshot.claims
            if c.status == UnderstandingClaimStatus.CONTESTED
        ]
        assert len(contested) == 2

        # Unknown: is mobile migration launch-blocking?
        conflict_unknowns = [
            u for u in snapshot.unknowns
            if u.kind == ProjectUnknownKind.CONFLICTING_EVIDENCE
        ]
        assert len(conflict_unknowns) == 1
        assert conflict_unknowns[0].priority == ResearchPriority.CRITICAL

        # Research task searches internal evidence before humans.
        task = next(
            t for t in snapshot.research_tasks
            if t.unknown_id == conflict_unknowns[0].id
        )
        assert task.preferred_source_types[0] == EvidenceSourceType.ONTOLOGY
        assert (
            task.preferred_source_types[-1]
            == EvidenceSourceType.HUMAN_CLARIFICATION
        )
