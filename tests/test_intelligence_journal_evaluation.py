from datetime import timedelta

import pytest

from tpllm.application.intelligence_evaluation import report
from tpllm.decisions.evaluation import FIXTURE_TIME
from tpllm.intelligence_tasks.comparison import compare_lanes, compare_reference
from tpllm.intelligence_tasks.fixtures import evaluate_reference
from tpllm.intelligence_tasks.models import OutcomeEvent, ReviewEvent
from tpllm.persistence import SQLitePersistenceConfig, verify_journal
from tpllm.persistence.sqlite import initialize_sqlite_persistence
from tpllm.runtime.durability import RuntimeJournalReconstructor, append_outcome, build_replay_context
from tpllm.runtime.intelligence_journal import JournalIntelligenceStore
from tests._intelligence_fixtures import CASES, ROOT, run, setup


def review(row, **kwargs):
    return ReviewEvent(record_id=row.id, label="accept" if row.reasoning else "agree", reviewer="reviewer",
        observed_at=FIXTURE_TIME+timedelta(seconds=1), recorded_at=FIXTURE_TIME+timedelta(seconds=2), **kwargs)


def test_sqlite_restart_replays_both_lanes_without_execution_and_preserves_legacy(tmp_path):
    db=initialize_sqlite_persistence(SQLitePersistenceConfig(path=str(tmp_path/"history.db")))
    service,store,packet,_=setup(journal=db.journal)
    rows=run(service,packet)
    store.review(review(rows[-1]))
    before=rows[-1].model_dump(mode="json")
    # Phase 1 records still deserialize without the new optional attempts field.
    legacy=rows[0].decision.model_dump();legacy.pop("attempts")
    assert type(rows[0].decision).model_validate(legacy).result==rows[0].decision.result
    db.close()
    db=initialize_sqlite_persistence(SQLitePersistenceConfig(path=str(tmp_path/"history.db")))
    restored=JournalIntelligenceStore(db.journal,"test")
    assert restored.records()[-1].model_dump(mode="json")==before and restored.packets()[0].id==packet.id
    assert restored.reviews() and restored.comparisons()
    assert verify_journal(journal=db.journal).valid
    class NoServices:
        def __getattr__(self, name): pytest.fail("Replay must not call any intelligence or action service")
    outcome=RuntimeJournalReconstructor(intelligence_services=NoServices()).apply_records(
        records=db.journal.read_from(global_sequence=1),organization_id="test",
        context=build_replay_context(replay_as_of=FIXTURE_TIME+timedelta(days=1)))
    assert len(outcome.bounded_decision_history)==3
    assert len(outcome.intelligence_task_history)==7
    assert not outcome.historical_decisions
    db.close()


def test_reviews_append_correct_without_rewriting_results_or_backdating():
    service,store,packet,_=setup();row=run(service,packet,tasks=(ROOT,))[0]
    original=row.model_dump_json()
    first=review(row);store.review(first)
    with pytest.raises(ValueError): store.review(review(row))
    correction=review(row,supersedes=first.id).model_copy(update={"label":"corrected_interpretation"})
    store.review(correction)
    assert len(store.reviews())==2 and store.get(row.id).model_dump_json()==original
    with pytest.raises(ValueError): store.review(review(row).model_copy(update={"observed_at":FIXTURE_TIME-timedelta(seconds=1)}))
    assert not store.reviews(known_at=FIXTURE_TIME)
    result=report(store)
    segment=result["segments"][0]
    assert segment["human_acceptance"]==0 and segment["human_correction_rate"]==1
    assert "brier" not in segment and segment["status"]=="EARLY"


def test_structured_outcomes_attach_at_t1_without_changing_t0_context():
    service,store,packet,_=setup();row=run(service,packet,tasks=("risk.present.v1",))[0]
    before=store.packets()[0].model_dump_json()
    stamp=FIXTURE_TIME+timedelta(days=1)
    signal=append_outcome(journal=store.journal,organization_id="test",outcome_id="outcome-1",project_id=row.project_id,
        decision_id=None,kpi_ids=[],milestone_ids=[],observed_value="Deployment recovered",outcome_type="deployment_recovered",
        occurred_at=stamp,recorded_at=stamp)
    event=OutcomeEvent(record_id=row.id,outcome="deployment_recovered",source_evidence_ref=signal.id,
        observed_at=stamp,recorded_at=stamp,decision_value=False)
    store.attach_outcome(event)
    comparison=compare_reference(row,event,kind="outcome")
    store.compare(comparison)
    assert comparison.state=="conflict"
    assert store.packets()[0].model_dump_json()==before
    assert not store.outcomes(known_at=FIXTURE_TIME)
    assert report(store,known_at=FIXTURE_TIME)["segments"][0]["labelled_samples"]==0
    assert report(store)["segments"][0]["labelled_samples"]==1
    with pytest.raises(ValueError): store.attach_outcome(event.model_copy(update={"source_evidence_ref":"invented"}))


def test_free_text_outcome_is_not_promoted_to_structured_truth():
    service,store,packet,_=setup();row=run(service,packet,tasks=(ROOT,))[0]
    stamp=FIXTURE_TIME+timedelta(days=1)
    signal=append_outcome(journal=store.journal,organization_id="test",outcome_id="prose",project_id=row.project_id,
        decision_id=None,kpi_ids=[],milestone_ids=[],observed_value="deployment_recovered",occurred_at=stamp,recorded_at=stamp)
    with pytest.raises(ValueError):
        store.attach_outcome(OutcomeEvent(record_id=row.id,outcome="deployment_recovered",source_evidence_ref=signal.id,
            observed_at=stamp,recorded_at=stamp))


@pytest.mark.parametrize("case,expected",[(0,"compatible"),(6,"compatible"),(8,"compatible"),(15,"conflict"),(21,"ambiguous")])
def test_comparison_semantics_preserve_compatible_multicausal_interpretations(case,expected):
    service,store,packet,_=setup(case=case);run(service,packet)
    assert store.comparisons()[0].state==expected


def test_overlap_near_tie_and_different_packet_not_false_equality():
    service,store,packet,_=setup(case=8);rows=run(service,packet)
    d=next(r for r in rows if r.task.task_id=="risk.family");r=rows[-1]
    r.reasoning.structured_output["operational_interpretation"]=["dependency"]
    assert compare_lanes(d,r,at=FIXTURE_TIME).state=="partially_compatible"
    _,_,other,_=setup(case=0)
    r=r.model_copy(update={"evidence_packet_id":other.id})
    # Store also checks lineage on insert, not only in the comparison helper.
    with pytest.raises(ValueError): store.append(r)


def test_model_task_cohort_segments_and_no_double_counting_batches():
    service,store,packet,provider=setup();run(service,packet)
    provider.resolved_model="reasoning-fixture-v2"
    run(service,packet,force=True)
    result=report(store)
    reasoning=[s for s in result["segments"] if s["lane"]=="reasoning"]
    assert {s["resolved_model"] for s in reasoning}=={"reasoning-fixture-v1","reasoning-fixture-v2"}
    decisions=next(e for e in result["efficiency"] if e["lane"]=="decision")
    assert decisions["requests"]==2 and decisions["questions_per_request"]==3
    assert all(e["median_latency_ms"] is None and e["cost_usd"] is None for e in result["efficiency"])
    assert all(s["samples"]==1 for s in result["segments"])


def test_evaluator_detects_its_deliberate_missing_major_causes():
    service,store,packet,_=setup(case=2);row=run(service,packet,tasks=(ROOT,))[0]
    check=evaluate_reference(row,CASES[2]["reference"])
    assert set(check["missing_causes"])=={"capacity_reduction","test_outage"}
    assert check["required_cause_coverage"]==pytest.approx(1/3)


def test_label_type_and_lane_rejection():
    service,store,packet,_=setup();rows=run(service,packet)
    with pytest.raises(ValueError): store.review(review(rows[-1],decision_value=.99))
    with pytest.raises(ValueError): store.review(review(rows[0]).model_copy(update={"label":"accept"}))
    with pytest.raises(ValueError): store.review(review(rows[0],decision_value="true"))


def test_unknown_task_and_disabled_providers_remain_coverage_gaps():
    service,store,packet,_=setup();service.decision_engine=service.reasoning_engine=None
    assert run(service,packet,tasks=(ROOT,"risk.present.v1","missing.v1"))==[]
    result=report(store)
    assert result["coverage"]["eligible"]==3 and result["coverage"]["skipped"]==3
    assert result["coverage"]["skip_reasons"]=={"provider_disabled":2,"unsupported_task":1}
