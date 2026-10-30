"""Export a complete synthetic product trace through existing ingestion/validation.

python evals/decisions/demo.py --output /tmp/tpllm-decision-demo.json
"""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta

from tpllm.cli_support import now
from tpllm.config import TPLLMConfig
from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.providers import MockDecisionProvider, parse_batch
from tpllm.decisions.registry import default_registry
from tpllm.models.program import Program, SuccessMetric
from tpllm.product_api import ProductWorkspace
from tpllm.product_flow import accept_collection
from tpllm.product_sources import Collection, SourceRecord
from tpllm.runtime.decision_journal import JournalDecisionStore


class CapturingFixtureProvider(MockDecisionProvider):
    def evaluate(self, **kwargs):
        self.state = kwargs["state"]
        return super().evaluate(**kwargs)


def demo():
    case = json.loads((Path(__file__).resolve().parents[2]/"tests/fixtures/decisions/scenarios.json").read_text())[0]
    with TemporaryDirectory() as directory:
        ws = ProductWorkspace(TPLLMConfig(data_dir=Path(directory)), demo=True)
        ws.store.save("schedule-demo", "program", Program(id="schedule-demo", name="Dependency readiness",
            goal="Deliver dependencies before release", success_metrics=[SuccessMetric(name="Dependency deliveries", target="100%")]))
        provider = CapturingFixtureProvider(parse_batch({"model":"jev-fixture-1","answers":case["answers"],
            "usage":{"input_tokens":0,"output_tokens":0}}, default_registry().all()))
        ws.decision_config = DecisionConfig(enabled=True, mode="shadow", include_source_excerpts=True)
        ws.decision_provider, ws.decision_provider_name = provider, "mock"
        stamp = now()
        sources = [SourceRecord(provider=s["source"], scope="fixture-scope", source_id="schedule-"+s["source"],
            project_id="schedule-demo", kind="issue", title="Synthetic schedule evidence", observed_at=stamp-timedelta(minutes=10),
            text=s["text"]+(" Dependency deliveries is 60%." if s["source"]=="jira" else ""),
            data={**s["data"],"synthetic":True,"observation_basis":"simulated_observation"}) for s in case["sources"]]
        accept_collection(ws, Collection(records=sources), at=stamp)
        store = JournalDecisionStore(ws.db.journal,ws.organization)
        report = {"provenance":"Synthetic sources, mock probabilities; no TypeSafe call or external action.",
            "sources":[s.model_dump(mode="json") for s in sources], "state":provider.state.model_dump(mode="json"),
            "traces":[t.model_dump(mode="json") for t in store.traces()], "effects":store.effects(),
            "journal":[{"event":r.event_type.value,"sequence":r.position.global_sequence,"hash":r.record_hash,
                "previous_hash":r.previous_record_hash} for r in ws.db.journal.read_from(global_sequence=1)],
            "authority":"No external action proposed. Existing delegation, validation and kill-switch controls remain authoritative."}
        ws.db.close()
        return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();report=demo();args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"traces":len(report["traces"]),"effects":len(report["effects"]),
        "validation":report["effects"][0]["validation"]["outcome"],"provenance":report["provenance"]}))
