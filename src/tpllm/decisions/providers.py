"""Inference-only providers. No tool registry, authority, or action dependency."""
from __future__ import annotations

import json
import socket
import re
import time
import urllib.error
import urllib.request
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.models import DecisionQuestion, DecisionResult, DecisionState, ProviderBatch, Usage


class DecisionProvider(Protocol):
    def evaluate(self, *, state: DecisionState, questions: tuple[DecisionQuestion, ...], model: str) -> ProviderBatch: ...


class DecisionProviderError(RuntimeError):
    """Only bounded error codes may cross the provider boundary."""
    def __init__(self, code: str, attempts: int = 0):
        super().__init__(code)
        self.code = code
        self.attempts = attempts


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:/-]+$")
    answers: dict[str, dict]
    usage: dict[str, int]


def parse_answer(raw: dict, question: DecisionQuestion) -> DecisionResult:
    kind = question.question_type
    fields = {"type", "noul"} if kind == "noul" else ({"type", "choice", "confidence", "probabilities"} if kind == "choice"
              else {"type", "score", "confidence", "probabilities", "legend"})
    if set(raw) != fields or raw.get("type") != kind:
        raise ValueError("schema_mismatch")
    numbers = ([raw["noul"]] if kind == "noul" else [raw["confidence"], *raw["probabilities"].values()])
    if kind == "score":
        numbers.append(raw["score"])
    if any(type(v) not in (int, float) for v in numbers):
        raise ValueError("non_numeric_probability")
    result = DecisionResult(question_type=kind, selected_value=raw[kind],
        probability=raw.get("noul"), confidence=raw.get("confidence"),
        probability_distribution=raw.get("probabilities", {}), legend=raw.get("legend", {}), raw_result=raw)
    result.validate_for(question)
    return result


def parse_batch(raw: object, questions: tuple[DecisionQuestion, ...], *, attempts=1) -> ProviderBatch:
    try:
        envelope = _Envelope.model_validate(raw)
        if set(envelope.usage) != {"input_tokens", "output_tokens"}:
            raise ValueError("usage schema")
        usage = Usage(input_count=envelope.usage["input_tokens"], output_count=envelope.usage["output_tokens"])
        expected = {q.definition_id for q in questions}
        if set(envelope.answers) - expected:
            raise ValueError("unexpected question")
    except (ValidationError, TypeError, ValueError):
        raise DecisionProviderError("schema_mismatch") from None
    results, errors = {}, {}
    for question in questions:
        key = question.definition_id
        if key not in envelope.answers:
            errors[key] = "missing_answer"
            continue
        try:
            results[key] = parse_answer(envelope.answers[key], question)
        except (ValueError, TypeError, KeyError, AttributeError):
            errors[key] = "invalid_answer"
    return ProviderBatch(model_resolved=envelope.model, results=results, errors=errors, usage=usage, attempts=attempts)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the API credential to a redirect target.


class TypeSafeJevProvider:
    endpoint = "https://api.typesafe.ai/v1/systemone"

    def __init__(self, config: DecisionConfig, *, transport=None, sleep=time.sleep):
        self.config = config
        self._transport = transport or urllib.request.build_opener(_NoRedirect()).open
        self._sleep = sleep

    def evaluate(self, *, state, questions, model):
        if not self.config.active:
            raise DecisionProviderError("disabled")
        key = self.config.api_key.get_secret_value()
        if not key:
            raise DecisionProviderError("invalid_credentials")
        if not questions or len({q.definition_id for q in questions}) != len(questions):
            raise DecisionProviderError("invalid_questions")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model):
            raise DecisionProviderError("invalid_model")
        body = {"model": model, "state": state.model_dump(mode="json"), "questions": {}}
        for q in questions:
            wire = {"type": q.question_type, "instructions": q.instructions}
            if q.criteria is not None:
                wire["criteria"] = q.criteria
            body["questions"][q.definition_id] = wire
        encoded = json.dumps(body, allow_nan=False).encode()
        if len(encoded) > 48000:
            raise DecisionProviderError("state_too_large")
        request = urllib.request.Request(self.endpoint, data=encoded,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
        code = "network_failure"
        for attempt in range(1, self.config.max_attempts + 1):
            delay = min(2, .25 * 2 ** (attempt - 1))
            try:
                with self._transport(request, timeout=self.config.timeout_seconds) as response:
                    data = response.read(262145)
                if len(data) > 262144:
                    raise DecisionProviderError("response_too_large")
                try:
                    def unique_object(pairs):
                        result = {}
                        for name, value in pairs:
                            if name in result:
                                raise ValueError("duplicate field")
                            result[name] = value
                        return result
                    raw = json.loads(data, object_pairs_hook=unique_object)
                except (ValueError, UnicodeError):
                    raise DecisionProviderError("malformed_response") from None
                return parse_batch(raw, questions, attempts=attempt)
            except DecisionProviderError as exc:
                raise DecisionProviderError(exc.code, attempt) from None
            except urllib.error.HTTPError as exc:
                code = {401: "invalid_credentials", 403: "forbidden", 404: "model_unavailable", 429: "rate_limited"}.get(exc.code, "api_error")
                if exc.code not in {408, 429, 500, 502, 503, 504, 529}:
                    raise DecisionProviderError(code, attempt) from None
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                if retry_after.isdigit():
                    if int(retry_after) > 2:
                        raise DecisionProviderError(code, attempt) from None  # do not retry earlier than requested
                    delay = max(delay, int(retry_after))
            except (TimeoutError, socket.timeout):
                code = "timeout"
            except urllib.error.URLError as exc:
                code = "timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "network_failure"
            except OSError:
                code = "network_failure"
            if attempt < self.config.max_attempts:
                self._sleep(delay)
        raise DecisionProviderError(code, self.config.max_attempts)


class MockDecisionProvider:
    """Explicit fixture answers; never synthesize missing evidence or answers."""
    def __init__(self, batch: ProviderBatch):
        self.batch = batch
        self.calls = 0

    def evaluate(self, *, state, questions, model):
        self.calls += 1
        return self.batch.model_copy(deep=True)
