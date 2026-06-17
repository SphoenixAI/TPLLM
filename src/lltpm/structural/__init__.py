"""Structural Intelligence Layer — graph topology as predictive risk modeling.

Treats the program (people, teams, work items, dependencies, milestones,
decisions, API contracts) as a living directed graph projected from the
existing ontology, then:

    1. projects ontology entities → NetworkX graph        (graph.py)
    2. measures topology (5 deterministic metrics)         (metrics.py)
    3. contextualizes ownership to suppress false alarms   (ownership.py)
    4. emits explainable StructuralSignals                 (signals.py)
    5. routes signals through the EXISTING validation gate (signals.signal_to_insight)
    6. simulates interventions on graph clones → matrix    (intervention.py)
    7. orchestrates the whole loop                         (engine.py)

No new infrastructure beyond NetworkX; snapshots persist via ArtifactStore.
Everything is suggest-only — the complete brain, no hands.
"""

from lltpm.structural.graph import (
    EdgeType,
    NodeType,
    OntologyEdge,
    OntologyNode,
    PROPAGATING_EDGE_TYPES,
    add_edge,
    add_node,
    graph_from,
    new_graph,
    node_type_of,
    project_to_graph,
)
from lltpm.structural.ownership import (
    OwnershipContext,
    OwnershipLevel,
)
from lltpm.structural.metrics import (
    UNRELATED_DISTANCE,
    GraphMetricObservation,
    active_context_entropy,
    betweenness_centrality,
    dependency_blast_radius,
    review_concentration,
    scope_variance,
    top_bottleneck,
)
from lltpm.structural.snapshot import (
    GraphSnapshot,
    SnapshotTrigger,
    snapshot_graph,
)
from lltpm.structural.signals import (
    StructuralSignal,
    StructuralSignalType,
    signal_to_insight,
)
from lltpm.structural.intervention import (
    InterventionOption,
    InterventionType,
    Mutator,
    mutate_add_bridge_node,
    mutate_reassign_owner,
    mutate_remove_edge,
    mutate_resequence_milestone,
    mutate_split_node,
    option_to_recommendation,
    rank_options,
    simulate_intervention,
)
from lltpm.structural.feedback import (
    HumanDecisionRecord,
    HumanDecisionType,
    OverrideReason,
    ParsedOverrideReason,
    parse_override_reason,
    post_recommendation_hook,
    record_human_decision,
)
from lltpm.structural.outcomes import (
    DecisionDiscrepancy,
    DecisionOutcomeLog,
    DecisionOutcomeType,
    DiscrepancyType,
    calculate_prediction_error,
    derive_decision_discrepancy,
    record_decision_outcome,
)
from lltpm.structural.calibration import (
    CalibrationHypothesis,
    CalibrationHypothesisStatus,
    CalibrationHypothesisType,
    FeedbackKPISummary,
    generate_calibration_hypothesis,
    summarize_feedback_kpis,
)
from lltpm.structural.engine import StructuralEngine

__all__ = [
    # Graph projection
    "NodeType", "EdgeType", "OntologyNode", "OntologyEdge",
    "PROPAGATING_EDGE_TYPES", "project_to_graph", "graph_from",
    "new_graph", "add_node", "add_edge", "node_type_of",
    # Ownership
    "OwnershipContext", "OwnershipLevel",
    # Metrics
    "GraphMetricObservation", "UNRELATED_DISTANCE",
    "betweenness_centrality", "top_bottleneck", "scope_variance",
    "dependency_blast_radius", "active_context_entropy", "review_concentration",
    # Snapshots
    "GraphSnapshot", "SnapshotTrigger", "snapshot_graph",
    # Signals
    "StructuralSignal", "StructuralSignalType", "signal_to_insight",
    # Intervention
    "InterventionOption", "InterventionType", "Mutator",
    "mutate_remove_edge", "mutate_add_bridge_node", "mutate_reassign_owner",
    "mutate_resequence_milestone", "mutate_split_node",
    "simulate_intervention", "rank_options",
    "option_to_recommendation",
    # Feedback (Flight Data Recorder)
    "HumanDecisionRecord", "HumanDecisionType",
    "OverrideReason", "ParsedOverrideReason",
    "parse_override_reason", "post_recommendation_hook", "record_human_decision",
    # Outcomes
    "DecisionDiscrepancy", "DecisionOutcomeLog",
    "DecisionOutcomeType", "DiscrepancyType",
    "calculate_prediction_error", "derive_decision_discrepancy",
    "record_decision_outcome",
    # Calibration
    "CalibrationHypothesis", "CalibrationHypothesisStatus",
    "CalibrationHypothesisType", "FeedbackKPISummary",
    "generate_calibration_hypothesis", "summarize_feedback_kpis",
    # Engine
    "StructuralEngine",
]
