"""Run: python evals/decisions/run.py [--provider typesafe] [--output PATH]."""
import argparse
import json
from pathlib import Path

from tpllm.application.decision_calibration import quality_reports, decision_telemetry
from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.engine import DecisionEngine
from tpllm.decisions.evaluation import FIXTURE_TIME, evaluate_case, fixture_baseline, fixture_state
from tpllm.decisions.providers import MockDecisionProvider, TypeSafeJevProvider, parse_batch
from tpllm.decisions.registry import default_registry
from tpllm.persistence.journal import InMemoryEventJournalStore
from tpllm.runtime.decision_journal import JournalDecisionStore


def run(provider="mock"):
    cases = json.loads((Path(__file__).resolve().parents[2]/"tests/fixtures/decisions/scenarios.json").read_text())
    registry = default_registry()
    store = JournalDecisionStore(InMemoryEventJournalStore(), "fixture-evaluation")
    cfg = DecisionConfig.from_env() if provider == "typesafe" else DecisionConfig()
    results = []
    for case in cases:
        adapter = TypeSafeJevProvider(cfg) if provider == "typesafe" else MockDecisionProvider(parse_batch(
            {"model": "fixture-jev-v1", "answers": case["answers"], "usage": {"input_tokens": 0, "output_tokens": 0}}, registry.all()))
        engine = DecisionEngine(providers={provider: adapter}, registry=registry, store=store, mode="shadow", clock=lambda: FIXTURE_TIME)
        traces, labels = evaluate_case(case=case, engine=engine, provider=provider, model=cfg.model if provider == "typesafe" else "fixture-model")
        for label in labels:
            store.label(label)
        results.append({"case": case["id"], "state_description": case["description"], "expected": case["expected"],
            "state": fixture_state(case).model_dump(mode="json"),
            "baseline": fixture_baseline(case), "baseline_origin": case["baseline_origin"],
            "results": [{"definition": t.definition.definition_id, "definition_snapshot": t.definition.model_dump(mode="json"), "result": t.result.model_dump() if t.result else None,
                "status": t.policy_evaluation.status, "strength": t.policy_evaluation.strength,
                "disagreement": t.disagreement, "error": t.error} for t in traces]})
    return {"provider": provider, "provenance": "Synthetic fixtures and authored labels; not evidence of production improvement.",
        "cases": results, "metrics": quality_reports(store.traces(), store.labels(), include_fixtures=True),
        "telemetry": decision_telemetry(store.traces(), store.labels())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("mock", "typesafe"), default="mock")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.provider)
    content = json.dumps(report, indent=2)+"\n"
    if args.output:
        args.output.write_text(content)
    print(json.dumps({"provider": args.provider, "cases": len(report["cases"]), "decisions": sum(len(c["results"]) for c in report["cases"]),
        "unavailable": report["telemetry"]["errors"], "note": report["provenance"]}))
