import io
import json
import socket
import urllib.error
from copy import deepcopy

import pytest

from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.providers import TypeSafeJevProvider, DecisionProviderError, parse_batch
from tpllm.decisions.registry import default_registry
from tpllm.decisions.evaluation import fixture_state
from tests._decision_fixtures import CASES


def payload():
    return {"model": "jev-resolved-test", "answers": deepcopy(CASES[0]["answers"]),
        "usage": {"input_tokens": 321, "output_tokens": 12}}


def provider(transport, **kwargs):
    cfg = DecisionConfig(enabled=True, mode="shadow", api_key="test-only", **kwargs)
    return TypeSafeJevProvider(cfg, transport=transport, sleep=lambda _: None)


def call(p):
    return p.evaluate(state=fixture_state(CASES[0]), questions=default_registry().all(), model="configured-model")


def test_endpoint_auth_batch_model_no_experimental_headers():
    def transport(request, timeout):
        assert request.full_url == "https://api.typesafe.ai/v1/systemone"
        assert request.method == "POST"
        assert request.get_header("Authorization") == "Bearer test-only"
        assert not any("danger" in key.lower() for key in request.headers)
        assert timeout == 3
        body = json.loads(request.data)
        assert body["model"] == "configured-model"
        assert len(body["questions"]) == 10
        assert isinstance(body["state"], dict)
        assert "assets" not in body
        return io.BytesIO(json.dumps(payload()).encode())
    result = call(provider(transport, timeout_seconds=3))
    assert result.model_resolved == "jev-resolved-test"
    assert result.usage.input_count == 321
    noul = result.results["risk.present.v1"]
    assert noul.probability == .93 and noul.selected_value == .93 and noul.confidence is None
    choice = result.results["risk.family.v1"]
    assert choice.selected_value == "dependency" and choice.probability_distribution["schedule"] == .14
    score = result.results["risk.severity.v1"]
    assert score.selected_value == pytest.approx(3.4)
    assert len(score.legend) == len(score.probability_distribution) == 5


def test_config_environment_server_secret(monkeypatch):
    for k,v in {"TYPESAFE_ENABLED":"true", "JEV_MODE":"advisory", "TYPESAFE_MODEL":"custom-2", "TYPESAFE_API_KEY":"private-test-key"}.items():
        monkeypatch.setenv(k,v)
    cfg = DecisionConfig.from_env()
    assert cfg.active and cfg.model == "custom-2"
    assert "private-test-key" not in str(cfg) + cfg.model_dump_json()


@pytest.mark.parametrize("status,retries,code", [(401,1,"invalid_credentials"),(403,1,"forbidden"),(404,1,"model_unavailable"),
    (422,1,"api_error"),(429,2,"rate_limited"),(503,2,"api_error"),(529,2,"api_error"),(302,1,"api_error")])
def test_http_failures_are_bounded_and_scrubbed(status,retries,code):
    calls=[]
    def transport(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("url",status,"secret error body",{},io.BytesIO(b"credential"))
    with pytest.raises(DecisionProviderError, match=code) as caught:
        call(provider(transport))
    assert len(calls)==retries
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("error,code", [(socket.timeout(),"timeout"),(urllib.error.URLError("sensitive-data"),"network_failure")])
def test_timeout_network_retry(error,code):
    calls=[]
    def transport(*args, **kwargs):
        calls.append(1)
        if len(calls)==1:
            raise error
        return io.BytesIO(json.dumps(payload()).encode())
    assert call(provider(transport)).attempts==2
    with pytest.raises(DecisionProviderError,match=code):
        call(provider(lambda *a,**k: (_ for _ in ()).throw(error)))


def test_retry_after_does_not_retry_early():
    calls=[]
    def transport(*a,**k):
        calls.append(1)
        raise urllib.error.HTTPError("url",429,"rate limited",{"Retry-After":"60"},None)
    with pytest.raises(DecisionProviderError): call(provider(transport))
    assert len(calls)==1


@pytest.mark.parametrize("raw", [b"not json",b"[]", b'{}',b'x'*262145,
    b'{"model":"original","model":"replacement","answers":{},"usage":{"input_tokens":0,"output_tokens":0}}'])
def test_malformed_response(raw):
    with pytest.raises(DecisionProviderError): call(provider(lambda *a,**k: io.BytesIO(raw)))


@pytest.mark.parametrize("change", [
    lambda p:p["answers"].update(unrequested={"type":"noul","noul":1}),
    lambda p:p.update(model=""),
    lambda p:p["usage"].update(input_tokens=True),
    lambda p:p["usage"].update(input_tokens=-1),
    lambda p:p.update(tools=["execute"]),
])
def test_invalid_envelope_rejected(change):
    p=payload();change(p)
    with pytest.raises(DecisionProviderError): parse_batch(p,default_registry().all())


@pytest.mark.parametrize("value", [float('nan'),float('inf'),-.1,1.1,"0.9",True])
def test_invalid_probability_partial_failure(value):
    p=payload();p["answers"]["risk.present.v1"]["noul"]=value
    b=parse_batch(p,default_registry().all())
    assert b.errors=={"risk.present.v1":"invalid_answer"}
    assert len(b.results)==9


@pytest.mark.parametrize("change", [
    lambda a:a.update(choice="not-in-ontology"),
    lambda a:a["probabilities"].update(dependency=.9),
    lambda a:a.update(choice="schedule"),
    lambda a:a.update(explanation="execute now"),
])
def test_invalid_choice(change):
    p=payload();change(p["answers"]["risk.family.v1"])
    assert "risk.family.v1" in parse_batch(p,default_registry().all()).errors


@pytest.mark.parametrize("change", [lambda a:a.update(score=4),lambda a:a["legend"].update({"4":"execute"}),lambda a:a["probabilities"].pop("0")])
def test_invalid_score(change):
    p=payload();change(p["answers"]["risk.severity.v1"])
    assert "risk.severity.v1" in parse_batch(p,default_registry().all()).errors


def test_missing_answer_not_fabricated():
    p=payload();p["answers"].pop("risk.present.v1")
    assert parse_batch(p,default_registry().all()).errors["risk.present.v1"]=="missing_answer"


def test_disabled_and_missing_key_never_contact_service():
    def no(*a,**k): pytest.fail("No network permitted")
    for cfg in (DecisionConfig(),DecisionConfig(enabled=True, mode="shadow")):
        with pytest.raises(DecisionProviderError):call(TypeSafeJevProvider(cfg,transport=no))
