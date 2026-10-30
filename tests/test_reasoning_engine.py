import copy
import json
from types import SimpleNamespace as NS

import pytest

from tpllm.intelligence_tasks.config import IntelligenceConfig
from tpllm.intelligence_tasks.registry import default_registry
from tpllm.reasoning.providers import ReasoningProviderError, StructuredLLMProvider
from tests._intelligence_fixtures import ROOT, run, setup


@pytest.mark.parametrize("task", [t.definition_id for t in default_registry().all() if t.lane=="reasoning"])
def test_all_structured_contracts_validate_locally_and_retain_version(task):
    service,store,packet,_=setup()
    row=run(service,packet,tasks=(task,))[0]
    assert row.status=="available" and row.reasoning.schema_valid and row.reasoning.evidence_refs_valid
    assert row.reasoning.schema_id==task and row.reasoning.schema_version==1
    assert row.resolved_model=="reasoning-fixture-v1" and row.requested_model=="fixture"
    assert row.reasoning.downstream_effect=="none"


@pytest.mark.parametrize("fault", ["malformed", "missing", "enum", "extra", "tool_call", "confidence", "duplicate", "oversized", "numeric_string"])
def test_provider_schema_claim_is_not_trusted(fault):
    service,store,packet,provider=setup()
    value=provider.outputs[ROOT]
    if fault=="malformed": value="{bad"
    elif fault=="missing": del value["primary_hypothesis"]
    elif fault=="enum": value["completion"]="execute_now"
    elif fault=="extra": value["grant_authority"]=True
    elif fault=="tool_call": value["tool_calls"]=[{"name":"send_message"}]
    elif fault=="confidence": value["probability"]=.99
    elif fault=="duplicate": value=json.dumps(value)[:-1]+',"completion":"complete"}'
    elif fault=="oversized": value["primary_hypothesis"]="x"*2001
    elif fault=="numeric_string": value["unknowns"]=[123]
    provider.outputs[ROOT]=value
    row=run(service,packet,tasks=(ROOT,))[0]
    assert row.status=="rejected" and row.failure_kind=="schema_failure"
    assert row.reasoning.structured_output is None


@pytest.mark.parametrize("field", ["supporting_evidence_refs","contradictory_evidence_refs","entity_refs","ontology_refs","task_refs"])
def test_unknown_ids_rejected(field):
    service,store,packet,provider=setup()
    provider.outputs[ROOT][field]=["invented-id"]
    row=run(service,packet,tasks=(ROOT,))[0]
    assert row.reasoning.schema_valid and not row.reasoning.evidence_refs_valid
    assert row.status=="rejected" and row.reasoning.unsupported_reference_count==1


def test_nested_recovery_evidence_and_insight_identity_are_checked():
    for task,edit in [("reasoning.recovery_options.v1",lambda o:o["options"][0].update(evidence_refs=["invented"])),
                      ("reasoning.insight_explanation.v1",lambda o:o.update(insight_ref="not-the-insight"))]:
        service,store,packet,provider=setup();edit(provider.outputs[task])
        row=run(service,packet,tasks=(task,))[0]
        assert row.status=="rejected" and row.reasoning.unsupported_reference_count==1


def test_missing_insight_or_evidence_skips_without_provider_call():
    service,store,packet,provider=setup()
    packet=packet.model_copy(update={"insight":None})
    row=run(service,packet,tasks=("reasoning.insight_explanation.v1",))[0]
    assert row.status=="skipped" and row.reason=="insufficient_state" and provider.calls==0


def test_provider_failure_is_unavailable_not_no_risk_and_exception_is_not_logged(caplog):
    service,store,packet,provider=setup()
    provider.outputs[ROOT]=RuntimeError("sensitive exception content")
    row=run(service,packet,tasks=(ROOT,))[0]
    assert row.status=="unavailable" and row.reasoning.structured_output is None
    assert "sensitive exception content" not in row.model_dump_json()+caplog.text


def test_provider_cannot_mutate_trusted_schema():
    service,store,packet,provider=setup()
    original=provider.synthesize
    def malicious(**kwargs):
        kwargs["task"].output_schema.clear()
        return original(**kwargs)
    provider.synthesize=malicious
    row=run(service,packet,tasks=(ROOT,))[0]
    assert row.task.output_schema and row.status=="available"


def fake_client(provider, content, failures=()):
    calls=[]
    failures=list(failures)
    def create(**kwargs):
        calls.append(kwargs)
        if failures: raise failures.pop(0)
        if provider=="anthropic":
            return NS(stop_reason="end_turn",content=[NS(type="text",text=content)],
                usage=NS(input_tokens=10,output_tokens=20),model="pinned-20261030",id="response-1")
        return NS(choices=[NS(finish_reason="stop",message=NS(content=content,refusal=None,tool_calls=None))],
            usage=NS(prompt_tokens=10,completion_tokens=20),model="pinned-20261030",id="response-1")
    return (NS(messages=NS(create=create)) if provider=="anthropic" else NS(chat=NS(completions=NS(create=create)))),calls


@pytest.mark.parametrize("provider",["anthropic","openai"])
def test_native_schema_transport_no_tools_bounded_retry_and_usage(provider):
    client,calls=fake_client(provider,"{}",[TimeoutError()])
    sleeps=[]
    adapter=StructuredLLMProvider(provider=provider,client=client,settings=IntelligenceConfig(),sleep=sleeps.append)
    result=adapter.synthesize(task=default_registry().get(ROOT),context="untrusted evidence",model="requested-alias")
    assert result.attempts==2 and result.resolved_model=="pinned-20261030" and result.usage.input_count==10
    assert len(sleeps)==1 and len(calls)==2
    assert all("tools" not in c and c["timeout"]==10 for c in calls)
    assert "output_config" in calls[0] if provider=="anthropic" else calls[0]["response_format"]["json_schema"]["strict"]


@pytest.mark.parametrize("status,expected,retry",[(401,"invalid_credentials",False),(400,"unsupported_schema_or_model",False),(429,"rate_limited",True),(503,"provider_failure",True)])
def test_provider_retry_taxonomy(status,expected,retry):
    class Failure(Exception): status_code=status
    client,calls=fake_client("openai","{}",[Failure(),Failure()])
    adapter=StructuredLLMProvider(provider="openai",client=client,settings=IntelligenceConfig(),sleep=lambda _:None)
    with pytest.raises(ReasoningProviderError) as error:
        adapter.synthesize(task=default_registry().get(ROOT),context="{}",model="fixture")
    assert error.value.code==expected and len(calls)==(2 if retry else 1)


def test_timeout_exhaustion_does_not_create_synthetic_output():
    client,calls=fake_client("anthropic","{}",[TimeoutError(),TimeoutError()])
    adapter=StructuredLLMProvider(provider="anthropic",client=client,settings=IntelligenceConfig(),sleep=lambda _:None)
    with pytest.raises(ReasoningProviderError) as error:
        adapter.synthesize(task=default_registry().get(ROOT),context="{}",model="fixture")
    assert error.value.code=="timeout" and error.value.attempts==2


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("fault", ["refusal", "truncated", "tool_call"])
def test_native_refusal_incomplete_or_tool_response_is_not_an_artifact(provider, fault):
    if provider == "anthropic":
        response = NS(stop_reason={"refusal":"refusal", "truncated":"max_tokens", "tool_call":"end_turn"}[fault],
            content=[NS(type="tool_use" if fault == "tool_call" else "text", text="{}")])
    else:
        response = NS(choices=[NS(finish_reason="length" if fault == "truncated" else "stop",
            message=NS(content="{}", refusal="declined" if fault == "refusal" else None,
                tool_calls=[{}] if fault == "tool_call" else None))])
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return response
    client = NS(messages=NS(create=create)) if provider == "anthropic" else NS(chat=NS(completions=NS(create=create)))
    adapter = StructuredLLMProvider(provider=provider, client=client, settings=IntelligenceConfig())
    with pytest.raises(ReasoningProviderError) as error:
        adapter.synthesize(task=default_registry().get(ROOT), context="{}", model="fixture")
    assert error.value.code == "refused_or_incomplete" and error.value.attempts == 1
    assert len(calls) == 1
