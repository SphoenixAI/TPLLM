"""Per-lane measurements. Unknown labels and costs remain unknown."""
from collections import Counter, defaultdict
from statistics import median

from tpllm.application.decision_calibration import quality_reports
from tpllm.decisions.models import CalibrationLabel


def _rate(values):
    return sum(values)/len(values) if values else None


def _median(values):
    return median(values) if values else None


def report(store, *, include_fixtures=False, known_at=None):
    records = store.records(known_at=known_at)
    reviews, outcomes = store.reviews(known_at=known_at), store.outcomes(known_at=known_at)
    latest = {r.record_id: r for r in reviews if include_fixtures or r.source != "fixture"}
    eventual = {o.record_id: o for o in outcomes}
    groups = defaultdict(list)
    for record in records:
        key = (record.task.lane, record.provider, record.requested_model, record.resolved_model,
               record.task.definition_id, record.task.definition_hash, record.cohort.cohort_id,
               record.cohort.cohort_version, record.execution.provenance)
        groups[key].append(record)
    segments, valuable = [], []
    for key, all_rows in groups.items():
        # Repeated runs of one packet are not independent quality samples.
        rows = list({r.evidence_packet_id: r for r in all_rows}.values())
        lane, provider, requested, resolved, task, task_hash, cohort, cohort_version, provenance = key
        item = {"lane": lane, "provider": provider, "requested_model": requested, "resolved_model": resolved,
            "task": task, "task_hash": task_hash, "cohort": cohort, "cohort_version": cohort_version,
            "provenance": provenance, "samples": len(rows), "attempted_records": len(all_rows),
            "failure_taxonomy": dict(Counter(r.failure_kind for r in all_rows if r.failure_kind))}
        if lane == "decision":
            labels, traces = [], []
            for r in rows:
                if not r.decision:
                    continue
                traces.append(r.decision)
                event = eventual.get(r.id)
                if event is None or event.decision_value is None:
                    event = latest.get(r.id)
                if event and event.decision_value is not None:
                    labels.append(CalibrationLabel(trace_id=r.decision.id, value=event.decision_value,
                        source="fixture" if getattr(event, "source", None) == "fixture" else "observed_outcome" if hasattr(event, "outcome") else "human_review",
                        reviewer=getattr(event, "reviewer", "structured-outcome"), evidence_refs=(event.id,),
                        observed_at=event.observed_at, recorded_at=event.recorded_at))
                result = r.decision.result
                if result and ((result.probability is not None and .4 <= result.probability <= .6)
                        or (result.question_type == "choice" and result.margin < .1)):
                    valuable.append({"record_id": r.id, "kind": "decision_ambiguity"})
                if event and event.decision_value is not None and result:
                    predicted = result.probability >= .5 if result.question_type == "noul" else result.selected_value
                    strength = max(result.probability, 1-result.probability) if result.probability is not None else max(result.probability_distribution.values())
                    differs = abs(predicted-event.decision_value) >= 1 if result.question_type == "score" else predicted != event.decision_value
                    if differs and strength >= .8:
                        valuable.append({"record_id": r.id, "kind": "confident_decision_error"})
            metrics = quality_reports(traces, labels, include_fixtures=include_fixtures, known_at=known_at)
            count = sum(m["samples"] for m in metrics.values())
            # Measured quality alone never promotes a segment or grants authority.
            degraded = count >= 20 and any(m["brier"] > .35 for m in metrics.values())
            item.update(metrics=metrics, labelled_samples=count,
                status="DEGRADED" if degraded else "UNSEEN" if not count else "EARLY" if count < 20 else "EVALUATING",
                calibration_approval="Not granted by Phase 2")
        else:
            schema = [r.reasoning.schema_valid for r in rows if r.reasoning and r.reasoning.schema_valid is not None]
            refs = [r.reasoning.evidence_refs_valid for r in rows if r.reasoning and r.reasoning.evidence_refs_valid is not None]
            human = [latest[r.id] for r in rows if r.id in latest and latest[r.id].source == "human"]
            total_refs = sum(r.reasoning.reference_count for r in rows if r.reasoning)
            bad_refs = sum(r.reasoning.unsupported_reference_count for r in rows if r.reasoning)
            accept = _rate([r.label == "accept" for r in human])
            item.update(schema_valid_rate=_rate(schema), schema_checked_samples=len(schema),
                evidence_reference_valid_rate=_rate(refs), reference_checked_samples=len(refs),
                unsupported_reference_rate=bad_refs/total_refs if total_refs else None,
                human_acceptance=accept, human_samples=len(human),
                human_correction_rate=_rate([r.label in {"partially_correct", "corrected_interpretation"} for r in human]),
                missing_evidence_rate=_rate([r.label == "missing_evidence" for r in human]),
                task_completion_rate=_rate([r.status == "available" and r.reasoning.structured_output.get("completion") == "complete" for r in rows]),
                status="UNTESTED" if not schema else "DEGRADED" if len(human) >= 20 and accept < .5 else "EARLY" if len(human) < 20 else "EVALUATING",
                validation_approval="Human validation for review requires a separate governed decision")
            for r in rows:
                if r.status in {"rejected", "unavailable"} or (r.id in latest and latest[r.id].label in {"reject", "missing_evidence", "unsupported_inference"}):
                    valuable.append({"record_id": r.id, "kind": "reasoning_failure", "reason": r.reason or latest.get(r.id).label})
        segments.append(item)
    efficiency = []
    by_provider = defaultdict(list)
    for r in records:
        by_provider[(r.task.lane, r.provider, r.requested_model, r.resolved_model, r.execution.provenance)].append(r)
    for (lane, provider, requested, resolved, provenance), rows in by_provider.items():
        batches = defaultdict(list)
        for r in rows:
            if r.execution.attempts:
                batches[r.execution.request_id].append(r)
        executed = [v[0] for v in batches.values()]
        usage = [r.execution.usage for r in executed if r.execution.usage is not None]
        efficiency.append({"lane": lane, "provider": provider, "requested_model": requested, "resolved_model": resolved,
            "provenance": provenance, "requests": len(executed), "attempts": sum(r.execution.attempts for r in executed),
            "retries": sum(max(0, r.execution.attempts-1) for r in executed),
            "median_latency_ms": _median([r.execution.latency_ms for r in executed]) if provenance == "live" else None,
            "median_total_tokens": _median([u.input_count+u.output_count for u in usage]) if provenance == "live" else None,
            "input_tokens": sum(u.input_count for u in usage) if usage and provenance == "live" else None,
            "output_tokens": sum(u.output_count for u in usage) if usage and provenance == "live" else None,
            "usage_reporting_requests": len(usage) if provenance == "live" else 0,
            "input_bytes": sum(r.execution.input_bytes for r in executed),
            "questions_per_request": _median([len(v) for v in batches.values()]) if lane == "decision" else None,
            "failure_rate": _rate([any(r.failure_kind == "provider_failure" for r in group) for group in batches.values()]),
            "schema_valid_rate": _rate([r.reasoning.schema_valid for r in rows if r.reasoning and r.reasoning.schema_valid is not None]) if lane == "reasoning" else None,
            "cost_usd": sum(r.execution.cost_usd for r in executed) if executed and all(r.execution.cost_usd is not None for r in executed) else None})
    comparisons = store.comparisons(known_at=known_at)
    valuable.extend({"record_id": c.left_id, "comparison_id": c.id, "kind": "cross_lane_conflict"}
        for c in comparisons if c.kind == "cross_lane" and c.state == "conflict")
    coverage = store.coverage_events()
    return {"segments": segments, "efficiency": efficiency, "valuable_cases": valuable,
        "coverage": {"eligible": len(records)+sum(len(e["task_ids"]) for e in coverage),
            "executed": sum(r.status != "skipped" for r in records), "skipped": sum(r.status == "skipped" for r in records)+sum(len(e["task_ids"]) for e in coverage),
            "failed": sum(r.status in {"rejected", "unavailable"} for r in records),
            "skip_reasons": dict(Counter([r.reason for r in records if r.status == "skipped"]+
                [e["reason"] for e in coverage for _ in e["task_ids"]]))},
        "comparisons": dict(Counter(c.state for c in comparisons)),
        "limitations": ["Fixture quality is not production performance.", "Mock latency, usage and cost are not provider measurements.",
            "Known references do not establish entailment or factual truth.", "No automatic routing or approval."]}
