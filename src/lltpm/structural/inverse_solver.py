"""Local inverse structural solver — counterexamples → ranked hypotheses.

A human override that worked out is a *counterexample* to the agent's ranking
model. This module asks the inverse question:

    "What small, plausible, local graph edit would have made the
     human-selected intervention rank higher at recommendation time?"

It searches a bounded local subgraph around the affected nodes, proposes
candidate edits (missing edges, edge-weight nudges, unmodeled constraints,
mis-estimated costs/weights), contradiction-checks each one, simulates the
rank shift it would produce, and promotes the ones that genuinely lift the
human's choice into ranked ``TopologicalMismatch`` *hypotheses*.

Hard safety boundary — this module is strictly non-mutating. It NEVER:
    - applies edges/weights to the live graph (it edits *copies* only),
    - tunes metric weights, thresholds, ownership context, or bridges,
    - changes the real intervention ranking,
    - touches the validation gate or its insight validator,
    - treats human success as ground truth.

Every artifact it produces defaults to a hypothesis status. The claim is
deliberately humble: not "this is the true missing edge", but "this is the
smallest plausible local edit that would have re-ranked the human's choice
higher, subject to contradiction checks."
"""

from __future__ import annotations

from enum import Enum
from uuid import uuid4

import networkx as nx
from pydantic import BaseModel, Field

from lltpm.structural.calibration import (
    CalibrationHypothesis,
    CalibrationHypothesisStatus,
    CalibrationHypothesisType,
)
from lltpm.structural.feedback import HumanDecisionRecord, OverrideReason
from lltpm.structural.replay import ReplayComparison, get_option_id


# ── Search bounds ───────────────────────────────────────────────────

DEFAULT_LOCAL_SEARCH_RADIUS = 2
DEFAULT_MAX_CANDIDATE_EDITS = 20
DEFAULT_MAX_COMBINED_EDITS = 2

_DISRUPTION_WEIGHT: dict[str, float] = {"low": 1.0, "medium": 2.0, "high": 3.0}


# ── Enums ───────────────────────────────────────────────────────────

class TopologicalMismatchType(str, Enum):
    MISSING_EDGE = "missing_edge"
    STALE_EDGE = "stale_edge"
    EDGE_WEIGHT_TOO_LOW = "edge_weight_too_low"
    EDGE_WEIGHT_TOO_HIGH = "edge_weight_too_high"
    MISSING_CONSTRAINT = "missing_constraint"
    INTERVENTION_COST_UNDERESTIMATED = "intervention_cost_underestimated"
    INTERVENTION_COST_OVERESTIMATED = "intervention_cost_overestimated"
    METRIC_WEIGHT_TOO_LOW = "metric_weight_too_low"
    METRIC_WEIGHT_TOO_HIGH = "metric_weight_too_high"
    OWNERSHIP_CONTEXT_STALE = "ownership_context_stale"
    HUMAN_CONTEXT_UNMODELED = "human_context_unmodeled"
    UNKNOWN = "unknown"


class CandidateEditType(str, Enum):
    ADD_EDGE = "add_edge"
    REMOVE_EDGE = "remove_edge"
    UPDATE_EDGE_WEIGHT = "update_edge_weight"
    ADD_CONSTRAINT = "add_constraint"
    UPDATE_INTERVENTION_COST = "update_intervention_cost"
    UPDATE_METRIC_WEIGHT = "update_metric_weight"


class CandidateEditStatus(str, Enum):
    PROPOSED = "proposed"
    CONTRADICTION_PASSED = "contradiction_passed"
    CONTRADICTION_FAILED = "contradiction_failed"
    PROMOTED_TO_HYPOTHESIS = "promoted_to_hypothesis"
    REJECTED = "rejected"


# ── Models ──────────────────────────────────────────────────────────

class CandidateGraphEdit(BaseModel):
    """One possible local graph/model edit to test in the sandbox."""
    id: str = ""
    edit_type: CandidateEditType = CandidateEditType.ADD_EDGE

    # Edge edits
    from_node_id: str | None = None
    to_node_id: str | None = None
    edge_id: str | None = None
    edge_type: str | None = None
    old_weight: float | None = None
    new_weight: float | None = None

    # Constraint edits
    constraint_type: str | None = None
    constraint_summary: str | None = None

    # Intervention / metric weight edits
    target_intervention_type: str | None = None
    target_metric_name: str | None = None
    old_value: float | None = None
    new_value: float | None = None

    rationale: str = ""
    source: str = "inverse_solver"
    status: CandidateEditStatus = CandidateEditStatus.PROPOSED


class HardConstraint(BaseModel):
    """A known architecture/organizational rule the solver must not violate.

    e.g. "this API may not be mocked in launch-bound code" → forbids a
    ``depends_on`` bridge edge to it.
    """
    id: str = ""
    constraint_type: str = ""
    summary: str = ""
    forbidden_edge_type: str | None = None
    protected_node_ids: list[str] = Field(default_factory=list)


class ContradictionCheckResult(BaseModel):
    """Verdict on whether a candidate edit is plausible enough to promote."""
    candidate_edit_id: str = ""
    passed: bool = True
    summary: str = ""
    contradicted_object_ids: list[str] = Field(default_factory=list)
    contradicted_constraint_ids: list[str] = Field(default_factory=list)
    severity: str = "none"


class TopologicalMismatch(BaseModel):
    """The key artifact: a ranked, contradiction-checked hypothesis
    that a small local edit would have re-ranked the human's choice higher.

    ``status`` always defaults to ``"hypothesis"`` — never auto-applied.
    """
    id: str = ""
    human_decision_record_id: str = ""
    decision_outcome_log_id: str | None = None
    replay_comparison_id: str | None = None
    graph_snapshot_id: str = ""

    agent_top_intervention_id: str = ""
    human_selected_intervention_id: str = ""

    mismatch_type: TopologicalMismatchType = TopologicalMismatchType.UNKNOWN
    required_graph_edit_distance: int = 0
    candidate_edit_ids: list[str] = Field(default_factory=list)

    before_rank: int | None = None
    after_rank: int | None = None
    predicted_rank_shift: float | None = None

    confidence: float = 0.0
    contradiction_check_passed: bool = False
    contradiction_summary: str | None = None

    hypothesis_summary: str = ""
    status: str = "hypothesis"


class InverseSolverResult(BaseModel):
    """Return object — the ranked menu of mismatch hypotheses."""
    id: str = ""
    replay_comparison_id: str = ""
    candidate_edit_ids: list[str] = Field(default_factory=list)
    mismatch_ids: list[str] = Field(default_factory=list)
    selected_mismatch_id: str | None = None
    searched_node_count: int = 0
    searched_edge_count: int = 0
    local_search_radius: int = DEFAULT_LOCAL_SEARCH_RADIUS
    summary: str = ""


# ── Local subgraph extraction (bounded search) ──────────────────────

def extract_local_subgraph(
    *,
    graph: nx.MultiDiGraph,
    seed_node_ids: list[str],
    radius: int = DEFAULT_LOCAL_SEARCH_RADIUS,
) -> nx.MultiDiGraph:
    """Return the subgraph within ``radius`` hops of any seed node.

    Bounded by design — the solver must never search the whole graph by
    default. Returns a *copy*; the original graph is never touched.
    """
    undirected = graph.to_undirected(as_view=True)
    nodes: set[str] = set()
    for seed in seed_node_ids:
        if seed not in graph:
            continue
        lengths = nx.single_source_shortest_path_length(undirected, seed, cutoff=radius)
        nodes.update(lengths.keys())
    return graph.subgraph(nodes).copy()


# ── Candidate edit generation ───────────────────────────────────────

def generate_missing_edge_candidates(
    *,
    local_graph: nx.MultiDiGraph,
    allowed_edge_types: set[str],
    max_candidates: int,
) -> list[CandidateGraphEdit]:
    """Propose plausible missing edges between currently-unconnected nodes."""
    candidates: list[CandidateGraphEdit] = []
    if "depends_on" not in allowed_edge_types:
        return candidates
    nodes = list(local_graph.nodes)
    for i, frm in enumerate(nodes):
        for to in nodes[i + 1:]:
            if len(candidates) >= max_candidates:
                return candidates
            if frm == to or local_graph.has_edge(frm, to):
                continue
            candidates.append(
                CandidateGraphEdit(
                    id=f"cge-{uuid4().hex[:8]}",
                    edit_type=CandidateEditType.ADD_EDGE,
                    from_node_id=frm,
                    to_node_id=to,
                    edge_type="depends_on",
                    new_weight=1.0,
                    rationale=f"Candidate missing depends_on edge between {frm} and {to}.",
                )
            )
    return candidates


def generate_edge_weight_candidates(
    *,
    local_graph: nx.MultiDiGraph,
    max_candidates: int,
) -> list[CandidateGraphEdit]:
    """Propose small weight nudges on existing edges."""
    candidates: list[CandidateGraphEdit] = []
    for frm, to, key, data in local_graph.edges(keys=True, data=True):
        if len(candidates) >= max_candidates:
            break
        current = float(data.get("weight", 1.0))
        for multiplier in (0.5, 1.5):
            candidates.append(
                CandidateGraphEdit(
                    id=f"cge-{uuid4().hex[:8]}",
                    edit_type=CandidateEditType.UPDATE_EDGE_WEIGHT,
                    from_node_id=frm,
                    to_node_id=to,
                    edge_id=str(key) if key is not None else None,
                    old_weight=current,
                    new_weight=current * multiplier,
                    rationale=(
                        f"Candidate edge weight update {current:.2f}→"
                        f"{current * multiplier:.2f} on edge {key}."
                    ),
                )
            )
            if len(candidates) >= max_candidates:
                break
    return candidates


# Map the deterministic OverrideReason enum onto candidate edits.
def generate_override_reason_candidates(
    *,
    human_decision_record: HumanDecisionRecord,
) -> list[CandidateGraphEdit]:
    """Turn a parsed human override reason into candidate edits.

    The override reason is the human's own explanation for diverging — a
    strong prior for what the model was missing.
    """
    parsed = human_decision_record.override_reason
    if parsed is None:
        return []
    reason = parsed.reason

    if reason in (OverrideReason.ORGANIZATIONAL_CONSTRAINT, OverrideReason.INCORRECT_DIAGNOSIS):
        ctype = (
            "organizational_constraint"
            if reason == OverrideReason.ORGANIZATIONAL_CONSTRAINT
            else "architecture_constraint"
        )
        return [
            CandidateGraphEdit(
                id=f"cge-{uuid4().hex[:8]}",
                edit_type=CandidateEditType.ADD_CONSTRAINT,
                constraint_type=ctype,
                constraint_summary=(
                    "Human feedback indicates a constraint not represented in "
                    "the intervention model, which over-ranked the agent's top option."
                ),
                rationale=(
                    f"Override reason '{reason.value}' suggests a missing "
                    f"{ctype} disqualifying the agent's top intervention."
                ),
            )
        ]

    if reason == OverrideReason.MISSING_CONTEXT:
        return [
            CandidateGraphEdit(
                id=f"cge-{uuid4().hex[:8]}",
                edit_type=CandidateEditType.ADD_CONSTRAINT,
                constraint_type="missing_context_constraint",
                constraint_summary="Human indicated missing context around the top option.",
                rationale="Override reason 'missing_context' suggests an unmodeled constraint.",
            )
        ]

    if reason == OverrideReason.TOO_DISRUPTIVE:
        return [
            CandidateGraphEdit(
                id=f"cge-{uuid4().hex[:8]}",
                edit_type=CandidateEditType.UPDATE_INTERVENTION_COST,
                target_intervention_type="agent_top_intervention",
                rationale=(
                    "Human found the top option too disruptive; its disruption "
                    "cost may have been underestimated in ranking."
                ),
            )
        ]

    if reason == OverrideReason.WRONG_PRIORITY:
        return [
            CandidateGraphEdit(
                id=f"cge-{uuid4().hex[:8]}",
                edit_type=CandidateEditType.UPDATE_METRIC_WEIGHT,
                target_metric_name="scope_variance",
                rationale=(
                    "Human signalled wrong prioritisation; a metric weight "
                    "(e.g. scope_variance) may be mis-set."
                ),
            )
        ]

    return []


def generate_candidate_edits(
    *,
    local_graph: nx.MultiDiGraph,
    human_decision_record: HumanDecisionRecord,
    allowed_edge_types: set[str] | None = None,
    max_candidate_edits: int = DEFAULT_MAX_CANDIDATE_EDITS,
) -> list[CandidateGraphEdit]:
    """Aggregate all candidate-edit families into one bounded list."""
    allowed = allowed_edge_types or {"depends_on", "blocks", "reviews", "owns"}
    candidates: list[CandidateGraphEdit] = []
    candidates.extend(generate_override_reason_candidates(human_decision_record=human_decision_record))
    candidates.extend(generate_edge_weight_candidates(local_graph=local_graph, max_candidates=max_candidate_edits))
    candidates.extend(
        generate_missing_edge_candidates(
            local_graph=local_graph, allowed_edge_types=allowed, max_candidates=max_candidate_edits
        )
    )
    return candidates[:max_candidate_edits]


# ── Sandbox application (copies only — never mutates the input) ──────

def apply_candidate_edit_to_copy(
    *,
    graph: nx.MultiDiGraph,
    candidate_edit: CandidateGraphEdit,
) -> nx.MultiDiGraph:
    """Apply a candidate edit to a *copy* of the graph and return it.

    Constraint / cost / metric-weight edits do not alter the graph topology;
    they are carried into rank simulation as metadata, so for those the
    returned copy is structurally identical (still a fresh copy).
    """
    clone = graph.copy()
    if candidate_edit.edit_type == CandidateEditType.ADD_EDGE:
        if candidate_edit.from_node_id and candidate_edit.to_node_id:
            clone.add_edge(
                candidate_edit.from_node_id,
                candidate_edit.to_node_id,
                edge_type=candidate_edit.edge_type or "depends_on",
                weight=candidate_edit.new_weight or 1.0,
                candidate_edit_id=candidate_edit.id,
            )
    elif candidate_edit.edit_type == CandidateEditType.UPDATE_EDGE_WEIGHT:
        frm, to = candidate_edit.from_node_id, candidate_edit.to_node_id
        if frm and to and clone.has_edge(frm, to):
            edge_data = clone.get_edge_data(frm, to)
            for _key, data in edge_data.items():
                data["weight"] = candidate_edit.new_weight or data.get("weight", 1.0)
                break
    return clone


# ── Rank simulation (consistent with the real tradeoff ranking) ─────

def option_base_score(option: object) -> float:
    """Base rank score — reuse the real ``tradeoff_score`` when present."""
    scorer = getattr(option, "tradeoff_score", None)
    if callable(scorer):
        try:
            return float(scorer())
        except Exception:
            pass
    rr = float(
        getattr(option, "predicted_structural_risk_reduction", None)
        or getattr(option, "predicted_risk_reduction", 0.0)
        or 0.0
    )
    conf = float(getattr(option, "confidence", 0.5) or 0.5)
    weight = _DISRUPTION_WEIGHT.get(str(getattr(option, "disruption_cost", "medium")).lower(), 2.0)
    return (rr * conf) / weight if weight else 0.0


def adjusted_option_score(
    *,
    option: object,
    candidate_edit: CandidateGraphEdit,
    agent_top_intervention_id: str,
    human_selected_intervention_id: str,
) -> float:
    """Score an option under the modelled effect of one candidate edit.

    Multiplicative adjustments keep this robust to the absolute scale of
    ``tradeoff_score``. This is a counterfactual *what-if*, not a claim that
    the edit is correct.
    """
    score = option_base_score(option)
    oid = get_option_id(option)
    et = candidate_edit.edit_type

    if et == CandidateEditType.ADD_CONSTRAINT and oid == agent_top_intervention_id:
        # A hard constraint effectively disqualifies the agent's top option.
        score *= 0.1
    elif et == CandidateEditType.UPDATE_INTERVENTION_COST:
        if str(getattr(option, "disruption_cost", "medium")).lower() == "high":
            score *= 0.5
        if oid == agent_top_intervention_id:
            score *= 0.6
    elif (
        et == CandidateEditType.UPDATE_METRIC_WEIGHT
        and candidate_edit.target_metric_name == "scope_variance"
        and oid == human_selected_intervention_id
    ):
        score *= 1.4
    elif (
        et in (CandidateEditType.ADD_EDGE, CandidateEditType.UPDATE_EDGE_WEIGHT)
        and oid == human_selected_intervention_id
    ):
        score *= 1.2

    return score


def simulate_rank_shift(
    *,
    candidate_edit: CandidateGraphEdit,
    replay_comparison: ReplayComparison,
    intervention_options: list[object],
) -> tuple[int | None, float]:
    """Return (human_option_after_rank, rank_shift).

    Positive ``rank_shift`` means the human-selected option moved to a
    better (lower-numbered) rank under the candidate edit.
    """
    scored: list[tuple[str, float]] = []
    for option in intervention_options:
        scored.append(
            (
                get_option_id(option),
                adjusted_option_score(
                    option=option,
                    candidate_edit=candidate_edit,
                    agent_top_intervention_id=replay_comparison.agent_top_intervention_id,
                    human_selected_intervention_id=replay_comparison.human_selected_intervention_id,
                ),
            )
        )
    scored.sort(key=lambda item: item[1], reverse=True)

    after_rank: int | None = None
    for index, (oid, _) in enumerate(scored, start=1):
        if oid == replay_comparison.human_selected_intervention_id:
            after_rank = index
            break

    before_rank = replay_comparison.original_human_option_rank
    if before_rank is None or after_rank is None:
        return after_rank, 0.0
    return after_rank, float(before_rank - after_rank)


# ── Contradiction check ─────────────────────────────────────────────

def contradiction_check(
    *,
    candidate_edit: CandidateGraphEdit,
    graph: nx.MultiDiGraph,
    hard_constraints: list[HardConstraint] | None = None,
    ownership_contexts: list[object] | None = None,
    authorized_bridges: list[object] | None = None,
) -> ContradictionCheckResult:
    """Reject implausible candidate edits before they become hypotheses."""
    hard_constraints = hard_constraints or []

    if candidate_edit.edit_type == CandidateEditType.ADD_EDGE:
        frm, to = candidate_edit.from_node_id, candidate_edit.to_node_id
        if frm not in graph or to not in graph:
            return ContradictionCheckResult(
                candidate_edit_id=candidate_edit.id,
                passed=False,
                summary="Candidate edge references a node absent from the local graph.",
                severity="high",
            )
        if frm == to and candidate_edit.edge_type in {"depends_on", "blocks"}:
            return ContradictionCheckResult(
                candidate_edit_id=candidate_edit.id,
                passed=False,
                summary="Candidate creates an invalid self-dependency.",
                severity="medium",
            )
        # Would the new edge create a dependency cycle?
        if candidate_edit.edge_type in {"depends_on", "blocks"} and to in graph and frm in graph:
            if nx.has_path(graph, to, frm):
                return ContradictionCheckResult(
                    candidate_edit_id=candidate_edit.id,
                    passed=False,
                    summary="Candidate edge would create an impossible dependency cycle.",
                    severity="high",
                )

    for constraint in hard_constraints:
        if (
            candidate_edit.edit_type == CandidateEditType.ADD_EDGE
            and constraint.forbidden_edge_type
            and candidate_edit.edge_type == constraint.forbidden_edge_type
        ):
            protected = constraint.protected_node_ids
            if not protected or candidate_edit.to_node_id in protected or candidate_edit.from_node_id in protected:
                return ContradictionCheckResult(
                    candidate_edit_id=candidate_edit.id,
                    passed=False,
                    summary=(
                        f"Candidate edge type '{candidate_edit.edge_type}' conflicts "
                        f"with hard constraint '{constraint.constraint_type}'."
                    ),
                    contradicted_constraint_ids=[constraint.id],
                    severity="high",
                )

    return ContradictionCheckResult(
        candidate_edit_id=candidate_edit.id,
        passed=True,
        summary="No contradiction detected for candidate edit.",
        severity="none",
    )


# ── Mismatch construction ───────────────────────────────────────────

def mismatch_type_from_candidate(candidate_edit: CandidateGraphEdit) -> TopologicalMismatchType:
    """Map a candidate edit to the structural mismatch it implies."""
    et = candidate_edit.edit_type
    if et == CandidateEditType.ADD_EDGE:
        return TopologicalMismatchType.MISSING_EDGE
    if et == CandidateEditType.UPDATE_EDGE_WEIGHT:
        if (
            candidate_edit.old_weight is not None
            and candidate_edit.new_weight is not None
            and candidate_edit.new_weight > candidate_edit.old_weight
        ):
            return TopologicalMismatchType.EDGE_WEIGHT_TOO_LOW
        return TopologicalMismatchType.EDGE_WEIGHT_TOO_HIGH
    if et == CandidateEditType.ADD_CONSTRAINT:
        return TopologicalMismatchType.MISSING_CONSTRAINT
    if et == CandidateEditType.UPDATE_INTERVENTION_COST:
        return TopologicalMismatchType.INTERVENTION_COST_UNDERESTIMATED
    if et == CandidateEditType.UPDATE_METRIC_WEIGHT:
        if (
            candidate_edit.old_value is not None
            and candidate_edit.new_value is not None
            and candidate_edit.new_value > candidate_edit.old_value
        ):
            return TopologicalMismatchType.METRIC_WEIGHT_TOO_LOW
        return TopologicalMismatchType.METRIC_WEIGHT_TOO_HIGH
    return TopologicalMismatchType.UNKNOWN


def build_topological_mismatch(
    *,
    replay_comparison: ReplayComparison,
    candidate_edit: CandidateGraphEdit,
    contradiction_result: ContradictionCheckResult,
    after_rank: int | None,
    rank_shift: float,
) -> TopologicalMismatch:
    """Assemble a ranked, contradiction-checked mismatch hypothesis."""
    before_rank = replay_comparison.original_human_option_rank

    confidence = 0.50
    if contradiction_result.passed:
        confidence += 0.20
    if rank_shift > 0:
        confidence += min(0.20, rank_shift * 0.10)
    if replay_comparison.human_option_outperformed_expectation:
        confidence += 0.10
    confidence = min(confidence, 0.95)

    return TopologicalMismatch(
        id=f"mismatch-{uuid4().hex[:8]}",
        human_decision_record_id=replay_comparison.human_decision_record_id,
        decision_outcome_log_id=replay_comparison.decision_outcome_log_id,
        replay_comparison_id=replay_comparison.id,
        graph_snapshot_id=replay_comparison.graph_snapshot_id,
        agent_top_intervention_id=replay_comparison.agent_top_intervention_id,
        human_selected_intervention_id=replay_comparison.human_selected_intervention_id,
        mismatch_type=mismatch_type_from_candidate(candidate_edit),
        required_graph_edit_distance=1,
        candidate_edit_ids=[candidate_edit.id],
        before_rank=before_rank,
        after_rank=after_rank,
        predicted_rank_shift=rank_shift,
        confidence=confidence,
        contradiction_check_passed=contradiction_result.passed,
        contradiction_summary=contradiction_result.summary,
        hypothesis_summary=(
            f"Candidate {candidate_edit.edit_type.value} would shift the "
            f"human-selected intervention from rank {before_rank} to rank "
            f"{after_rank}. Rationale: {candidate_edit.rationale}"
        ),
        status="hypothesis",
    )


# ── Persistence helper ──────────────────────────────────────────────

def _save(store: object | None, program_id: str, name: str, model: BaseModel) -> None:
    if store is not None:
        store.save(program_id, name, model)


# ── Main solver ─────────────────────────────────────────────────────

def run_local_inverse_solver(
    *,
    graph: nx.MultiDiGraph,
    replay_comparison: ReplayComparison,
    human_decision_record: HumanDecisionRecord,
    intervention_options: list[object],
    seed_node_ids: list[str],
    artifact_store: object | None = None,
    program_id: str = "",
    hard_constraints: list[HardConstraint] | None = None,
    ownership_contexts: list[object] | None = None,
    authorized_bridges: list[object] | None = None,
    local_search_radius: int = DEFAULT_LOCAL_SEARCH_RADIUS,
    max_candidate_edits: int = DEFAULT_MAX_CANDIDATE_EDITS,
) -> InverseSolverResult:
    """Search a bounded local subgraph for plausible edits that would have
    re-ranked the human's choice higher; return ranked mismatch hypotheses.

    Strictly non-mutating: all edits are applied to copies, and the live
    graph, ranking, weights, thresholds, and ownership context are untouched.
    """
    local_graph = extract_local_subgraph(
        graph=graph, seed_node_ids=seed_node_ids, radius=local_search_radius
    )

    candidates = generate_candidate_edits(
        local_graph=local_graph,
        human_decision_record=human_decision_record,
        max_candidate_edits=max_candidate_edits,
    )

    candidate_ids: list[str] = []
    mismatches: list[TopologicalMismatch] = []

    for candidate in candidates:
        candidate_ids.append(candidate.id)

        contradiction = contradiction_check(
            candidate_edit=candidate,
            graph=local_graph,
            hard_constraints=hard_constraints,
            ownership_contexts=ownership_contexts,
            authorized_bridges=authorized_bridges,
        )
        candidate.status = (
            CandidateEditStatus.CONTRADICTION_PASSED
            if contradiction.passed
            else CandidateEditStatus.CONTRADICTION_FAILED
        )

        # Sandbox the edit on a copy (proves non-mutation; feeds a future
        # full re-projection ranker). Skip for failed candidates.
        if contradiction.passed:
            apply_candidate_edit_to_copy(graph=local_graph, candidate_edit=candidate)

        after_rank, rank_shift = simulate_rank_shift(
            candidate_edit=candidate,
            replay_comparison=replay_comparison,
            intervention_options=intervention_options,
        )

        if contradiction.passed and rank_shift > 0:
            mismatch = build_topological_mismatch(
                replay_comparison=replay_comparison,
                candidate_edit=candidate,
                contradiction_result=contradiction,
                after_rank=after_rank,
                rank_shift=rank_shift,
            )
            mismatches.append(mismatch)
            candidate.status = CandidateEditStatus.PROMOTED_TO_HYPOTHESIS

        _save(artifact_store, program_id, f"candidate_edit_{candidate.id}", candidate)
        _save(artifact_store, program_id, f"contradiction_{candidate.id}", contradiction)

    # Rank hypotheses: confidence, then rank shift, then smaller edit distance.
    mismatches.sort(
        key=lambda m: (
            m.confidence,
            m.predicted_rank_shift or 0.0,
            -m.required_graph_edit_distance,
        ),
        reverse=True,
    )

    for mismatch in mismatches:
        _save(artifact_store, program_id, f"mismatch_{mismatch.id}", mismatch)

    result = InverseSolverResult(
        id=f"inv-{uuid4().hex[:8]}",
        replay_comparison_id=replay_comparison.id,
        candidate_edit_ids=candidate_ids,
        mismatch_ids=[m.id for m in mismatches],
        selected_mismatch_id=mismatches[0].id if mismatches else None,
        searched_node_count=local_graph.number_of_nodes(),
        searched_edge_count=local_graph.number_of_edges(),
        local_search_radius=local_search_radius,
        summary=(
            f"Generated {len(mismatches)} topological mismatch hypotheses "
            f"from {len(candidates)} candidate edits over "
            f"{local_graph.number_of_nodes()} local nodes."
        ),
    )
    _save(artifact_store, program_id, f"inverse_solver_result_{result.id}", result)
    return result


# ── Bridge to the calibration layer ──────────────────────

_MISMATCH_TO_CALIBRATION: dict[TopologicalMismatchType, tuple[CalibrationHypothesisType, str]] = {
    TopologicalMismatchType.MISSING_EDGE: (
        CalibrationHypothesisType.ADD_MISSING_EDGE_TYPE,
        "Inverse solver found a plausible missing edge that would improve ranking alignment.",
    ),
    TopologicalMismatchType.MISSING_CONSTRAINT: (
        CalibrationHypothesisType.ADD_CONSTRAINT_TYPE,
        "Inverse solver found a missing constraint that would explain the human-selected option.",
    ),
    TopologicalMismatchType.INTERVENTION_COST_UNDERESTIMATED: (
        CalibrationHypothesisType.ADJUST_INTERVENTION_COST,
        "Inverse solver suggests intervention disruption cost was underestimated.",
    ),
    TopologicalMismatchType.INTERVENTION_COST_OVERESTIMATED: (
        CalibrationHypothesisType.ADJUST_INTERVENTION_COST,
        "Inverse solver suggests intervention disruption cost was overestimated.",
    ),
    TopologicalMismatchType.METRIC_WEIGHT_TOO_LOW: (
        CalibrationHypothesisType.INCREASE_METRIC_WEIGHT,
        "Inverse solver suggests a metric may be underweighted.",
    ),
    TopologicalMismatchType.EDGE_WEIGHT_TOO_LOW: (
        CalibrationHypothesisType.INCREASE_METRIC_WEIGHT,
        "Inverse solver suggests an edge weight may be too low.",
    ),
    TopologicalMismatchType.METRIC_WEIGHT_TOO_HIGH: (
        CalibrationHypothesisType.DECREASE_METRIC_WEIGHT,
        "Inverse solver suggests a metric may be overweighted.",
    ),
    TopologicalMismatchType.EDGE_WEIGHT_TOO_HIGH: (
        CalibrationHypothesisType.DECREASE_METRIC_WEIGHT,
        "Inverse solver suggests an edge weight may be too high.",
    ),
}


def calibration_hypothesis_from_topological_mismatch(
    *,
    mismatch: TopologicalMismatch,
    artifact_store: object | None = None,
    program_id: str = "",
) -> CalibrationHypothesis:
    """Adapt a TopologicalMismatch into a calibration HYPOTHESIS.

    Observational only — like every calibration hypothesis, it defaults to
    ``HYPOTHESIS`` status and is never auto-applied.
    """
    hyp_type, summary = _MISMATCH_TO_CALIBRATION.get(
        mismatch.mismatch_type,
        (CalibrationHypothesisType.NO_CHANGE, "No safe calibration hypothesis from this mismatch."),
    )
    hypothesis = CalibrationHypothesis(
        id=f"cal-{uuid4().hex[:8]}",
        hypothesis_type=hyp_type,
        status=CalibrationHypothesisStatus.HYPOTHESIS,
        source_mismatch_id=mismatch.id,
        signal_type_affected="",
        summary=summary,
        evidence_count=1,
        confidence=mismatch.confidence,
    )
    _save(artifact_store, program_id, f"calibration_hypothesis_{hypothesis.id}", hypothesis)
    return hypothesis
