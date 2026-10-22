"""Deterministic baseline providers.

Two of the engines take a Protocol with no shipping implementation:
claim extraction and scenario candidate generation (Commit
24). In production those are LLM-backed. Without something in their
place the system cannot run at all, which would make the open-source
quickstart "configure an API key, then hope".

These are deliberate baselines, and they are labelled as baselines
everywhere it matters. ``BASELINE_SCENARIO_METHOD`` and
``BASELINE_RECOMMENDATION_METHOD`` go into the reproducibility manifest,
so an artifact derived from a baseline is distinguishable from one
derived by a real model — and the persistence layer's compatibility check will
refuse to call them equivalent. That distinction is the point: a
keyword match over evidence text is a useful floor and a poor imitation
of reading, and nothing here should let the two be confused.

They are deterministic by construction, which also makes them the right
providers for replay-equivalence tests.
"""

from __future__ import annotations

from tpllm.intelligence.project_understanding import (
    UnderstandingClaim,
    UnderstandingClaimStatus,
    UnderstandingClaimType,
)
from tpllm.scenarios.models import (
    ScenarioEventCandidate,
    ScenarioEventType,
)

# Method identifiers recorded in the manifest. A reconstruction under a
# real model will not compare equal to one under these, which is correct.
BASELINE_SCENARIO_METHOD = "baseline-structural-v1"
BASELINE_RECOMMENDATION_METHOD = "baseline-keyword-v1"


# Keyword to claim type. Crude on purpose: a baseline that pretended to
# nuance would be harder to tell apart from real extraction.
BASELINE_CLAIM_RULES: tuple[tuple[str, UnderstandingClaimType], ...] = (
    ("depends on", UnderstandingClaimType.DEPENDENCY),
    ("blocked", UnderstandingClaimType.DEPENDENCY),
    ("approval", UnderstandingClaimType.DEPENDENCY),
    ("incident", UnderstandingClaimType.FAILURE_MODE),
    ("outage", UnderstandingClaimType.FAILURE_MODE),
    ("failure", UnderstandingClaimType.FAILURE_MODE),
    ("capacity", UnderstandingClaimType.OPERATIONAL_REQUIREMENT),
    ("sla", UnderstandingClaimType.OPERATIONAL_REQUIREMENT),
    ("integration", UnderstandingClaimType.TECHNICAL_MECHANISM),
    ("migration", UnderstandingClaimType.TECHNICAL_MECHANISM),
    ("launch", UnderstandingClaimType.SUCCESS_CRITERION),
    ("target", UnderstandingClaimType.SUCCESS_CRITERION),
    ("goal", UnderstandingClaimType.SUCCESS_CRITERION),
    ("out of scope", UnderstandingClaimType.NON_GOAL),
    ("owns", UnderstandingClaimType.OWNERSHIP),
    ("owner", UnderstandingClaimType.OWNERSHIP),
)

# Confidence deliberately low. A keyword match is weak evidence that a
# claim was made, and downstream readiness and scenario scoring should
# treat it that way rather than as a confident reading.
BASELINE_CLAIM_CONFIDENCE = 0.45


class KeywordClaimExtractor:
    """A keyword floor for claim extraction, not a substitute for reading.

    Deterministic: the same evidence always yields the same claims, in
    the same order, with the same ids. Claims carry low confidence
    because that is what a keyword match actually warrants.
    """

    method_version = BASELINE_RECOMMENDATION_METHOD

    def __init__(
        self,
        rules: tuple[tuple[str, UnderstandingClaimType], ...] | None = None,
        *,
        confidence: float = BASELINE_CLAIM_CONFIDENCE,
    ):
        self.rules = rules or BASELINE_CLAIM_RULES
        self.confidence = confidence

    def extract_claims(self, *, project_id, evidence, as_of):
        claims: list[UnderstandingClaim] = []
        for item in sorted(evidence, key=lambda e: e.id):
            content = item.content or ""
            lowered = content.lower()
            matched: set[UnderstandingClaimType] = set()
            for keyword, claim_type in self.rules:
                if keyword not in lowered or claim_type in matched:
                    continue
                matched.add(claim_type)
                claims.append(
                    UnderstandingClaim(
                        id=f"claim-{claim_type.value}-{item.id}",
                        project_id=project_id,
                        claim_type=claim_type,
                        statement=content[:300],
                        status=UnderstandingClaimStatus.SUPPORTED,
                        evidence_ids=[item.id],
                        confidence=self.confidence,
                        first_observed_at=item.source.observed_at,
                        last_supported_at=item.source.observed_at,
                        inferred=True,
                        inference_explanation=(
                            f"Keyword '{keyword}' matched in evidence "
                            f"{item.id}. This is a baseline extraction, not "
                            f"a reading of the text."
                        ),
                    )
                )
        return claims


class StructuralScenarioGenerator:
    """Scenario candidates derived from the reconstructed present.

    Builds one pair of mutually exclusive futures per open blocker and
    per active decision: it resolves, or it slips. That is genuinely
    structural — it comes from real dependency state rather than
    invention — but it is also the floor. It does not model recovery
    routes, lateral interactions, or compound failure, so a lattice from
    this generator is narrower than the modelled space the lattice can
    represent.

    Probabilities are a flat prior with low confidence, which keeps the
    two quantities honestly separate: the system is saying "roughly even,
    and we barely know", not "even odds, confidently".
    """

    method_version = BASELINE_SCENARIO_METHOD

    def __init__(
        self,
        *,
        resolution_probability: float = 0.55,
        probability_confidence: float = 0.25,
        slip_impact: float = 0.6,
    ):
        self.resolution_probability = resolution_probability
        self.probability_confidence = probability_confidence
        self.slip_impact = slip_impact

    def generate_events(self, *, temporal_state, horizon, context):
        candidates: list[ScenarioEventCandidate] = []

        for blocker_id in sorted(temporal_state.active_blocker_ids):
            group = f"blocker-{blocker_id}"
            candidates.append(
                ScenarioEventCandidate(
                    id=f"cand-{blocker_id}-resolved",
                    event_type=ScenarioEventType.DEPENDENCY_DELIVERED,
                    description=f"Blocker {blocker_id} is resolved.",
                    prerequisite_state_conditions=[
                        f"blocker_open={blocker_id}"
                    ],
                    resulting_state_changes={
                        "blockers_resolved": [blocker_id],
                    },
                    base_probability=self.resolution_probability,
                    impact=0.1,
                    outcome_labels=["blockers_cleared"],
                    mutually_exclusive_group=group,
                )
            )
            candidates.append(
                ScenarioEventCandidate(
                    id=f"cand-{blocker_id}-persists",
                    event_type=ScenarioEventType.DEPENDENCY_DELAYED,
                    description=(
                        f"Blocker {blocker_id} persists past the horizon."
                    ),
                    prerequisite_state_conditions=[
                        f"blocker_open={blocker_id}"
                    ],
                    resulting_state_changes={},
                    base_probability=round(
                        1.0 - self.resolution_probability, 6,
                    ),
                    impact=self.slip_impact,
                    outcome_labels=["blocker_persisted"],
                    mutually_exclusive_group=group,
                )
            )

        for decision_id in sorted(temporal_state.active_decision_ids):
            group = f"decision-{decision_id}"
            candidates.append(
                ScenarioEventCandidate(
                    id=f"cand-{decision_id}-made",
                    event_type=ScenarioEventType.DECISION_MADE,
                    description=f"Decision {decision_id} is concluded.",
                    resulting_state_changes={
                        "decision_states": {decision_id: "concluded"},
                    },
                    base_probability=self.resolution_probability,
                    impact=0.1,
                    outcome_labels=["decisions_settled"],
                    mutually_exclusive_group=group,
                )
            )
            candidates.append(
                ScenarioEventCandidate(
                    id=f"cand-{decision_id}-delayed",
                    event_type=ScenarioEventType.DECISION_DELAYED,
                    description=(
                        f"Decision {decision_id} slips past the horizon."
                    ),
                    resulting_state_changes={
                        "decision_states": {decision_id: "delayed"},
                    },
                    base_probability=round(
                        1.0 - self.resolution_probability, 6,
                    ),
                    impact=self.slip_impact,
                    outcome_labels=["decision_delayed"],
                    mutually_exclusive_group=group,
                )
            )

        return candidates


def baseline_providers() -> dict[str, object]:
    """The offline provider set: deterministic, labelled, no API key."""
    return {
        "claim_extractor": KeywordClaimExtractor(),
        "scenario_generator": StructuralScenarioGenerator(),
    }
