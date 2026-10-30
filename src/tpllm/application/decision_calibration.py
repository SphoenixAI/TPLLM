"""Measured decision quality, separate from governed permission to use it."""
from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

from tpllm.decisions.models import DecisionCalibrationProfile, utc
from tpllm.decisions.policy import calibration_key
from tpllm.evaluation.forecast_metrics import build_calibration_buckets


def quality_reports(traces, labels, *, include_fixtures=False, known_at=None):
    latest = {}
    for label in labels:
        if known_at and utc(label.recorded_at) > utc(known_at):
            continue
        latest[label.trace_id] = label
    unique = {}
    for t in traces:
        label = latest.get(t.id)
        if not t.result or not label or (known_at and utc(t.created_at) > utc(known_at)):
            continue
        if label.source == "alternative_provider" or (label.source == "fixture" and not include_fixtures):
            continue
        key = calibration_key(t.definition, t.provider, t.model_resolved, t.project_id)
        sample = (key, t.state_hash)
        if sample not in unique or utc(label.recorded_at) >= utc(unique[sample][1].recorded_at):
            unique[sample] = (t, label)
    groups = defaultdict(list)
    for (key, _), pair in unique.items():
        groups[key].append(pair)
    reports = {}
    for key, rows in groups.items():
        briers, errors, correctness, top2, margins, pairs, ranked_scores = [], [], [], [], [], [], []
        tp = fp = tn = fn = 0
        categories = defaultdict(list)
        category_pairs = defaultdict(list)
        for trace, label in rows:
            r, y, kind = trace.result, label.value, trace.definition.question_type
            if kind == "noul":
                p = r.probability
                briers.append((p - float(y)) ** 2)
                correct = (p >= .5) == y
                tp += int(p >= .5 and y); fp += int(p >= .5 and not y)
                tn += int(p < .5 and not y); fn += int(p < .5 and y)
                pairs.append(SimpleNamespace(predicted_probability=p, actual_occurred=y))
            else:
                dist = r.probability_distribution
                if kind == "choice":
                    correct = r.selected_value == y
                    briers.append(sum((p - int(k == y)) ** 2 for k, p in dist.items()))
                    ordered = sorted(dist, key=lambda k: (-dist[k], k))
                    # All boundary ties count; do not claim a uniquely ranked alternative.
                    cutoff = sorted(dist.values(), reverse=True)[min(1, len(dist)-1)]
                    top2.append(dist[str(y)] >= cutoff)
                    margins.append(r.margin)
                    categories[str(y)].append(correct)
                    for category, probability in dist.items():
                        category_pairs[category].append(SimpleNamespace(predicted_probability=probability, actual_occurred=category == y))
                    pairs.append(SimpleNamespace(predicted_probability=dist[str(r.selected_value)], actual_occurred=correct))
                else:
                    error = abs(r.selected_value - float(y)); errors.append(error)
                    correct = max(0, 1 - error / (len(dist)-1))
                    # Fractional outcomes distribute target mass between adjacent levels.
                    target = {k: max(0, 1-abs(int(k)-float(y))) for k in dist}
                    briers.append(sum((p - target[k]) ** 2 for k, p in dist.items()))
                    cumulative, target_cumulative, squared = 0, 0, 0
                    for k in sorted(dist, key=int)[:-1]:
                        cumulative += dist[k]; target_cumulative += target[k]
                        squared += (cumulative-target_cumulative)**2
                    ranked_scores.append(squared/(len(dist)-1))
                    pairs.append(SimpleNamespace(predicted_probability=r.confidence, actual_occurred=error <= .5))
            correctness.append(correct)
        buckets = build_calibration_buckets(pairs)
        n = len(rows)
        reports[key] = {"samples": n, "brier": sum(briers)/n,
            "accuracy": sum(correctness)/n if rows[0][0].definition.question_type != "score" else None,
            "mean_absolute_error": sum(errors)/len(errors) if errors else None,
            "ranked_probability_score": sum(ranked_scores)/len(ranked_scores) if ranked_scores else None,
            "top_2_coverage": sum(top2)/len(top2) if top2 else None,
            "mean_margin": sum(margins)/len(margins) if margins else None,
            "false_positive_rate": fp/(fp+tn) if fp+tn else None,
            "false_negative_rate": fn/(fn+tp) if fn+tp else None,
            "calibration_error": sum(b.sample_count*b.gap for b in buckets)/n,
            "buckets": [b.model_dump() for b in buckets],
            "category_accuracy": {k: sum(v)/len(v) for k, v in categories.items()},
            "category_calibration": {k: [b.model_dump() for b in build_calibration_buckets(v)] for k, v in category_pairs.items()},
            "label_refs": [label.id for _, label in rows],
            "domain": rows[0][0].definition.domain, "question_type": rows[0][0].definition.question_type}
    return reports


def measured_profiles(*, registry, traces, labels, at):
    """Refresh metrics, retaining only pre-existing governed approvals."""
    reports = quality_reports(traces, labels, known_at=at)
    profiles = {}
    active = not registry.kill_switch_engaged and registry.status.value == "active"
    for key, report in reports.items():
        approval = registry.decision_profiles.get(key)
        profiles[key] = DecisionCalibrationProfile(key=key, samples=report["samples"], brier=report["brier"],
            calibration_error=report["calibration_error"], mean_absolute_error=report["mean_absolute_error"],
            approved=bool(active and approval and approval.approved),
            expires_at=approval.expires_at if approval else None,
            evidence_refs=approval.evidence_refs if approval else ())
    return profiles


def decision_telemetry(traces, labels):
    batches = {t.batch_id: t for t in traces if t.inference_performed}
    families = defaultdict(lambda: {"count": 0, "uncertain": 0, "confidence": [], "probability": [], "disagreements": 0})
    for t in traces:
        group = families["|".join((t.provider, t.model_resolved or "unresolved", t.definition.definition_id))]
        group["count"] += 1
        group["uncertain"] += int(t.policy_evaluation.status == "uncertain")
        group["disagreements"] += int(t.disagreement is True)
        if t.result and t.result.confidence is not None:
            group["confidence"].append(t.result.confidence)
        if t.result and t.result.probability is not None:
            group["probability"].append(t.result.probability)
    latest_labels = {l.trace_id: l for l in labels if l.source != "alternative_provider"}
    overrides = 0
    for t in traces:
        l = latest_labels.get(t.id)
        if l and l.source in {"human_review", "corrected_classification"} and t.result:
            v = t.result.probability >= .5 if t.definition.question_type == "noul" else t.result.selected_value
            overrides += int(v != l.value)
    return {"calls": len(batches), "errors": sum(t.error is not None for t in traces),
        "timeouts": sum(t.error == "timeout" for t in batches.values()),
        "latency_ms": [t.latency_ms for t in batches.values()],
        "input_tokens": sum(t.usage.input_count for t in batches.values()),
        "output_tokens": sum(t.usage.output_count for t in batches.values()),
        "questions_per_request": [t.question_count for t in batches.values()],
        "human_overrides": overrides, "calibration_outcomes": len(latest_labels), "families": dict(families)}
