"""Repeatable fixture evaluations; live inference sends only sanitized cases."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tpllm.application.intelligence_evaluation import report
from tpllm.config import TPLLMConfig
from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.engine import DecisionEngine
from tpllm.decisions.models import fingerprint
from tpllm.decisions.providers import MockDecisionProvider, TypeSafeJevProvider
from tpllm.decisions.registry import default_registry as decision_registry
from tpllm.intelligence_tasks.config import IntelligenceConfig
from tpllm.intelligence_tasks.fixtures import FixtureDecisionProvider, decision_batch, evaluate_reference, packet_for, reasoning_outputs
from tpllm.intelligence_tasks.models import ReviewEvent
from tpllm.intelligence_tasks.registry import default_registry
from tpllm.persistence.journal import InMemoryEventJournalStore
from tpllm.reasoning.engine import ReasoningEngine
from tpllm.reasoning.providers import MockReasoningProvider, ReasoningProviderError, StructuredLLMProvider
from tpllm.runtime.decision_journal import JournalDecisionStore
from tpllm.runtime.intelligence_journal import JournalIntelligenceStore
from tpllm.runtime.intelligence_service import IntelligenceTaskService

FIXTURES = Path(__file__).resolve().parents[2]/"tests/fixtures/intelligence/scenarios.json"


def run(*, lane="dual", provider="mock", task=None, cohort=None, fixture=None, limit=None):
    cases = json.loads(FIXTURES.read_text())
    cases = [c for c in cases if (not cohort or c["cohort"] == cohort) and (not fixture or c["id"] == fixture)]
    if limit is not None:
        cases = cases[:limit]
    cfg, settings = TPLLMConfig.from_env(), IntelligenceConfig(mode="fixtures", include_excerpts=True)
    registry = default_registry()
    tasks = [t.definition_id for t in registry.all() if (t.lane == "reasoning" or t.task_id in {"risk.present", "risk.family", "risk.severity"})
        and (lane == "dual" or t.lane == lane) and (not task or t.definition_id == task)]
    if not tasks or not cases:
        raise ValueError("No matching tasks or fixtures")
    journal = InMemoryEventJournalStore()
    store = JournalIntelligenceStore(journal, "phase2-evaluation")
    dstore = JournalDecisionStore(journal, "phase2-evaluation")
    dcfg = DecisionConfig.from_env()
    summaries, availability = [], {}
    real_reasoning = None
    if provider != "mock" and lane != "decision":
        if provider in {"anthropic", "openai"}:
            cfg.provider = provider
        try:
            real_reasoning = StructuredLLMProvider.from_config(cfg, settings)
            availability["reasoning"] = "configured; actual results recorded below"
        except ReasoningProviderError:
            availability["reasoning"] = "credentials unavailable"
    for case in cases:
        packet = packet_for(case)
        stamp = datetime.now(timezone.utc)
        clock = lambda: stamp
        if provider == "mock":
            dprovider = FixtureDecisionProvider(decision_batch(case, decision_registry()))
            outputs = reasoning_outputs(case, packet)
            if case["mock_reasoning"]["fault"] == "timeout":
                outputs["reasoning.root_cause_analysis.v1"] = ReasoningProviderError("timeout", 2)
            rprovider = MockReasoningProvider(outputs)
        else:
            dprovider = TypeSafeJevProvider(dcfg) if dcfg.active and dcfg.api_key.get_secret_value() else None
            rprovider = real_reasoning
            availability["decision"] = "configured; actual results recorded below" if dprovider else "credentials or feature flag unavailable"
        dname, rname = ("mock", "mock") if provider == "mock" else ("typesafe", cfg.provider)
        engine = DecisionEngine(providers={dname:dprovider}, registry=decision_registry(), store=dstore, mode="shadow", clock=clock) if dprovider else None
        reasoning = ReasoningEngine(providers={rname:rprovider}, registry=registry, store=store, clock=clock) if rprovider else None
        rows = IntelligenceTaskService(store=store, decision_engine=engine, reasoning_engine=reasoning, clock=clock).run(
            packet=packet, task_ids=tasks, decision_provider=dname, decision_model=dcfg.model if provider != "mock" else "fixture-jev",
            reasoning_provider=rname, reasoning_model=cfg.model if provider != "mock" else "fixture-reasoning",
            provenance="mock" if provider == "mock" else "live")
        references = {}
        for row in rows:
            if row.decision and row.decision.result:
                expected = case["reference"][{"risk.present":"risk_present", "risk.family":"risk_family", "risk.severity":"severity"}[row.task.task_id]]
                store.review(ReviewEvent(record_id=row.id, label="correct_label", decision_value=expected,
                    reviewer="fixture-author", source="fixture", evidence_refs=("fixture:"+case["id"],),
                    observed_at=stamp+timedelta(microseconds=1), recorded_at=stamp+timedelta(microseconds=1)))
            check = evaluate_reference(row, case["reference"])
            if check:
                references[row.id] = check
        summaries.append({"fixture": case["id"], "cohort": case["cohort"], "category": case["category"],
            "packet_id": packet.id, "record_ids": [r.id for r in rows], "reference_results": references})
    evaluation = report(store, include_fixtures=True)
    for summary in summaries:
        for record_id, check in summary["reference_results"].items():
            if check.get("missing_causes"):
                evaluation["valuable_cases"].append({"record_id":record_id, "kind":"missing_major_cause",
                    "missing_causes":check["missing_causes"], "basis":"authored_fixture_reference"})
    return {"schema_version":"phase2-report.v1", "provider_mode":provider,
        "fixture_digest":fingerprint(json.loads(FIXTURES.read_text())), "tasks":tasks,
        "availability":availability, "cases":summaries, "evaluation":evaluation,
        "records":[r.model_dump(mode="json") for r in store.records()],
        "comparisons":[c.model_dump(mode="json") for c in store.comparisons()],
        "journal":[r.model_dump(mode="json") for r in journal.read_from(global_sequence=1)],
        "efficiency_conclusion":"No cost/latency ranking: bounded judgment and rich synthesis are different workloads. Mock performance is excluded.",
        "phase3_recommendation":"Keep explicit task selection and human review until live, cohort-balanced measurements establish benefit against the existing baseline."}


def compact_report(result):
    """Keep inspectable artifacts, metrics and lineage without repeated schemas.

    Full runner output retains the complete hash-chained journal for replay.
    This compact report is for source-controlled examples, not journal restore.
    """
    compact = {k:v for k,v in result.items() if k not in {"journal", "records"}}
    compact["definition_snapshots"] = {r["task"]["task_id"]+f'.v{r["task"]["version"]}':r["task"] for r in result["records"]}
    compact["records"] = [{**r,"task":{k:v for k,v in r["task"].items() if k != "output_schema"}} for r in result["records"]]
    compact["journal_export"] = "Omitted in compact report; rerun without --compact for full immutable journal."
    return compact


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--lane",choices=("decision","reasoning","dual"),default="dual")
    parser.add_argument("--provider",choices=("mock","live","typesafe","anthropic","openai"),default="mock")
    for name in ("task","cohort","fixture"):
        parser.add_argument("--"+name)
    parser.add_argument("--limit",type=int)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--compact",action="store_true")
    args=parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    result=run(**{k:v for k,v in vars(args).items() if k not in {"output","compact"}})
    if args.compact:
        result=compact_report(result)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"cases":len(result["cases"]),"tasks":len(result["tasks"]),"coverage":result["evaluation"]["coverage"],"availability":result["availability"]}))
