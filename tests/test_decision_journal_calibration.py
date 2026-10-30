from datetime import timedelta

import pytest

from tpllm.application.decision_calibration import quality_reports, measured_profiles, decision_telemetry
from tpllm.application.registry import ActiveCalibrationRegistry
from tpllm.decisions.models import CalibrationLabel
from tpllm.decisions.evaluation import FIXTURE_TIME
from tpllm.persistence import SQLitePersistenceConfig, verify_journal
from tpllm.persistence.sqlite import initialize_sqlite_persistence
from tpllm.runtime.decision_journal import JournalDecisionStore
from tpllm.runtime.durability import RuntimeJournalReconstructor, build_replay_context
from tests._decision_fixtures import setup, CASES


def run(engine,state,**kwargs):
    return engine.evaluate(provider="mock",model="jev-latest",state=state,**kwargs)


def label(t, value, **kwargs):
    return CalibrationLabel(trace_id=t.id,value=value,source="human_review",reviewer="reviewer",
        evidence_refs=("observed-result",), observed_at=FIXTURE_TIME+timedelta(seconds=1),
        recorded_at=FIXTURE_TIME+timedelta(seconds=2), **kwargs)


def test_sqlite_restart_chain_temporal_lineage_and_read_forward(tmp_path):
    config=SQLitePersistenceConfig(path=str(tmp_path/"history.db"))
    db=initialize_sqlite_persistence(config)
    store=JournalDecisionStore(db.journal,"test")
    e,p,_,s=setup(store=store)
    first=run(e,s)
    e.clock=lambda:FIXTURE_TIME+timedelta(minutes=1)
    second=run(e,s,force=True)
    assert second[0].supersedes==first[0].id
    assert len(store.traces(known_at=FIXTURE_TIME))==10
    assert len(store.traces(valid_at=FIXTURE_TIME-timedelta(seconds=1)))==0
    before=db.journal.read_from(global_sequence=1)[0].record_hash
    db.close()
    db=initialize_sqlite_persistence(config)
    store=JournalDecisionStore(db.journal,"test")
    assert len(store.traces())==20 and store.get(first[0].id).state_hash==s.state_hash
    assert store.get(first[0].id).model_requested=="jev-latest"
    assert store.get(first[0].id).model_resolved=="jev-fixture-1"
    assert store.get(first[0].id).evidence_refs==s.evidence_refs
    assert db.journal.read_from(global_sequence=1)[0].record_hash==before
    assert verify_journal(journal=db.journal).valid
    db.close()


def test_labels_are_append_only_and_metrics_do_not_promote():
    e,_,store,s=setup();traces=run(e,s)
    t=next(t for t in traces if t.definition.decision_id=="risk.present")
    one=label(t,True);store.label(one)
    original=store.get(t.id).model_dump_json()
    with pytest.raises(ValueError):store.label(label(t,False))
    two=label(t,False,supersedes=one.id);store.label(two)
    assert len(store.labels())==2 and store.get(t.id).model_dump_json()==original
    report=next(iter(quality_reports(traces,store.labels()).values()))
    assert report["samples"]==1 and report["brier"]==pytest.approx(.93**2)
    assert report["false_positive_rate"]==1
    assert quality_reports(traces,store.labels(),known_at=FIXTURE_TIME)=={}
    profiles=measured_profiles(registry=ActiveCalibrationRegistry(),traces=traces,labels=store.labels(),at=FIXTURE_TIME+timedelta(days=1))
    assert all(not p.approved for p in profiles.values())
    assert decision_telemetry(traces,store.labels())["human_overrides"]==1


@pytest.mark.parametrize("key,bad",[("risk.present",1),("risk.family","invalid"),("risk.severity",5)])
def test_invalid_labels_rejected(key,bad):
    e,_,store,s=setup();traces=run(e,s)
    t=next(t for t in traces if t.definition.decision_id==key)
    with pytest.raises(ValueError):store.label(label(t,bad))
    assert not store.labels()


def test_label_temporal_order_and_evidence_required():
    e,_,store,s=setup();t=run(e,s)[0]
    base=label(t,False)
    for change in ({"observed_at":FIXTURE_TIME-timedelta(seconds=1)}, {"recorded_at":FIXTURE_TIME}, {"evidence_refs":()}):
        with pytest.raises(ValueError):store.label(base.model_copy(update=change))


def test_fixture_metrics_segment_by_definition_and_do_not_enter_runtime_calibration():
    e,_,store,s=setup();traces=run(e,s)
    labels=[]
    for t in traces:
        l=label(t,CASES[0]["expected"][t.definition.definition_id]).model_copy(update={"source":"fixture"})
        store.label(l);labels.append(l)
    assert quality_reports(traces,labels)=={}
    reports=quality_reports(traces,labels,include_fixtures=True)
    assert len(reports)==10
    choice=next(v for k,v in reports.items() if "risk.family.v1" in k)
    assert choice["accuracy"]==1 and choice["top_2_coverage"]==1
    score=next(v for k,v in reports.items() if "risk.severity.v1" in k)
    assert score["mean_absolute_error"]==pytest.approx(.4)
    metrics=decision_telemetry(traces,labels)
    assert metrics["calls"]==1 and metrics["input_tokens"]==150 and metrics["questions_per_request"]==[10]


def test_reconstruction_restores_answers_without_provider_or_execution():
    e,p,store,s=setup();run(e,s)
    class NoServices:
        def __getattr__(self,name):
            pytest.fail("Decision history must not invoke inference or execution during replay")
    outcome=RuntimeJournalReconstructor(intelligence_services=NoServices()).apply_records(
        records=store.journal.read_from(global_sequence=1),organization_id="test",
        context=build_replay_context(replay_as_of=FIXTURE_TIME+timedelta(days=1)))
    assert len(outcome.bounded_decision_history)==10
    assert not outcome.historical_decisions and p.calls==1


def test_journal_failure_never_yields_unrecorded_advice():
    e,_,store,s=setup("advisory")
    def fail(t):raise OSError("disk failure")
    store.append=fail
    with pytest.raises(OSError):run(e,s)
