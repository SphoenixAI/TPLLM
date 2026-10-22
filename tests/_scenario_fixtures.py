"""Shared fixtures for scenario tests.

Builds the spec's contract-approval scenario: approval may land, be
delayed, or be rejected; a delay may be worked around, scope-reduced, or
simply slip. Several distinct paths converge on "launch_on_time".
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tpllm.scenarios import (
    ScenarioEventCandidate,
    ScenarioEventType,
    ScenarioFactorType,
    ScenarioHorizon,
    build_factor,
    generate_scenario_lattice,
)
from tpllm.temporal import ProjectTemporalState

PROJECT = "atlas"
T0 = datetime(2026, 9, 10, 9, 0, 0)
E = ScenarioEventType


def horizon(**overrides) -> ScenarioHorizon:
    defaults = dict(
        start_at=T0,
        end_at=T0 + timedelta(days=14),
        step_days=2,
        maximum_depth=3,
        maximum_active_states=60,
        minimum_branch_probability=0.02,
    )
    defaults.update(overrides)
    return ScenarioHorizon(**defaults)


def temporal_state(
    *,
    blockers: list[str] | None = None,
    unknowns: list[str] | None = None,
    contested: list[str] | None = None,
    decisions: list[str] | None = None,
    as_of: datetime | None = None,
) -> ProjectTemporalState:
    return ProjectTemporalState(
        project_id=PROJECT,
        as_of=as_of or T0,
        active_claim_ids=["claim-prepaid-validated"],
        contested_claim_ids=list(contested or []),
        active_decision_ids=list(decisions or ["dec-v3-conditional"]),
        open_action_request_ids=["req-approval"],
        active_blocker_ids=list(blockers or ["blk-contract"]),
        unresolved_unknown_ids=list(unknowns or ["unk-coexistence"]),
        evidence_cutoff_at=as_of or T0,
        state_fingerprint="fp-test",
    )


def factors() -> list:
    """Factors referenced by the candidate events below."""
    return [
        build_factor(
            factor_id="fac-approval-pending",
            factor_type=ScenarioFactorType.DECISION_AGE,
            description="Contract approval has been pending six days.",
            direction="increases", magnitude=0.4, confidence=0.7,
            source_ids=["req-approval"],
        ),
        build_factor(
            factor_id="fac-prepaid-done",
            factor_type=ScenarioFactorType.CURRENT_STRUCTURAL_SIGNAL,
            description="Prepaid validation completed, satisfying the "
                        "prior condition.",
            direction="decreases", magnitude=0.5, confidence=0.8,
            source_ids=["claim-prepaid-validated"],
        ),
        build_factor(
            factor_id="fac-coexistence-unknown",
            factor_type=ScenarioFactorType.TECHNICAL_UNCERTAINTY,
            description="v2/v3 coexistence is unvalidated.",
            direction="increases", magnitude=0.45, confidence=0.4,
            source_ids=["unk-coexistence"],
        ),
    ]


class ContractScenarioGenerator:
    """Deterministic generator for the contract-approval scenario."""

    def generate_events(self, *, temporal_state, horizon, context):
        return [
            # Step 1: three mutually exclusive approval outcomes.
            ScenarioEventCandidate(
                id="cand-approve",
                event_type=E.DECISION_MADE,
                description="Contract v3 approved within two days.",
                prerequisite_state_conditions=["blocker_open=blk-contract"],
                resulting_state_changes={
                    "decision_states": {"dec-v3": "approved"},
                    "blockers_resolved": ["blk-contract"],
                    "dependency_states": {"contract": "approved"},
                },
                factor_ids=["fac-prepaid-done", "fac-approval-pending"],
                base_probability=0.58,
                impact=0.2,
                controllable=True,
                intervention_ids=["int-send-decision-capsule"],
            ),
            ScenarioEventCandidate(
                id="cand-delay",
                event_type=E.DECISION_DELAYED,
                description="Contract approval slips past the window.",
                prerequisite_state_conditions=["blocker_open=blk-contract"],
                resulting_state_changes={
                    "decision_states": {"dec-v3": "delayed"},
                    "dependency_states": {"contract": "delayed"},
                },
                factor_ids=["fac-approval-pending"],
                base_probability=0.27,
                impact=0.5,
                controllable=True,
                intervention_ids=["int-escalate"],
            ),
            ScenarioEventCandidate(
                id="cand-reject",
                event_type=E.DECISION_REOPENED,
                description="Approval rejected; architecture reopened.",
                prerequisite_state_conditions=["blocker_open=blk-contract"],
                resulting_state_changes={
                    "decision_states": {"dec-v3": "rejected"},
                    "dependency_states": {"contract": "rejected"},
                },
                factor_ids=["fac-coexistence-unknown"],
                base_probability=0.15,
                impact=0.9,  # catastrophic but unlikely
            ),
            # Step 2 after approval: integration passes or fails.
            ScenarioEventCandidate(
                id="cand-integration-pass",
                event_type=E.INTEGRATION_PASSES,
                description="Downstream integrations validate cleanly.",
                prerequisite_state_conditions=["contract=approved"],
                resulting_state_changes={
                    "dependency_states": {"integration": "passed"},
                },
                factor_ids=["fac-prepaid-done"],
                base_probability=0.78,
                impact=0.1,
                outcome_labels=["launch_on_time"],
            ),
            ScenarioEventCandidate(
                id="cand-integration-fail",
                event_type=E.INTEGRATION_FAILS,
                description="Coexistence issue surfaces during integration.",
                prerequisite_state_conditions=["contract=approved"],
                resulting_state_changes={
                    "dependency_states": {"integration": "failed"},
                    "blockers_opened": ["blk-coexistence"],
                },
                factor_ids=["fac-coexistence-unknown"],
                base_probability=0.22,
                impact=0.7,
                outcome_labels=["launch_slips"],
            ),
            # Step 2 after delay: three recovery routes.
            ScenarioEventCandidate(
                id="cand-workaround",
                event_type=E.WORKAROUND_ADOPTED,
                description="Temporary compatibility layer adopted.",
                prerequisite_state_conditions=["contract=delayed"],
                resulting_state_changes={
                    "dependency_states": {"integration": "workaround"},
                },
                factor_ids=["fac-prepaid-done"],
                base_probability=0.40,
                impact=0.3,
                controllable=True,
                intervention_ids=["int-compat-layer"],
                outcome_labels=["launch_on_time"],
            ),
            ScenarioEventCandidate(
                id="cand-scope-cut",
                event_type=E.SCOPE_REDUCES,
                description="Enterprise-only scope cut preserves the date.",
                prerequisite_state_conditions=["contract=delayed"],
                resulting_state_changes={
                    "dependency_states": {"integration": "reduced"},
                },
                factor_ids=[],
                base_probability=0.35,
                impact=0.4,
                controllable=True,
                intervention_ids=["int-scope-cut"],
                outcome_labels=["launch_on_time"],
            ),
            ScenarioEventCandidate(
                id="cand-no-action",
                event_type=E.MILESTONE_SLIPS,
                description="No workaround adopted; milestone slips.",
                prerequisite_state_conditions=["contract=delayed"],
                resulting_state_changes={
                    "dependency_states": {"integration": "slipped"},
                },
                factor_ids=["fac-approval-pending"],
                base_probability=0.25,
                impact=0.8,
                outcome_labels=["launch_slips"],
            ),
            # Lateral: a miscommunication branch off the approved state.
            ScenarioEventCandidate(
                id="cand-miscomm",
                event_type=E.MISCOMMUNICATION_OCCURS,
                description=(
                    "Mobile reads the enterprise decision as global v3."
                ),
                prerequisite_state_conditions=["contract=approved"],
                resulting_state_changes={
                    "dependency_states": {"mobile": "mismatched"},
                },
                factor_ids=["fac-coexistence-unknown"],
                base_probability=0.10,
                impact=0.6,
                controllable=True,
                intervention_ids=["int-propagate-decision"],
                outcome_labels=["launch_slips"],
            ),
            # Upstream: an executive priority change.
            ScenarioEventCandidate(
                id="cand-priority",
                event_type=E.PRIORITY_CHANGE,
                description="Executive priority shifts to a partner launch.",
                prerequisite_state_conditions=["contract=approved"],
                resulting_state_changes={
                    "decision_states": {"priority": "changed"},
                },
                factor_ids=[],
                base_probability=0.06,
                impact=0.75,
                outcome_labels=["launch_slips"],
            ),
        ]


def lattice(state=None, hz=None, **overrides):
    return generate_scenario_lattice(
        temporal_state=state or temporal_state(),
        generator=ContractScenarioGenerator(),
        horizon=hz or horizon(),
        extra_factors=factors(),
        **overrides,
    )
