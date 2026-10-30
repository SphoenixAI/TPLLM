import ast
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jsonschema")
from tpllm.cli_support import now
from tpllm.config import TPLLMConfig
from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.providers import MockDecisionProvider, DecisionProviderError
from tpllm.product_api import ProductWorkspace
from tpllm.product_decisions import assess
from tpllm.product_demo import seed_coordination
from tpllm.product_flow import derive
from tpllm.product_authority import eligible, execute_prepared
from tpllm.runtime.decision_journal import JournalDecisionStore
from tests._decision_fixtures import batch, setup


@pytest.fixture
def ws(tmp_path):
    ws=ProductWorkspace(TPLLMConfig(data_dir=tmp_path),demo=True)
    ws.seed_demo();seed_coordination(ws)
    yield ws
    ws.db.close()


def enable(ws, mode="shadow"):
    ws.decision_config=DecisionConfig(enabled=True,mode=mode,model="jev-latest")
    ws.decision_provider=MockDecisionProvider(batch(3))
    ws.decision_provider_name="mock"


def test_shadow_does_not_change_existing_kpis_recommendations_actions_or_authority(ws):
    at=now()
    base=deepcopy(derive(ws,at=at))
    grants=deepcopy(ws.get("delegations"));receipts=deepcopy(ws.get("action_receipts"))
    before=[eligible(ws,a,at) for a in base["actions"]]
    enable(ws)
    after=derive(ws,at=at)
    assert after==base
    assert ws.get("delegations")==grants and ws.get("action_receipts")==receipts
    assert [eligible(ws,a,at) for a in after["actions"]]==before
    detail=ws.snapshot()["decision_intelligence"]
    assert detail["traces"] and detail["mode"]=="shadow"
    assert all(t["observation_refs"] for t in detail["traces"])
    source_ids={r["evidence_id"] for r in base["records"]}
    assert all(set(t["evidence_refs"]) <= source_ids for t in detail["traces"])
    assert all(not t["policy_evaluation"]["influence_allowed"] for t in detail["traces"])
    assert all(not i["contributing_trace_ids"] for i in detail["insights"])
    assert JournalDecisionStore(ws.db.journal,ws.organization).effects()
    # Product journals use naive UTC; replay must not encounter a mix after
    # decision records are added to the same chain.
    assert ws.analyze()["published"]


def test_advisory_requires_calibration_and_cannot_bypass_kill_switch(ws):
    enable(ws,"advisory");ws.put("coordination_paused",True)
    projection=derive(ws)
    assert ws.snapshot()["decision_intelligence"]["traces"]
    for action in projection["actions"]:
        assert execute_prepared(ws,action,at=now(),sender=lambda *a:pytest.fail("must not send"))["status"]=="held"
    assert all(not i["contributing_trace_ids"] for i in ws.snapshot()["decision_intelligence"]["insights"])
    # The separate runtime authority gate stays above even its strongest baseline recommendation.
    from tests.test_runtime_autonomy_races import _attempt
    from tests._runtime_fixtures import autonomy_state
    result,finalization=_attempt(autonomy_state=autonomy_state(kill_switch_engaged=True))
    assert not result.executed and finalization is None


def test_provider_failure_retains_baseline_and_records_unavailable(ws):
    at=now();base=deepcopy(derive(ws,at=at));enable(ws)
    def fail(**kwargs):raise DecisionProviderError("timeout")
    ws.decision_provider.evaluate=fail
    assert derive(ws,at=at)==base
    d=ws.snapshot()["decision_intelligence"]
    assert d["traces"] and all(t["result"] is None and t["error"]=="timeout" for t in d["traces"])
    assert all(not t["policy_evaluation"]["influence_allowed"] for t in d["traces"])


def test_validator_runs_for_each_candidate_and_rejection_is_preserved(ws,monkeypatch):
    import tpllm.product_decisions as product
    from tpllm.ontology.validation import ValidationResult
    called=[]
    def reject(insight,**kwargs):
        called.append(insight.id)
        return ValidationResult(insight_id=insight.id,passed=False,outcome="insufficient_evidence",reasons=["Independent validator rejection"])
    monkeypatch.setattr(product,"validate_insight",reject)
    enable(ws,"advisory");derive(ws)
    d=ws.snapshot()["decision_intelligence"]
    assert len(called)==len(d["insights"])>0
    assert all(not i["validation"]["passed"] and i["recommendation"] is None for i in d["insights"])


def test_disabling_hides_advisory_effects_and_stops_calls(ws):
    enable(ws);derive(ws);calls=ws.decision_provider.calls
    ws.decision_config=DecisionConfig()
    derive(ws)
    assert ws.decision_provider.calls==calls
    assert ws.snapshot()["decision_intelligence"]["mode"]=="off"


def test_new_package_has_no_action_permission_or_persistence_dependencies():
    root=Path(__file__).resolve().parents[1]/"src/tpllm/decisions"
    for path in root.glob("*.py"):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                assert not (node.module or "").startswith(("tpllm.tools","tpllm.product_authority","tpllm.runtime","tpllm.persistence","tpllm.application"))
    from tests.test_architecture_boundaries import build_first_party_import_graph, FIRST_PARTY_PACKAGES, assert_no_forbidden_package_cycles
    assert_no_forbidden_package_cycles(build_first_party_import_graph(FIRST_PARTY_PACKAGES+["decisions"]))


def test_governed_advisory_changes_only_review_priority_and_is_journalled(ws,tmp_path):
    import json
    from tpllm.product_decisions import snapshot
    from tpllm.decisions.models import CalibrationLabel
    from tpllm.decisions.policy import calibration_key
    enable(ws,"shadow");at=now();base=deepcopy(derive(ws,at=at))
    store=JournalDecisionStore(ws.db.journal,ws.organization)
    traces=[t for t in store.traces() if t.definition.decision_id=="review.human_recommended"]
    for t in traces:
        store.label(CalibrationLabel(trace_id=t.id,value=True,source="human_review",reviewer="test-reviewer",
            evidence_refs=("test-observed-outcome",),observed_at=at+timedelta(seconds=1),recorded_at=at+timedelta(seconds=1)))
    t=traces[0];key=calibration_key(t.definition,t.provider,t.model_resolved,t.project_id)
    policy_path=tmp_path/"decision-policy.json"
    policy_path.write_text(json.dumps({"policy":{"approved_segments":[key],"min_samples":1},
        "calibration_approvals":[{"key":key,"approved":True,"expires_at":(at+timedelta(days=1)).isoformat(),"evidence_refs":["test-governed-approval"]}]}))
    ws.decision_config=DecisionConfig(enabled=True,mode="advisory",model="jev-latest",policy_path=str(policy_path))
    assess(ws,base,at=at+timedelta(seconds=2))
    d=snapshot(ws, at=at+timedelta(seconds=3))
    assert all(i["priority"]=="review" for i in d["insights"])
    assert all(i["contributing_trace_ids"] for i in d["insights"])
    assert ws.get("coordination")==base
    assert any(e["downstream_effect"]=="review_priority" for e in store.effects())
    approved = policy_path.read_text()
    policy_path.write_text(json.dumps({"policy": {}, "calibration_approvals": []}))
    calls = ws.decision_provider.calls
    assert all(i["priority"]=="normal" for i in snapshot(ws, at=at+timedelta(seconds=3))["insights"])
    assert ws.decision_provider.calls == calls
    policy_path.write_text(approved)
    ws.put("coordination_paused",True)
    assess(ws,base,at=at+timedelta(seconds=3))
    assert all(i["priority"]=="normal" for i in snapshot(ws, at=at+timedelta(seconds=3))["insights"])
