"""Interpretation compatibility, not string equality or universal correctness."""
from tpllm.intelligence_tasks.models import ComparisonRecord


def compare_lanes(decision, reasoning, *, at):
    state, explanation = "unresolved", "One lane is unavailable, rejected, or outside selective overlap."
    d, r = decision.decision, reasoning.reasoning
    if decision.status == reasoning.status == "available" and d and d.result and r:
        families = set((r.structured_output or {}).get("operational_interpretation", []))
        if d.definition.decision_id == "risk.family" and families:
            winner = d.result.selected_value
            dist = d.result.probability_distribution
            near = {k for k, p in dist.items() if p >= max(dist.values())-.1}
            if families == {"unknown"} or winner == "unknown":
                state, explanation = "ambiguous", "At least one interpretation explicitly retains unknown."
            elif winner in families:
                state = "agree" if families == {winner} else "compatible"
                explanation = "The bounded primary family appears in the synthesis; additional causes can coexist."
            elif near & families:
                state, explanation = "partially_compatible", "The synthesis supports a near-tied decision alternative."
            elif d.result.margin is not None and d.result.margin < .1:
                state, explanation = "ambiguous", "A close decision distribution does not establish a categorical conflict."
            else:
                state, explanation = "conflict", "The synthesis supports different families from the clear bounded winner; review needed."
    return ComparisonRecord(left_id=decision.id, right_id=reasoning.id, kind="cross_lane", state=state,
        method="operational-family-overlap.v1", explanation=explanation, created_at=at)


def compare_reference(record, event, *, kind):
    """Only explicit reference labels; never infer a label from outcome prose."""
    state = {"agree":"agree", "accept":"agree", "disagree":"conflict", "reject":"conflict",
        "partially_correct":"partially_compatible", "ambiguous":"ambiguous"}.get(getattr(event,"label",None), "unresolved")
    explanation = "Recorded reference requires interpretation." if state == "unresolved" else "Explicit reviewer assessment."
    if record.decision and record.decision.result and event.decision_value is not None:
        result, value = record.decision.result, event.decision_value
        if result.question_type == "score":
            error = abs(result.selected_value-value)
            state = "agree" if error <= .5 else "conflict" if error >= 1 else "ambiguous"
        else:
            predicted = result.probability >= .5 if result.question_type == "noul" else result.selected_value
            state = "agree" if predicted == value else "conflict"
        explanation = "Compared with an explicitly supplied decision reference label; no label inferred from prose."
    return ComparisonRecord(left_id=record.id, right_id=event.id, kind=kind, state=state,
        method="explicit-reference.v1", explanation=explanation, created_at=event.recorded_at)
