"""Isolated, explicitly synthetic UI preview; never reads production settings."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from tpllm.config import TPLLMConfig
from tpllm.dashboard import create_app
from tpllm.decisions.engine import DecisionEngine
from tpllm.decisions.registry import default_registry as decisions
from tpllm.intelligence_tasks.fixtures import FixtureDecisionProvider, decision_batch, packet_for, reasoning_outputs
from tpllm.intelligence_tasks.registry import default_registry
from tpllm.reasoning.engine import ReasoningEngine
from tpllm.reasoning.providers import MockReasoningProvider
from tpllm.runtime.decision_journal import JournalDecisionStore
from tpllm.runtime.intelligence_journal import JournalIntelligenceStore
from tpllm.runtime.intelligence_service import IntelligenceTaskService


def create_demo(data_dir):
    app = create_app(TPLLMConfig(data_dir=Path(data_dir)), collect_in_background=False)
    ws = app.state.product_space("demo")
    ws.seed_demo()
    from tpllm.product_demo import seed_coordination
    seed_coordination(ws)
    store = JournalIntelligenceStore(ws.db.journal, ws.organization)
    if store.records():
        return app
    cases = json.loads((Path(__file__).parents[2]/"tests/fixtures/intelligence/scenarios.json").read_text())
    for case in [cases[i] for i in (0, 1, 2, 8, 14, 15)]:
        packet = packet_for(case)
        packet = packet.model_copy(update={"state":packet.state.model_copy(update={"project_id":"visual-search"})})
        dprovider = FixtureDecisionProvider(decision_batch(case, decisions()))
        rprovider = MockReasoningProvider(reasoning_outputs(case, packet))
        dengine = DecisionEngine(providers={"mock":dprovider},registry=decisions(),
            store=JournalDecisionStore(ws.db.journal,ws.organization),mode="shadow")
        rengine = ReasoningEngine(providers={"mock":rprovider},registry=default_registry(),store=store)
        IntelligenceTaskService(store=store,decision_engine=dengine,reasoning_engine=rengine).run(
            packet=packet,task_ids=("risk.present.v1","risk.family.v1","risk.severity.v1",
                "reasoning.root_cause_analysis.v1","reasoning.recovery_options.v1"),
            decision_provider="mock",decision_model="fixture-jev",reasoning_provider="mock",
            reasoning_model="fixture-reasoning",provenance="mock")
    return app


if __name__ == "__main__":
    import uvicorn
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-dir",type=Path,required=True)
    parser.add_argument("--port",type=int,default=8769)
    args=parser.parse_args()
    uvicorn.run(create_demo(args.data_dir),host="127.0.0.1",port=args.port)
