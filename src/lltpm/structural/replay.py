"""Counterfactual replay — reconstruct the recommendation context.

When a human picks an intervention other than the agent's top-ranked one and
that choice turns out well, the episode is a *counterexample* to the ranking
model. Before the inverse solver can ask "what small graph edit would have
made the human's choice rank higher?", it needs a faithful reconstruction of
what the agent saw at recommendation time versus what actually happened.

``ReplayComparison`` is that reconstruction: agent top option, human-selected
option, their original ranks, the agent's predicted risk reduction for each,
and the observed outcome. It is pure observation — nothing here mutates the
graph, the ranking, or any agent state.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from lltpm.structural.feedback import HumanDecisionRecord
from lltpm.structural.outcomes import DecisionOutcomeLog


# ── Option accessors (tolerant of field-name drift) ─────────────────

def get_option_id(option: object) -> str:
    """Best-effort id for an intervention option."""
    return (
        getattr(option, "id", None)
        or getattr(option, "intervention_id", None)
        or ""
    )


def get_predicted_reduction(option: object) -> float | None:
    """Best-effort predicted structural risk reduction for an option.

    In this codebase ``InterventionOption.predicted_structural_risk_reduction``
    is the canonical field; the fallbacks keep replay robust if the option
    shape evolves.
    """
    if option is None:
        return None
    for attr in ("predicted_structural_risk_reduction", "predicted_risk_reduction"):
        if hasattr(option, attr):
            val = getattr(option, attr)
            if val is not None:
                return float(val)
    after = getattr(option, "after_metrics", None)
    if isinstance(after, dict):
        for key in ("milestoneSlipRisk", "risk"):
            if key in after:
                return float(after[key])
    return None


# ── ReplayComparison ────────────────────────────────────────────────

class ReplayComparison(BaseModel):
    """Agent top option vs human-selected option vs observed outcome.

    ``observed_risk_change`` uses the Commit 3 convention: positive means
    structural risk *decreased* (improvement), negative means it increased.
    ``human_option_outperformed_expectation`` is True when the observed
    reduction beat what the agent predicted for the human's chosen option —
    the signal that the agent under-ranked a genuinely good move.
    """
    id: str = ""
    human_decision_record_id: str = ""
    decision_outcome_log_id: str | None = None
    graph_snapshot_id: str = ""

    agent_top_intervention_id: str = ""
    human_selected_intervention_id: str = ""

    original_top_rank: int | None = None
    original_human_option_rank: int | None = None

    agent_top_predicted_reduction: float | None = None
    human_option_predicted_reduction: float | None = None
    observed_risk_change: float | None = None

    human_option_outperformed_expectation: bool = False
    summary: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


def build_replay_comparison(
    *,
    human_decision_record: HumanDecisionRecord,
    decision_outcome_log: DecisionOutcomeLog | None,
    intervention_options: list[object],
    agent_top_intervention_id: str = "",
    selected_intervention_id: str = "",
    graph_snapshot_id: str = "",
    artifact_store: object | None = None,
    program_id: str = "",
) -> ReplayComparison:
    """Reconstruct and compare the original recommendation state.

    ``intervention_options`` is assumed to be the originally-*ranked* list
    (index 0 = rank 1). The agent's top option defaults to the first entry;
    the human-selected option defaults to the decision record's
    ``intervention_option_id``. Both can be overridden explicitly.
    """
    option_ids = [get_option_id(o) for o in intervention_options]
    option_by_id = {oid: o for oid, o in zip(option_ids, intervention_options) if oid}

    top_id = agent_top_intervention_id or (option_ids[0] if option_ids else "")
    selected_id = (
        selected_intervention_id
        or human_decision_record.intervention_option_id
        or human_decision_record.recommendation_id
    )

    def _rank_of(target: str) -> int | None:
        return option_ids.index(target) + 1 if target in option_ids else None

    top_rank = _rank_of(top_id) or (1 if top_id else None)
    selected_rank = _rank_of(selected_id)

    top_reduction = get_predicted_reduction(option_by_id.get(top_id))
    selected_reduction = get_predicted_reduction(option_by_id.get(selected_id))

    observed = decision_outcome_log.actual_risk_change if decision_outcome_log else None

    outperformed = bool(
        selected_id
        and selected_id != top_id
        and observed is not None
        and selected_reduction is not None
        and observed > selected_reduction
    )

    comparison = ReplayComparison(
        id=f"replay-{uuid4().hex[:8]}",
        human_decision_record_id=human_decision_record.id,
        decision_outcome_log_id=decision_outcome_log.id if decision_outcome_log else None,
        graph_snapshot_id=graph_snapshot_id or getattr(human_decision_record, "graph_snapshot_id", "") or "",
        agent_top_intervention_id=top_id,
        human_selected_intervention_id=selected_id,
        original_top_rank=top_rank,
        original_human_option_rank=selected_rank,
        agent_top_predicted_reduction=top_reduction,
        human_option_predicted_reduction=selected_reduction,
        observed_risk_change=observed,
        human_option_outperformed_expectation=outperformed,
        summary=(
            f"Human selected '{selected_id}' (original rank {selected_rank}); "
            f"agent top was '{top_id}' (rank {top_rank}). "
            f"Observed risk change={observed}."
        ),
    )

    if artifact_store is not None:
        artifact_store.save(program_id, f"replay_comparison_{comparison.id}", comparison)

    return comparison
