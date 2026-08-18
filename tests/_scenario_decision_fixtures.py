"""Fixtures for Commit 27 scenario-aware decision tests.

Builds two option lattices with deliberately contrasting future
distributions so the Option B canonical case is exercised end to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lltpm.coordination import (
    DecisionOptionAssessment,
    DecisionPosture,
    ScenarioDecisionPolicy,
    ScenarioOutcomeUtility,
)
from lltpm.scenarios import (
    ScenarioEventCandidate,
    ScenarioEventType,
    ScenarioFactorType,
    ScenarioHorizon,
    build_factor,
    generate_scenario_lattice,
)
from lltpm.temporal import ProjectTemporalState

PROJECT = "atlas"
T0 = datetime(2026, 11, 2, 9, 0, 0)
E = ScenarioEventType


def horizon(**overrides) -> ScenarioHorizon:
    defaults = dict(
        start_at=T0, end_at=T0 + timedelta(days=10), step_days=2,
        maximum_depth=2, maximum_active_states=60,
        minimum_branch_probability=0.02,
    )
    defaults.update(overrides)
    return ScenarioHorizon(**defaults)


def base_state(**overrides) -> ProjectTemporalState:
    defaults = dict(
        project_id=PROJECT, as_of=T0,
        active_claim_ids=["claim-prepaid-validated"],
        active_decision_ids=["dec-v3-conditional"],
        open_action_request_ids=["req-approval"],
        active_blocker_ids=["blk-contract"],
        unresolved_unknown_ids=["unk-coexistence"],
        evidence_cutoff_at=T0, state_fingerprint="fp-decision-test",
    )
    defaults.update(overrides)
    return ProjectTemporalState(**defaults)


def factors():
    return [
        build_factor(
            factor_id="fac-prepaid-done",
            factor_type=ScenarioFactorType.CURRENT_STRUCTURAL_SIGNAL,
            description="Prepaid validation completed.",
            direction="decreases", magnitude=0.5, confidence=0.8,
            source_ids=["claim-prepaid-validated"],
        ),
        build_factor(
            factor_id="fac-coexistence-unknown",
            factor_type=ScenarioFactorType.TECHNICAL_UNCERTAINTY,
            description="v2/v3 coexistence is unvalidated.",
            direction="increases", magnitude=0.45, confidence=0.6,
            source_ids=["unk-coexistence"],
        ),
    ]


class _OptionGenerator:
    """Two-stage generator so recovery genuinely *follows* failure.

    Step 1: integration passes (terminal success) or fails (not terminal).
    Step 2: after a failure, a workaround may still reach the date, or
    the milestone slips. Options without a recovery route always slip.
    """

    def __init__(
        self,
        *,
        success_weight: float,
        failure_weight: float,
        recovery_weight: float = 0.0,
        irreversible: bool = False,
    ):
        self.success_weight = success_weight
        self.failure_weight = failure_weight
        self.recovery_weight = recovery_weight
        self.irreversible = irreversible

    def generate_events(self, *, temporal_state, horizon, context):
        events = [
            ScenarioEventCandidate(
                id="cand-success",
                event_type=E.INTEGRATION_PASSES,
                description="Integration validates cleanly.",
                prerequisite_state_conditions=["blocker_open=blk-contract"],
                resulting_state_changes={
                    "dependency_states": {"integration": "passed"},
                },
                factor_ids=["fac-prepaid-done"],
                base_probability=self.success_weight,
                impact=0.1,
                outcome_labels=["launch_on_time"],
            ),
            ScenarioEventCandidate(
                id="cand-failure",
                event_type=E.INTEGRATION_FAILS,
                description="Compatibility issue surfaces.",
                prerequisite_state_conditions=["blocker_open=blk-contract"],
                resulting_state_changes={
                    "dependency_states": {"integration": "failed"},
                },
                factor_ids=["fac-coexistence-unknown"],
                base_probability=self.failure_weight,
                impact=0.8,
            ),
            # Step 2, only reachable after a failure.
            ScenarioEventCandidate(
                id="cand-slip",
                event_type=E.MILESTONE_SLIPS,
                description="No route remains; the milestone slips.",
                prerequisite_state_conditions=["integration=failed"],
                resulting_state_changes={
                    "dependency_states": {"milestone": "slipped"},
                },
                factor_ids=["fac-coexistence-unknown"],
                base_probability=max(0.05, 1.0 - self.recovery_weight),
                impact=0.85,
                outcome_labels=["launch_slips"],
            ),
        ]
        if self.recovery_weight > 0:
            events.append(
                ScenarioEventCandidate(
                    id="cand-workaround",
                    event_type=E.WORKAROUND_ADOPTED,
                    description="Compatibility layer preserves the date.",
                    prerequisite_state_conditions=["integration=failed"],
                    resulting_state_changes={
                        "dependency_states": {"integration": "workaround"},
                    },
                    factor_ids=["fac-prepaid-done"],
                    base_probability=self.recovery_weight,
                    impact=0.3,
                    controllable=True,
                    intervention_ids=["int-compat-layer"],
                    outcome_labels=["launch_on_time"],
                )
            )
        if self.irreversible:
            events.append(
                ScenarioEventCandidate(
                    id="cand-expand",
                    event_type=E.SCOPE_EXPANDS,
                    description="Global rollout expands committed scope.",
                    prerequisite_state_conditions=["blocker_open=blk-contract"],
                    resulting_state_changes={
                        "dependency_states": {"scope": "expanded"},
                    },
                    factor_ids=["fac-coexistence-unknown"],
                    base_probability=self.failure_weight * 0.5,
                    impact=0.7,
                    outcome_labels=["launch_slips"],
                )
            )
        return events


def option_lattice(
    *,
    success_weight: float,
    failure_weight: float,
    recovery_weight: float = 0.0,
    irreversible: bool = False,
    state: ProjectTemporalState | None = None,
):
    return generate_scenario_lattice(
        temporal_state=state or base_state(),
        generator=_OptionGenerator(
            success_weight=success_weight,
            failure_weight=failure_weight,
            recovery_weight=recovery_weight,
            irreversible=irreversible,
        ),
        horizon=horizon(),
        extra_factors=factors(),
    )


def option_state(option_id: str) -> ProjectTemporalState:
    """Each option is analysed from its own counterfactual root, exactly
    as the real pipeline does — so their lattices are distinct."""
    return base_state(state_fingerprint=f"fp-cf-{option_id}")


def risky_lattice(state=None):
    """Option A: global v3 — more failure mass, no recovery route, and an
    irreversible scope expansion."""
    return option_lattice(
        success_weight=0.70, failure_weight=0.30,
        recovery_weight=0.0, irreversible=True,
        state=state or option_state("opt-a"),
    )


def safe_lattice(state=None):
    """Option B: enterprise-only v3 — less failure mass, a strong
    recovery route, fully reversible."""
    return option_lattice(
        success_weight=0.85, failure_weight=0.15,
        recovery_weight=0.75, irreversible=False,
        state=state or option_state("opt-b"),
    )


def comparable_lattice(state=None):
    """A near-identical risk profile, used to prove scenario analysis does
    not mechanically prefer the conservative option."""
    return option_lattice(
        success_weight=0.85, failure_weight=0.15,
        recovery_weight=0.70, irreversible=False,
        state=state or option_state("opt-a"),
    )


def assessment(
    option_id: str,
    *,
    reversible: bool = True,
    violated: list[str] | None = None,
    scores: dict[str, float] | None = None,
) -> DecisionOptionAssessment:
    return DecisionOptionAssessment(
        option_id=option_id,
        criterion_scores=scores or {"crit-0": 0.9},
        supporting_evidence_ids=[f"ev-for-{option_id}"],
        contradicting_evidence_ids=[f"ev-against-{option_id}"],
        violated_hard_constraints=list(violated or []),
        assumptions=[f"assumption for {option_id}"],
        predicted_positive_impacts=[f"{option_id} unblocks work"],
        reversible=reversible,
        evidence_confidence=0.9,
    )


def utilities() -> list[ScenarioOutcomeUtility]:
    return [
        ScenarioOutcomeUtility(
            outcome_id="launch_on_time", utility=1.0,
            rationale="The launch commitment is met.",
        ),
        ScenarioOutcomeUtility(
            outcome_id="launch_slips", utility=-0.6,
            rationale="Downstream integrations and customers are affected.",
        ),
    ]


def policy(posture=DecisionPosture.PRESERVE_OPTIONALITY, **overrides):
    defaults = dict(posture=posture)
    defaults.update(overrides)
    return ScenarioDecisionPolicy(**defaults)
