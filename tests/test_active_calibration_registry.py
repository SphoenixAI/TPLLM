"""Active Calibration Registry & Ranker Consult Point.

The ranker remains pure: it consults governed active calibration records
through a bounded, scope-checked, reversible registry. Governance remains
the only source of authority. No registry → behavior identical to prior
ranking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lltpm.application import (
    ActiveCalibrationRecord,
    ActiveCalibrationRegistry,
    ActiveRegistryStatus,
    ApplicationStatus,
    CalibrationApplicationDecision,
    CalibrationConsultContext,
    build_ranker_calibration_trace,
    consult_active_calibrations,
    deactivate_active_calibration,
    rank_options_with_active_calibrations,
    record_calibration_trace,
    register_active_calibration,
    score_option_with_active_calibrations,
)
from lltpm.application.registry import RankerCalibrationTrace
from lltpm.structural.intervention import InterventionOption, InterventionType


# ── Fixtures ─────────────────────────────────────────────────────────

def _active_record(
    record_id: str = "active-42",
    proposal_id: str = "proposal-bridge-penalty-42",
    status: str = "active",
    target_intervention_type: str | None = "bridge_injection",
    proposed_delta: float = -0.10,
    **extra,
) -> SimpleNamespace:
    """Duck-typed registered record: identity + proposal metadata."""
    return SimpleNamespace(
        id=record_id,
        calibration_proposal_id=proposal_id,
        status=status,
        target_intervention_type=target_intervention_type,
        proposed_delta=proposed_delta,
        **extra,
    )


def _bridge_option(base_risk: float = 0.80) -> InterventionOption:
    # disruption low (weight 1.0), confidence 1.0 → tradeoff_score == base_risk
    return InterventionOption(
        id="bridge_injection_option",
        intervention_type=InterventionType.BRIDGE_INJECTION,
        predicted_structural_risk_reduction=base_risk,
        confidence=1.0,
        disruption_cost="low",
    )


def _reviewer_option(base_risk: float = 0.75) -> InterventionOption:
    return InterventionOption(
        id="reviewer_redistribution_option",
        intervention_type=InterventionType.REVIEWER_REDISTRIBUTION,
        predicted_structural_risk_reduction=base_risk,
        confidence=1.0,
        disruption_cost="low",
    )


def _context(**kwargs) -> CalibrationConsultContext:
    defaults = dict(
        decision_id="decision-123",
        team_id="backend",
        domain_id="payments",
        intervention_type="bridge_injection",
    )
    defaults.update(kwargs)
    return CalibrationConsultContext(**defaults)


def _registry_with(*records, **kwargs) -> ActiveCalibrationRegistry:
    registry = ActiveCalibrationRegistry(**kwargs)
    for record in records:
        registry = register_active_calibration(registry=registry, record=record)
    return registry


# ── Test 1: No registry preserves base score ─────────────────────────

class TestNoRegistry:
    def test_none_registry_preserves_base(self):
        result = consult_active_calibrations(
            registry=None,
            option=_bridge_option(),
            base_score=0.7,
            context=_context(),
        )
        assert result.adjusted_score == 0.7
        assert result.base_score == 0.7
        assert result.decision == CalibrationApplicationDecision.SKIPPED_NO_REGISTRY


# ── Test 2: Empty registry preserves base score ──────────────────────

class TestEmptyRegistry:
    def test_empty_registry_preserves_base(self):
        result = consult_active_calibrations(
            registry=ActiveCalibrationRegistry(),
            option=_bridge_option(),
            base_score=0.8,
            context=_context(),
        )
        assert result.adjusted_score == 0.8
        assert result.decision == CalibrationApplicationDecision.SKIPPED_NO_ACTIVE_RECORDS


# ── Test 3: Kill switch blocks adjustment ────────────────────────────

class TestKillSwitch:
    def test_kill_switch_blocks(self):
        registry = _registry_with(_active_record(), kill_switch_engaged=True)
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.8,
            context=_context(),
        )
        assert result.adjusted_score == 0.8
        assert result.decision == CalibrationApplicationDecision.SKIPPED_KILL_SWITCH
        assert result.applied_adjustments == []

    def test_kill_switched_status_blocks(self):
        registry = _registry_with(
            _active_record(), status=ActiveRegistryStatus.KILL_SWITCHED,
        )
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.8,
            context=_context(),
        )
        assert result.decision == CalibrationApplicationDecision.SKIPPED_KILL_SWITCH


# ── Test 4: Register active calibration ──────────────────────────────

class TestRegister:
    def test_register_adds_record(self):
        registry = ActiveCalibrationRegistry()
        record = _active_record(record_id="active-1")
        updated = register_active_calibration(registry=registry, record=record)
        assert "active-1" in updated.active_records
        assert updated.active_records["active-1"] is record
        assert registry.active_records == {}  # original untouched

    def test_register_commit9_record(self):
        # The real control-plane model registers by its id.
        record = ActiveCalibrationRecord(
            id="active-c9", proposal_id="p-app", status=ApplicationStatus.ACTIVE,
        )
        registry = register_active_calibration(
            registry=ActiveCalibrationRegistry(), record=record,
        )
        assert "active-c9" in registry.active_records

    def test_register_without_id_raises(self):
        with pytest.raises(ValueError, match="record id"):
            register_active_calibration(
                registry=ActiveCalibrationRegistry(),
                record=SimpleNamespace(),
            )


# ── Test 5: Deactivate active calibration ────────────────────────────

class TestDeactivate:
    def test_deactivate_removes_record(self):
        registry = _registry_with(_active_record(record_id="active-A"))
        updated = deactivate_active_calibration(
            registry=registry, active_calibration_id="active-A",
        )
        assert "active-A" not in updated.active_records
        assert "active-A" in registry.active_records  # original untouched

    def test_deactivate_unknown_is_noop(self):
        registry = _registry_with(_active_record(record_id="active-A"))
        updated = deactivate_active_calibration(
            registry=registry, active_calibration_id="does-not-exist",
        )
        assert "active-A" in updated.active_records


# ── Test 6: Inactive record skipped ──────────────────────────────────

class TestInactiveRecord:
    @pytest.mark.parametrize(
        "status", ["reverted", "inactive", "expired", "quarantined", "superseded"],
    )
    def test_inactive_statuses_skipped(self, status):
        registry = _registry_with(_active_record(status=status))
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.8,
            context=_context(),
        )
        assert result.adjusted_score == 0.8
        assert result.decision == CalibrationApplicationDecision.SKIPPED_INACTIVE_RECORD

    def test_commit9_reverted_record_skipped(self):
        record = ActiveCalibrationRecord(
            id="active-c9", proposal_id="p-app", status=ApplicationStatus.REVERTED,
        )
        registry = register_active_calibration(
            registry=ActiveCalibrationRegistry(), record=record,
        )
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.8,
            context=_context(),
        )
        assert result.decision == CalibrationApplicationDecision.SKIPPED_INACTIVE_RECORD


# ── Test 7: Scope mismatch skipped ───────────────────────────────────

class TestScopeMismatch:
    def test_wrong_intervention_type_skipped(self):
        registry = _registry_with(
            _active_record(target_intervention_type="bridge_injection"),
        )
        result = consult_active_calibrations(
            registry=registry,
            option=_reviewer_option(),
            base_score=0.75,
            context=_context(intervention_type="reviewer_redistribution"),
        )
        assert result.adjusted_score == 0.75
        assert result.decision == CalibrationApplicationDecision.SKIPPED_OUT_OF_SCOPE

    def test_wrong_team_skipped(self):
        registry = _registry_with(
            _active_record(team_id="backend"),
        )
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.80,
            context=_context(team_id="frontend"),
        )
        assert result.decision == CalibrationApplicationDecision.SKIPPED_OUT_OF_SCOPE


# ── Test 8: Matching scope applies bounded delta ─────────────────────

class TestScopeMatchApplies:
    def test_matching_type_applies_delta(self):
        registry = _registry_with(
            _active_record(proposed_delta=-0.10),
        )
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.80,
            context=_context(),
        )
        assert result.decision == CalibrationApplicationDecision.APPLIED
        assert result.total_bounded_delta == pytest.approx(-0.10)
        assert result.adjusted_score == pytest.approx(0.70)
        assert result.base_score == 0.80


# ── Test 9: Single adjustment cap is enforced ────────────────────────

class TestSingleCap:
    def test_single_cap_clamps_delta(self):
        registry = _registry_with(
            _active_record(proposed_delta=-0.50),
            max_single_adjustment=0.15,
        )
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.80,
            context=_context(),
        )
        assert result.applied_adjustments[0].delta == pytest.approx(-0.50)
        assert result.applied_adjustments[0].bounded_delta == pytest.approx(-0.15)
        assert result.adjusted_score == pytest.approx(0.65)


# ── Test 10: Total adjustment cap is enforced ────────────────────────

class TestTotalCap:
    def test_total_cap_clamps_sum(self):
        registry = _registry_with(
            _active_record(record_id="active-1", proposed_delta=-0.15),
            _active_record(record_id="active-2", proposed_delta=-0.15),
            max_single_adjustment=0.15,
            max_total_adjustment=0.20,
        )
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.80,
            context=_context(),
        )
        assert len(result.applied_adjustments) == 2
        assert result.total_bounded_delta == pytest.approx(-0.20)
        assert result.adjusted_score == pytest.approx(0.60)


# ── Test 11: Applied and skipped lists preserved ─────────────────────

class TestAppliedSkippedLists:
    def test_mixed_records_split_correctly(self):
        registry = _registry_with(
            _active_record(record_id="active-match", proposed_delta=-0.10),
            _active_record(
                record_id="active-foreign",
                target_intervention_type="milestone_resequencing",
            ),
        )
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.80,
            context=_context(),
        )
        assert len(result.applied_adjustments) == 1
        assert len(result.skipped_adjustments) == 1
        assert result.applied_adjustments[0].active_calibration_id == "active-match"
        assert result.skipped_adjustments[0].active_calibration_id == "active-foreign"


# ── Test 12: Adapter preserves behavior with no registry ─────────────

class TestAdapterNoRegistry:
    def test_ranking_matches_base_tradeoff(self):
        a = _bridge_option(base_risk=0.80)
        b = _reviewer_option(base_risk=0.75)

        ranked, results = rank_options_with_active_calibrations(
            options=[b, a],
            context=_context(),
            registry=None,
        )
        assert [o.id for o in ranked] == [a.id, b.id]
        for r in results:
            assert r.adjusted_score == r.base_score
            assert r.decision == CalibrationApplicationDecision.SKIPPED_NO_REGISTRY


# ── Test 13: Adapter changes ranking only through registry ───────────

class TestAdapterRegistryRanking:
    def test_registry_boost_flips_ranking(self):
        a = _bridge_option(base_risk=0.80)          # base 0.80
        b = _reviewer_option(base_risk=0.75)        # base 0.75

        registry = _registry_with(
            _active_record(
                record_id="active-boost-b",
                target_intervention_type="reviewer_redistribution",
                proposed_delta=0.10,
            ),
        )
        ranked, results = rank_options_with_active_calibrations(
            options=[a, b],
            context=_context(intervention_type=None),
            registry=registry,
        )
        assert [o.id for o in ranked] == [b.id, a.id]  # B (0.85) above A (0.80)

        result_b = next(r for r in results if r.option_id == b.id)
        assert result_b.base_score == pytest.approx(0.75)
        assert result_b.adjusted_score == pytest.approx(0.85)

        result_a = next(r for r in results if r.option_id == a.id)
        assert result_a.adjusted_score == result_a.base_score


# ── Test 14: Trace captures applied calibration ids ──────────────────

class TestTraceContents:
    def test_trace_records_ids_and_scores(self):
        registry = _registry_with(_active_record(record_id="active-42"))
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.82,
            context=_context(),
        )
        trace = build_ranker_calibration_trace(context=_context(), result=result)

        assert "active-42" in trace.applied_calibration_ids
        assert trace.base_score == pytest.approx(0.82)
        assert trace.adjusted_score == pytest.approx(0.72)
        assert trace.decision_id == "decision-123"
        assert trace.option_id == "bridge_injection_option"


# ── Test 15: Record trace is append-only by copy ─────────────────────

class TestTraceAppendOnly:
    def test_old_registry_unchanged(self):
        old_trace = RankerCalibrationTrace(
            id="trace-old", base_score=0.5, adjusted_score=0.5,
            total_bounded_delta=0.0,
        )
        registry = ActiveCalibrationRegistry(traces=[old_trace])

        new_trace = RankerCalibrationTrace(
            id="trace-new", base_score=0.8, adjusted_score=0.7,
            total_bounded_delta=-0.1,
        )
        updated = record_calibration_trace(registry=registry, trace=new_trace)

        assert [t.id for t in updated.traces] == ["trace-old", "trace-new"]
        assert [t.id for t in registry.traces] == ["trace-old"]
        assert registry.traces[0].base_score == 0.5  # prior entry unmodified


# ── Test 16: Unknown scope does not apply ────────────────────────────

class TestNoScopeNoAuthority:
    def test_record_without_scope_fields_skipped(self):
        record = SimpleNamespace(
            id="active-scopeless",
            calibration_proposal_id="p-x",
            status="active",
            proposed_delta=-0.10,
        )
        registry = _registry_with(record)
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.80,
            context=CalibrationConsultContext(),
        )
        assert result.adjusted_score == 0.80
        assert result.decision == CalibrationApplicationDecision.SKIPPED_OUT_OF_SCOPE


# ── Test 17: Base score always preserved ─────────────────────────────

class TestBaseScorePreserved:
    def test_base_score_on_result_and_adjustments(self):
        registry = _registry_with(_active_record(proposed_delta=-0.10))
        result = consult_active_calibrations(
            registry=registry,
            option=_bridge_option(),
            base_score=0.80,
            context=_context(),
        )
        assert result.base_score == 0.80
        for adjustment in result.applied_adjustments + result.skipped_adjustments:
            assert adjustment.base_score == 0.80


# ── Test 18: Registry does not grant governance authority ────────────

class TestNoGovernanceAuthority:
    def test_registry_source_has_no_authority_literals(self):
        import inspect
        import lltpm.application.registry as reg
        import lltpm.application.ranker_adapter as adapter

        forbidden = [
            "issue_approval(",
            "transition_status(",
            "APPROVED",
            "approve",
            "renew_approval(",
        ]
        for mod in [reg, adapter]:
            source = inspect.getsource(mod)
            for pattern in forbidden:
                assert pattern not in source, (
                    f"{mod.__name__} must not contain '{pattern}' — "
                    f"governance remains the only source of authority."
                )


# ── Test 19: Registry does not edit ranker source ────────────────────

class TestNoRankerSourceEdit:
    def test_adapter_source_has_no_mutation_literals(self):
        import inspect
        import lltpm.application.registry as reg
        import lltpm.application.ranker_adapter as adapter

        forbidden = [
            "write(",
            "open(",
            "apply_to_live_ranker",
            "monkeypatch",
            "setattr(",
        ]
        for mod in [reg, adapter]:
            source = inspect.getsource(mod)
            for pattern in forbidden:
                assert pattern not in source, (
                    f"{mod.__name__} must not contain '{pattern}' — "
                    f"the ranker remains pure; the registry only consults."
                )


# ── Test 20: Full consult is deterministic ───────────────────────────

class TestDeterminism:
    def test_same_inputs_same_results(self):
        registry = _registry_with(
            _active_record(record_id="active-1", proposed_delta=-0.10),
            _active_record(
                record_id="active-2",
                target_intervention_type="milestone_resequencing",
            ),
        )
        option = _bridge_option()
        context = _context()

        r1 = consult_active_calibrations(
            registry=registry, option=option, base_score=0.80, context=context,
        )
        r2 = consult_active_calibrations(
            registry=registry, option=option, base_score=0.80, context=context,
        )

        assert r1.adjusted_score == r2.adjusted_score
        assert r1.decision == r2.decision
        assert (
            [a.active_calibration_id for a in r1.applied_adjustments]
            == [a.active_calibration_id for a in r2.applied_adjustments]
        )
        assert (
            [a.active_calibration_id for a in r1.skipped_adjustments]
            == [a.active_calibration_id for a in r2.skipped_adjustments]
        )


# ── End-to-end scenario from the spec ────────────────────────────────

class TestEndToEndScenario:
    def test_spec_scenario(self):
        record = _active_record(
            record_id="active_42",
            proposal_id="proposal_bridge_penalty_42",
            proposed_delta=-0.12,
        )
        registry = _registry_with(
            record, max_single_adjustment=0.15, max_total_adjustment=0.25,
        )
        option = _bridge_option(base_risk=0.82)  # tradeoff_score 0.82
        context = _context()

        adjusted, result = score_option_with_active_calibrations(
            option=option, context=context, registry=registry,
        )
        assert result.base_score == pytest.approx(0.82)
        assert result.total_bounded_delta == pytest.approx(-0.12)
        assert adjusted == pytest.approx(0.70)
        assert result.decision == CalibrationApplicationDecision.APPLIED
        assert result.applied_adjustments[0].active_calibration_id == "active_42"

        trace = build_ranker_calibration_trace(context=context, result=result)
        assert trace.decision_id == "decision-123"
        assert trace.applied_calibration_ids == ["active_42"]

        # Kill switch: base preserved.
        killed = registry.model_copy(update={"kill_switch_engaged": True})
        adjusted_k, result_k = score_option_with_active_calibrations(
            option=option, context=context, registry=killed,
        )
        assert adjusted_k == pytest.approx(0.82)
        assert result_k.decision == CalibrationApplicationDecision.SKIPPED_KILL_SWITCH
        assert result_k.applied_adjustments == []
