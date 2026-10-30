import json
from pathlib import Path
from datetime import timedelta

from tpllm.decisions.evaluation import FIXTURE_TIME, fixture_state
from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.engine import DecisionEngine
from tpllm.decisions.providers import MockDecisionProvider, parse_batch
from tpllm.decisions.registry import default_registry
from tpllm.persistence.journal import InMemoryEventJournalStore
from tpllm.runtime.decision_journal import JournalDecisionStore

CASES = json.loads((Path(__file__).parent/"fixtures/decisions/scenarios.json").read_text())


def batch(case=0):
    return parse_batch({"model": "jev-fixture-1", "answers": CASES[case]["answers"],
        "usage": {"input_tokens": 150, "output_tokens": 10}}, default_registry().all())


def setup(mode="shadow", case=0, store=None, gate=None):
    adapter = MockDecisionProvider(batch(case))
    store = store or JournalDecisionStore(InMemoryEventJournalStore(), "test")
    engine = DecisionEngine(providers={"mock": adapter}, registry=default_registry(), store=store,
        mode=mode, policy_gate=gate, clock=lambda: FIXTURE_TIME)
    return engine, adapter, store, fixture_state(CASES[case])
