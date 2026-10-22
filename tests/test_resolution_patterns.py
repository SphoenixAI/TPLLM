"""Resolution patterns and matching."""

from __future__ import annotations

import pytest

from tpllm.coordination import WorkstreamType, build_workstream_state
from tpllm.memory import (
    CoordinationTrace,
    ResolutionPattern,
    build_playbook_suggestion,
    match_resolution_patterns,
    record_pattern_outcome,
)
from tests._coordination_fixtures import (
    AS_OF,
    PROJECT,
    WS,
    actor,
    objective,
    request,
    understanding,
)

_CONTRACT_SIGNATURE = (
    "cross_team|health:watch|needs_authority:may_approve|"
    "request:approval|unresolved_decisions:1|ws_type:migration"
)


def _pattern(**overrides) -> ResolutionPattern:
    defaults = dict(
        id="pat-contract",
        name="Cross-team API contract ambiguity",
        problem_signature=_CONTRACT_SIGNATURE,
        applicable_conditions=[
            "multiple downstream teams hold conflicting contract assumptions",
        ],
        recommended_sequence=[
            "Identify interface authority.",
            "Gather latest team positions.",
            "Attach compatibility/validation evidence.",
            "Request scoped approval.",
            "Record conclusion as conclusive decision.",
        ],
        evidence_requirements=["compatibility validation results"],
        authority_requirements=["interface owner with approval authority"],
        known_failure_conditions=[
            "Broad alignment meeting scheduled before checking existing "
            "decision history.",
        ],
        source_trace_ids=["trace-1"],
        observed_success_count=3,
        observed_failure_count=1,
        confidence=0.75,
    )
    defaults.update(overrides)
    return ResolutionPattern(**defaults)


class TestPatternRecord:
    def test_success_failure_counts_preserved(self):
        pattern = _pattern()
        updated = record_pattern_outcome(
            pattern=pattern, successful=True, source_trace_id="trace-9",
        )
        assert updated.observed_success_count == 4
        assert updated.observed_failure_count == 1
        assert "trace-9" in updated.source_trace_ids
        # Original untouched.
        assert pattern.observed_success_count == 3

        failed = record_pattern_outcome(
            pattern=updated, successful=False, source_trace_id="trace-10",
        )
        assert failed.observed_failure_count == 2

    def test_confidence_bounded(self):
        pattern = _pattern(observed_success_count=0,
                           observed_failure_count=0, confidence=0.0)
        for i in range(50):
            pattern = record_pattern_outcome(
                pattern=pattern, successful=True,
                source_trace_id=f"trace-{i}",
            )
        assert pattern.confidence <= 0.95
        with pytest.raises(ValueError, match="within"):
            _pattern(confidence=1.4)


class TestPatternMatching:
    def test_similar_problem_retrieves_pattern(self):
        matches = match_resolution_patterns(
            problem_signature=_CONTRACT_SIGNATURE,
            context_conditions=[
                "multiple downstream teams hold conflicting contract "
                "assumptions",
            ],
            patterns=[_pattern()],
        )
        assert matches[0].applicable is True
        assert matches[0].structural_similarity == 1.0

    def test_different_problem_does_not_falsely_match(self):
        unrelated = (
            "health:on_track|kpi:stale|request:data|ws_type:research"
        )
        matches = match_resolution_patterns(
            problem_signature=unrelated,
            context_conditions=[],
            patterns=[_pattern()],
        )
        assert matches[0].applicable is False
        assert matches[0].structural_similarity < 0.6

    def test_unmet_condition_blocks_applicability(self):
        matches = match_resolution_patterns(
            problem_signature=_CONTRACT_SIGNATURE,
            context_conditions=[],  # condition not established
            patterns=[_pattern()],
        )
        assert matches[0].applicable is False
        assert matches[0].mismatched_conditions

    def test_matching_deterministic(self):
        kwargs = dict(
            problem_signature=_CONTRACT_SIGNATURE,
            context_conditions=[
                "multiple downstream teams hold conflicting contract "
                "assumptions",
            ],
            patterns=[_pattern(), _pattern(id="pat-b", confidence=0.5)],
        )
        first = [m.model_dump() for m in match_resolution_patterns(**kwargs)]
        second = [m.model_dump() for m in match_resolution_patterns(**kwargs)]
        assert first == second


class TestPlaybookSuggestion:
    def _workstream(self):
        return build_workstream_state(
            workstream_id=WS, project_id=PROJECT, vertical="Payments",
            feature_or_scope="Account Graph Migration",
            workstream_type=WorkstreamType.MIGRATION,
            objective=objective(), key_dates=[],
            understanding=understanding(), readiness=None,
            decisions=[], action_requests=[request("req-1")],
            commitments=[], structural_signals=[], kpi_ids=[], wins=[],
            primary_contact=actor(), previous_state=None, as_of=AS_OF,
        )

    def test_playbook_is_advisory_with_pattern_content(self):
        pattern = _pattern()
        match = match_resolution_patterns(
            problem_signature=_CONTRACT_SIGNATURE,
            context_conditions=pattern.applicable_conditions,
            patterns=[pattern],
        )[0]
        suggestion = build_playbook_suggestion(
            workstream=self._workstream(),
            pattern=pattern,
            match=match,
            problem_summary="Contract v3 approval unresolved across teams",
        )
        assert suggestion.recommended_steps[0] == (
            "Identify interface authority."
        )
        assert suggestion.warnings  # known failure conditions surfaced
        assert suggestion.confidence <= pattern.confidence
        # Advisory only: the model has no send/approve capability fields.
        field_names = set(type(suggestion).model_fields)
        for forbidden in ("send", "approve", "recipients", "channel"):
            assert not any(forbidden in n for n in field_names)

    def test_inapplicable_match_rejected(self):
        pattern = _pattern()
        bad_match = match_resolution_patterns(
            problem_signature="health:on_track|request:data",
            context_conditions=[],
            patterns=[pattern],
        )[0]
        with pytest.raises(ValueError, match="applicable"):
            build_playbook_suggestion(
                workstream=self._workstream(),
                pattern=pattern,
                match=bad_match,
                problem_summary="unrelated",
            )


class TestExtractorProtocol:
    def test_deterministic_fake_extractor(self):
        class FakeExtractor:
            def extract_patterns(self, *, traces):
                return [
                    _pattern(
                        id=f"pat-{t.id}",
                        problem_signature=t.problem_signature,
                        source_trace_ids=[t.id],
                    )
                    for t in traces
                    if t.successful
                ]

        trace = CoordinationTrace(
            id="trace-ok", project_id=PROJECT,
            problem_signature=_CONTRACT_SIGNATURE,
            initial_problem="contract ambiguity",
            successful=True,
            outcome_evidence_ids=["ev-out"],
            created_at=AS_OF,
        )
        failed = CoordinationTrace(
            id="trace-bad", project_id=PROJECT,
            problem_signature="x", initial_problem="y",
            successful=False, created_at=AS_OF,
        )
        patterns = FakeExtractor().extract_patterns(traces=[trace, failed])
        assert len(patterns) == 1
        assert patterns[0].source_trace_ids == ["trace-ok"]
