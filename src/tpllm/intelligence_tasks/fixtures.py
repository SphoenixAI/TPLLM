"""Sanitized synthetic workloads and independent semantic reference checks."""
from datetime import timedelta

from tpllm.decisions.evaluation import FIXTURE_TIME
from tpllm.decisions.providers import MockDecisionProvider, parse_batch
from tpllm.intelligence_tasks.evidence import EvidencePacketBuilder
from tpllm.intelligence_tasks.models import InsightContext


class FixtureDecisionProvider(MockDecisionProvider):
    """Replay only explicitly requested questions; retain Phase 1 strictness."""
    def evaluate(self, *, state, questions, model):
        batch = super().evaluate(state=state, questions=questions, model=model)
        ids = {q.definition_id for q in questions}
        return batch.model_copy(update={"results": {k:v for k,v in batch.results.items() if k in ids},
            "errors": {k:v for k,v in batch.errors.items() if k in ids}})


def packet_for(case):
    records = [{"project_id": "fixture-project", "key": "source:"+s["id"], "provider": "fixture",
        "source_id": s["id"], "version": "synthetic.v1", "evidence_id": s["id"], "kind": s["kind"],
        "observed_at": (FIXTURE_TIME-timedelta(minutes=30-i)).isoformat(), "collected_at": FIXTURE_TIME.isoformat(),
        "text": s["text"], "data": s["data"]} for i, s in enumerate(case["sources"])]
    insight = InsightContext(id="insight:"+case["id"], summary="Review the fixture's documented operational state.",
        evidence_refs=tuple(s["id"] for s in case["sources"]), valid_at=FIXTURE_TIME, known_at=FIXTURE_TIME,
        validation_outcome="synthetic_existing_insight")
    return EvidencePacketBuilder().build(project_id="fixture-project", entity_id=case["id"], records=records,
        valid_at=FIXTURE_TIME, known_at=FIXTURE_TIME, include_excerpts=True, manual_cohort=case["cohort"], insight=insight)


def decision_batch(case, registry):
    mock, answers = case["mock_decision"], {}
    for q in registry.all():
        if q.decision_id == "risk.present":
            answers[q.definition_id] = {"type": "noul", "noul": mock["risk_probability"]}
        elif q.decision_id == "risk.family":
            specified = mock["family_probabilities"]
            remaining = (1-sum(specified.values()))/(len(q.criteria)-len(specified))
            dist = {k: specified.get(k, remaining) for k in q.criteria}
            answers[q.definition_id] = {"type": "choice", "choice": max(dist, key=dist.get), "confidence": .8, "probabilities": dist}
        elif q.decision_id == "risk.severity":
            level = mock["severity_level"]
            dist = {str(i): .84 if i == level else .04 for i in range(5)}
            answers[q.definition_id] = {"type": "score", "score": sum(int(k)*p for k,p in dist.items()),
                "confidence": .8, "probabilities": dist, "legend": {str(i):v for i,v in enumerate(q.criteria)}}
    return parse_batch({"model": "jev-fixture-v1", "answers": answers,
        "usage": {"input_tokens": 0, "output_tokens": 0}}, tuple(q for q in registry.all() if q.definition_id in answers))


def reasoning_outputs(case, packet):
    mock, reference = case["mock_reasoning"], case["reference"]
    refs = list(packet.state.evidence_refs)
    common = {"completion": reference["expected_completion"], "supporting_evidence_refs": refs,
        "contradictory_evidence_refs": refs[-1:] if case["category"] == "contradictory" else [],
        "entity_refs": [packet.state.entity_id], "ontology_refs": list(packet.state.ontology_refs), "task_refs": [],
        "unknowns": ["Synthetic scenario; operational truth and provider performance are not established."]}
    outputs = {
        "reasoning.root_cause_analysis.v1": {**common, "primary_hypothesis": mock["primary_hypothesis"],
            "alternative_hypotheses": ["Additional causes require independent evidence."], "cause_tags": mock["cause_tags"],
            "operational_interpretation": mock["operational_interpretation"], "recommended_next_evidence": ["Verify against source telemetry."]},
        "reasoning.operational_summary.v1": {**common, "situation": mock["primary_hypothesis"],
            "material_changes": [s["text"] for s in case["sources"]], "dependencies": [], "risks_to_review": []},
        "reasoning.dependency_analysis.v1": {**common, "affected_entities": [packet.state.entity_id],
            "dependency_paths": [], "likely_bottlenecks": [], "downstream_impacts": []},
        "reasoning.recovery_options.v1": {**common, "options": [{"description": "Review the evidence before choosing an intervention.",
            "expected_benefit": "Resolve uncertainty", "tradeoffs": ["Reviewer time"], "dependencies": [], "risks": [],
            "required_authority": "human_review", "evidence_refs": refs}]},
        "reasoning.insight_explanation.v1": {**common, "insight_ref": packet.insight.id, "summary": mock["primary_hypothesis"],
            "why_it_matters": "This evidence informs operational review.", "uncertainties": common["unknowns"],
            "next_questions": ["What independent observation would resolve this?"]},
    }
    root = outputs["reasoning.root_cause_analysis.v1"]
    if mock["fault"] == "unknown_reference":
        root["supporting_evidence_refs"] = ["invented-evidence"]
    elif mock["fault"] == "missing_field":
        del root["primary_hypothesis"]
    # Timeout injected at the provider boundary by the runner, not a schema.
    return outputs


def evaluate_reference(record, reference):
    """Semantic tags + source IDs, never exact prose or a universal score."""
    if record.task.task_id != "reasoning.root_cause_analysis":
        return None
    output = record.reasoning.structured_output if record.reasoning else None
    if record.status != "available" or output is None:
        return {"evaluated": False, "reason": record.reason}
    actual = set(output["cause_tags"])
    required = set(reference["required_cause_tags"])
    return {"evaluated": True, "required_cause_coverage": len(required & actual)/len(required) if required else None,
        "missing_causes": sorted(required-actual), "acceptable_alternates_present": sorted(actual & set(reference["acceptable_alternate_tags"])),
        "missing_required_evidence": sorted(set(reference["required_evidence_refs"])-set(output["supporting_evidence_refs"])),
        "forbidden_cause_claims": sorted(actual & set(reference["forbidden_cause_tags"])),
        "completion_matches_reference": output["completion"] == reference["expected_completion"],
        "evaluation_basis": "Authored semantic rubric; entailment and prose quality still need human review."}
