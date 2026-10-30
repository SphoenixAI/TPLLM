"""Batch inference and append-before-use orchestration through injected ports."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from tpllm.decisions.models import DecisionTrace, Usage, fingerprint, utc
from tpllm.decisions.policy import DecisionPolicyGate, calibration_key
from tpllm.decisions.providers import DecisionProvider, DecisionProviderError
from tpllm.decisions.registry import DecisionRegistry
from tpllm.temporal.models import TemporalFactWindow

logger = logging.getLogger(__name__)


class DecisionTraceStore(Protocol):
    def append(self, trace: DecisionTrace) -> None: ...
    def traces(self, *, project_id=None, known_at=None, valid_at=None) -> list[DecisionTrace]: ...


class DecisionEngine:
    def __init__(self, *, providers: dict[str, DecisionProvider], registry: DecisionRegistry,
                 store: DecisionTraceStore, mode="off", policy_gate=None, clock=None):
        self.providers = providers
        self.registry = registry
        self.store = store
        self.mode = mode
        self.policy_gate = policy_gate or DecisionPolicyGate()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate(self, *, provider: str, model: str, state, definitions=None,
                 profiles=None, baseline=None, kill_switch=False, force=False) -> list[DecisionTrace]:
        if self.mode == "off":
            return []
        questions = tuple(self.registry.get(k) for k in definitions) if definitions else self.registry.all()
        if not questions or len({q.definition_id for q in questions}) != len(questions):
            raise ValueError("Provide distinct registered definitions")
        if any(q.status != "active" for q in questions):
            raise ValueError("Retired definition cannot produce a new judgment")
        # Snapshot inputs before crossing the provider port: a mutable adapter
        # cannot change the registry, state hash, policy or persisted semantics.
        state = type(state).model_validate_json(state.model_dump_json())
        old = self.store.traces(project_id=state.project_id)
        at = utc(self.clock())
        policy_hash = fingerprint(self.policy_gate.policy.model_dump(mode="json"))
        matches = [t for t in old if t.entity_id == state.entity_id and t.state_hash == state.state_hash
            and t.provider == provider and t.model_requested == model and t.mode == self.mode
            and t.policy_evaluation.policy_hash == policy_hash]
        by_definition = {t.definition.definition_id: t for t in matches}
        cached = None
        if not force and all(q.definition_id in by_definition and by_definition[q.definition_id].definition_hash == q.definition_hash for q in questions):
            cached = [by_definition[q.definition_id] for q in questions]
            # Reconsult current calibration, evidence age and the kill switch.
            # A cached acceptance is not a perpetual permission.
            unchanged = True
            for q, t in zip(questions, cached):
                key = calibration_key(q, provider, t.model_resolved or "unresolved", state.project_id)
                current = self.policy_gate.evaluate(question=q, result=t.result, provider=provider,
                    model=t.model_resolved, state=state, mode=self.mode, at=at,
                    profile=(profiles or {}).get(key), kill_switch=kill_switch)
                unchanged = unchanged and current == t.policy_evaluation
            if unchanged:
                return cached
        start = time.monotonic()
        batch, error = None, None
        error_attempts = 0
        try:
            if cached and all(t.result for t in cached):
                from tpllm.decisions.models import ProviderBatch
                batch = ProviderBatch(model_resolved=cached[0].model_resolved,
                    results={t.definition.definition_id: t.result for t in cached}, usage=Usage(), attempts=0)
            else:
                batch = self.providers[provider].evaluate(state=state.model_copy(deep=True),
                    questions=tuple(q.model_copy(deep=True) for q in questions), model=model)
            if (set(batch.results) | set(batch.errors)) - {q.definition_id for q in questions}:
                raise DecisionProviderError("unexpected_question")
            for q in questions:
                if q.definition_id in batch.results:
                    batch.results[q.definition_id].validate_for(q)
        except DecisionProviderError as exc:
            error = exc.code
            error_attempts = exc.attempts
            batch = None
        except Exception:
            # Never persist exception text: it can contain headers or payloads.
            error, batch = "provider_failure", None
            error_attempts = 1
        latency = (time.monotonic() - start) * 1000
        batch_id, traces = str(uuid4()), []
        for q in questions:
            key = q.definition_id
            result = batch.results.get(key) if batch else None
            resolved = batch.model_resolved if batch else None
            profile_key = calibration_key(q, provider, resolved or "unresolved", state.project_id)
            evaluation = self.policy_gate.evaluate(question=q, result=result, provider=provider, model=resolved,
                state=state, mode=self.mode, at=at, profile=(profiles or {}).get(profile_key), kill_switch=kill_switch)
            previous = next((t for t in reversed(old) if t.entity_id == state.entity_id
                and t.definition.definition_id == key and t.provider == provider), None)
            base = (baseline or {}).get(q.decision_id)
            disagrees = None
            if base is not None and result:
                if q.question_type == "noul" and isinstance(base, bool):
                    # Evaluation boundary only, never a policy/authority threshold.
                    disagrees = (result.probability >= .5) != base
                elif q.question_type == "choice":
                    disagrees = result.selected_value != base
                elif q.question_type == "score":
                    disagrees = abs(result.selected_value - base) >= 1
            trace = DecisionTrace(batch_id=batch_id, project_id=state.project_id, entity_id=state.entity_id,
                definition=q, definition_hash=q.definition_hash, provider=provider, model_requested=model,
                model_resolved=resolved, mode=self.mode, state_hash=state.state_hash,
                state_schema_version=state.schema_version, evidence_refs=state.evidence_refs,
                observation_refs=state.observation_refs, ontology_refs=state.ontology_refs,
                evidence_cutoff=state.known_at, temporal=TemporalFactWindow(valid_from=state.valid_at, known_from=at),
                created_at=at, inference_at=by_definition[key].inference_at if batch and batch.attempts == 0 else at,
                reused_from=by_definition[key].id if batch and batch.attempts == 0 else None,
                latency_ms=latency, usage=batch.usage if batch else Usage(), question_count=len(questions),
                result=result, error=error or (batch.errors.get(key, "missing_answer") if result is None else None),
                inference_performed=not (batch and batch.attempts == 0),
                attempts=batch.attempts if batch else error_attempts,
                policy_evaluation=evaluation, baseline_value=base, disagreement=disagrees,
                policy_parameters=self.policy_gate.policy.model_dump(mode="json"), calibration_profile=(profiles or {}).get(profile_key),
                supersedes=previous.id if previous else None)
            # A failed append prevents every advisory effect. Caller may fall
            # back to baseline; it may not use an unjournalled result.
            self.store.append(trace)
            traces.append(trace)
        logger.info("bounded decisions evaluated", extra={"project_id": state.project_id,
            "duration_ms": latency, "mode": self.mode, "failure_count": sum(t.result is None for t in traces),
            "mismatch_count": sum(t.disagreement is True for t in traces), "artifact_count": len(traces)})
        return traces
