import json
from pathlib import Path

from tpllm.decisions.engine import DecisionEngine
from tpllm.decisions.evaluation import FIXTURE_TIME
from tpllm.decisions.providers import MockDecisionProvider
from tpllm.decisions.registry import default_registry as decisions
from tpllm.intelligence_tasks.fixtures import FixtureDecisionProvider, decision_batch, packet_for, reasoning_outputs
from tpllm.intelligence_tasks.registry import default_registry
from tpllm.persistence.journal import InMemoryEventJournalStore
from tpllm.reasoning.engine import ReasoningEngine
from tpllm.reasoning.providers import MockReasoningProvider
from tpllm.runtime.decision_journal import JournalDecisionStore
from tpllm.runtime.intelligence_journal import JournalIntelligenceStore
from tpllm.runtime.intelligence_service import IntelligenceTaskService

CASES = json.loads((Path(__file__).parent/"fixtures/intelligence/scenarios.json").read_text())
ROOT = "reasoning.root_cause_analysis.v1"


def setup(case=0, journal=None, outputs=None):
    case = CASES[case]
    journal = journal if journal is not None else InMemoryEventJournalStore()
    store = JournalIntelligenceStore(journal, "test")
    packet = packet_for(case)
    provider = MockReasoningProvider(outputs or reasoning_outputs(case, packet))
    engine = ReasoningEngine(providers={"mock":provider}, registry=default_registry(), store=store, clock=lambda: FIXTURE_TIME)
    dprovider = FixtureDecisionProvider(decision_batch(case, decisions()))
    dengine = DecisionEngine(providers={"mock":dprovider}, registry=decisions(),
        store=JournalDecisionStore(journal,"test"), mode="shadow", clock=lambda: FIXTURE_TIME)
    service = IntelligenceTaskService(store=store, decision_engine=dengine, reasoning_engine=engine, clock=lambda: FIXTURE_TIME)
    return service, store, packet, provider


def run(service, packet, tasks=("risk.present.v1", "risk.family.v1", "risk.severity.v1", ROOT), **kwargs):
    return service.run(packet=packet, task_ids=tasks, decision_provider="mock", decision_model="fixture",
        reasoning_provider="mock", reasoning_model="fixture", provenance="mock", **kwargs)
