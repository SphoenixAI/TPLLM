"""Probabilistic scenario lattice (Commit 24).

Models multiple plausible futures, multiple causes for the same future,
and multiple ways the organization can recover — reasoning forward,
backward, upstream, downstream, laterally, and about recovery.

Honesty rules enforced throughout: probability is separate from
confidence, probability is separate from impact, pruned mass is always
disclosed, the method is honestly labeled, and the system never claims
to have considered every possible future — only the material modeled
scenario space.
"""

from lltpm.scenarios.factors import (
    build_correlated_evidence_factors,
    build_factor,
    derive_factors_from_state,
)
from lltpm.scenarios.generation import (
    HIGH_IMPACT_PRESERVATION_THRESHOLD,
    ScenarioCandidateGenerator,
    build_root_state,
    generate_scenario_lattice,
)
from lltpm.scenarios.lattice import (
    LatticeIntegrityReport,
    assess_lattice_integrity,
    find_convergent_states,
)
from lltpm.scenarios.models import (
    InformationValueEstimate,
    ScenarioCoverageAssessment,
    ScenarioDirection,
    ScenarioEventCandidate,
    ScenarioEventType,
    ScenarioFactorType,
    ScenarioHorizon,
    ScenarioLattice,
    ScenarioPath,
    ScenarioProbabilityFactor,
    ScenarioProbabilityMethod,
    ScenarioRiskSummary,
    ScenarioSensitivity,
    ScenarioState,
    ScenarioTailMass,
    ScenarioTransition,
    build_state_signature,
)
from lltpm.scenarios.probability import (
    calculate_outcome_probability,
    calculate_path_probability,
    clamp_probability,
    combine_factors,
    deduplicate_correlated_factors,
    enumerate_paths,
    normalize_sibling_transitions,
    omitted_probability_mass,
    probability_of_outcome,
    total_displayed_probability,
)
from lltpm.scenarios.queries import (
    ScenarioAnswer,
    ScenarioQuestion,
    ScenarioQuestionType,
    answer_scenario_question,
    assess_scenario_coverage,
    calculate_scenario_sensitivity,
    estimate_value_of_information,
    find_alternate_success_paths,
    find_downstream_exposure,
    find_lateral_interactions,
    find_low_probability_high_impact_paths,
    find_most_likely_next,
    find_paths_to_outcome,
    find_upstream_precursors,
    rank_paths_by_quadrant,
    summarize_path_risk,
)
from lltpm.scenarios.simulation import (
    ScenarioSimulationReport,
    simulate_scenario_lattice,
)

__all__ = [
    "HIGH_IMPACT_PRESERVATION_THRESHOLD",
    "InformationValueEstimate",
    "LatticeIntegrityReport",
    "ScenarioAnswer",
    "ScenarioCandidateGenerator",
    "ScenarioCoverageAssessment",
    "ScenarioDirection",
    "ScenarioEventCandidate",
    "ScenarioEventType",
    "ScenarioFactorType",
    "ScenarioHorizon",
    "ScenarioLattice",
    "ScenarioPath",
    "ScenarioProbabilityFactor",
    "ScenarioProbabilityMethod",
    "ScenarioQuestion",
    "ScenarioQuestionType",
    "ScenarioRiskSummary",
    "ScenarioSensitivity",
    "ScenarioSimulationReport",
    "ScenarioState",
    "ScenarioTailMass",
    "ScenarioTransition",
    "answer_scenario_question",
    "assess_lattice_integrity",
    "assess_scenario_coverage",
    "build_correlated_evidence_factors",
    "build_factor",
    "build_root_state",
    "build_state_signature",
    "calculate_outcome_probability",
    "calculate_path_probability",
    "calculate_scenario_sensitivity",
    "clamp_probability",
    "combine_factors",
    "deduplicate_correlated_factors",
    "derive_factors_from_state",
    "enumerate_paths",
    "estimate_value_of_information",
    "find_alternate_success_paths",
    "find_convergent_states",
    "find_downstream_exposure",
    "find_lateral_interactions",
    "find_low_probability_high_impact_paths",
    "find_most_likely_next",
    "find_paths_to_outcome",
    "find_upstream_precursors",
    "generate_scenario_lattice",
    "normalize_sibling_transitions",
    "omitted_probability_mass",
    "probability_of_outcome",
    "rank_paths_by_quadrant",
    "simulate_scenario_lattice",
    "summarize_path_risk",
    "total_displayed_probability",
]
