"""Reusable historical-case harness. No action or promotion path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tpllm.decisions.models import CalibrationLabel
from tpllm.decisions.state import DecisionStateBuilder

FIXTURE_TIME = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def fixture_state(case):
    records = [{"project_id": "fixture-project", "key": "fixture:"+case["id"]+":"+entry["source"],
        "provider": entry["source"], "source_id": case["id"]+":"+entry["source"], "version": "fixture-v1",
        "evidence_id": "evidence:"+case["id"]+":"+entry["source"], "kind": "issue",
        "observed_at": (FIXTURE_TIME-timedelta(hours=1)).isoformat(), "collected_at": FIXTURE_TIME.isoformat(),
        "text": entry["text"], "data": entry["data"]} for entry in case["sources"]]
    return DecisionStateBuilder(include_source_excerpts=True).build(project_id="fixture-project", entity_id=case["id"],
        records=records, valid_at=FIXTURE_TIME, known_at=FIXTURE_TIME)


def fixture_baseline(case):
    """Execute the existing KPI baseline, including its blind spots."""
    from tpllm.intelligence.kpi import KPIDefinition, KPIObservation, KPITarget, assess_kpi_health
    kpi = case["kpi"]
    definition = KPIDefinition(id=case["id"], project_id="fixture-project", canonical_name=kpi["name"],
        description="Fixture operational indicator", why_it_matters="Operational outcome under evaluation",
        kpi_type="operational", indicator_role="unknown", unit="count", confidence=1)
    target = KPITarget(id=case["id"]+":target", kpi_id=case["id"], operator="less_than_or_equal", target_value=kpi["target"],
        effective_from=FIXTURE_TIME-timedelta(days=1), evidence_ids=[], confidence=1)
    observation = KPIObservation(id=case["id"]+":observation", kpi_id=case["id"], value=kpi["value"], unit="count",
        observed_at=FIXTURE_TIME, reported_at=FIXTURE_TIME, source_system="fixture", source_id=case["id"],
        evidence_ids=["fixture:"+case["id"]], confidence=1)
    trend = assess_kpi_health(definition=definition, target=target, observations=[observation], profile=None, as_of=FIXTURE_TIME)
    return {"risk.present": trend.health.value in {"watch", "breached", "critical"}}


def evaluate_case(*, case, engine, provider, model):
    state = fixture_state(case)
    traces = engine.evaluate(provider=provider, model=model, state=state,
        definitions=tuple(case["expected"]), baseline=fixture_baseline(case))
    labels = [CalibrationLabel(trace_id=t.id, value=case["expected"][t.definition.definition_id],
        source="fixture", reviewer="fixture-author", evidence_refs=("fixture:"+case["id"],),
        observed_at=FIXTURE_TIME+timedelta(seconds=1), recorded_at=FIXTURE_TIME+timedelta(seconds=1))
        for t in traces if t.result]
    return traces, labels
