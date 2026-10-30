"""Locally validated synthesis. Does not publish insights or invoke actions."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from tpllm.decisions.models import utc
from tpllm.integrations.envelopes import redact_secret_text
from tpllm.intelligence_tasks.evidence import ReasoningContextBuilder
from tpllm.intelligence_tasks.models import EvidencePacket, ExecutionMetadata, IntelligenceRecord, ReasoningTrace
from tpllm.intelligence_tasks.schemas import SCHEMAS
from tpllm.reasoning.providers import ReasoningProviderError


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON field")
        value[key] = item
    return value


def validate_references(output, packet):
    sets = {"supporting_evidence_refs": packet.state.evidence_refs, "contradictory_evidence_refs": packet.state.evidence_refs,
        "evidence_refs": packet.state.evidence_refs, "entity_refs": packet.entity_refs, "affected_entities": packet.entity_refs,
        "ontology_refs": packet.state.ontology_refs, "task_refs": packet.task_refs,
        "insight_ref": (packet.insight.id,) if packet.insight else ()}
    flags, count, unsupported = [], 0, 0
    def visit(value):
        nonlocal count, unsupported
        if isinstance(value, dict):
            for key, items in value.items():
                if key in sets:
                    refs = [items] if isinstance(items, str) else items
                    count += len(refs)
                    bad = sum(ref not in sets[key] for ref in refs)
                    unsupported += bad
                    if bad:
                        flags.append("unknown_"+key)
                else:
                    visit(items)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(output)
    if output["completion"] == "complete" and not output["supporting_evidence_refs"]:
        flags.append("missing_factual_support")
    return tuple(sorted(set(flags))), count, unsupported


class ReasoningEngine:
    def __init__(self, *, providers, registry, store, clock=None):
        self.providers, self.registry, self.store = providers, registry, store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate(self, *, task_id, packet, provider, model, provenance="live"):
        task = self.registry.get(task_id)
        if task.lane != "reasoning" or task_id not in SCHEMAS:
            raise ValueError("Unsupported reasoning schema")
        if task.output_schema != SCHEMAS[task_id].model_json_schema():
            raise ValueError("Schema no longer matches registered local validator")
        packet = EvidencePacket.model_validate_json(packet.model_dump_json())
        context, context_hash = ReasoningContextBuilder().build(packet)
        at, start = utc(self.clock()), time.monotonic()
        output = None
        schema_valid = refs_valid = None
        response = None
        failure = reason = None
        flags, count, unsupported, attempts = (), 0, 0, 0
        status = "available"
        if not packet.state.evidence_refs or ("insight" in task.required_context and packet.insight is None):
            status, reason = "skipped", "insufficient_state"
        elif provider not in self.providers:
            status, reason = "skipped", "provider_disabled"
        else:
            try:
                attempts = 1
                response = self.providers[provider].synthesize(task=task.model_copy(deep=True), context=context, model=model)
                attempts = response.attempts
                if len(response.content.encode()) > 64000:
                    raise ValueError("Oversized output")
                raw = json.loads(response.content, object_pairs_hook=unique_object,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
                output = SCHEMAS[task_id].model_validate(raw).model_dump(mode="json")
                schema_valid = True
                # Never persist echoed credentials, even in schema-valid prose.
                if redact_secret_text(json.dumps(output))[0] != json.dumps(output):
                    output = None
                    flags, refs_valid = ("sensitive_output_rejected",), False
                else:
                    flags, count, unsupported = validate_references(output, packet)
                    refs_valid = not flags
                if not refs_valid:
                    status, failure, reason = "rejected", "evidence_validation_failure", "ungrounded_output"
            except ReasoningProviderError as exc:
                status, failure = "unavailable", "provider_failure"
                safe = {"timeout", "rate_limited", "provider_disabled", "invalid_credentials", "forbidden",
                        "unsupported_schema_or_model", "model_unavailable", "refused_or_incomplete", "malformed_response", "invalid_model", "state_too_large"}
                reason, attempts = exc.code if exc.code in safe else "provider_failure", exc.attempts
            except (ValidationError, ValueError, TypeError):
                status, failure, reason, schema_valid, output = "rejected", "schema_failure", "invalid_structured_output", False, None
            except Exception:
                status, failure, reason = "unavailable", "provider_failure", "provider_failure"
        trace = ReasoningTrace(schema_id=task.definition_id, schema_version=task.schema_version,
            structured_output=output, schema_valid=schema_valid, evidence_refs_valid=refs_valid,
            unsupported_claim_flags=flags, reference_count=count, unsupported_reference_count=unsupported,
            provider_metadata={"response_id": response.response_id} if response else {})
        record = IntelligenceRecord(task=task, project_id=packet.state.project_id, entity_id=packet.state.entity_id,
            cohort=packet.cohort, provider=provider, requested_model=model, resolved_model=response.resolved_model if response else None,
            evidence_packet_id=packet.id, context_hash=context_hash, evidence_refs=packet.state.evidence_refs,
            created_at=at, valid_time=packet.state.valid_at, transaction_time=packet.state.known_at,
            execution=ExecutionMetadata(request_id=str(uuid4()), latency_ms=(time.monotonic()-start)*1000,
                input_bytes=len(context.encode()), usage=response.usage if response else None, attempts=attempts, provenance=provenance),
            status=status, failure_kind=failure, reason=reason, reasoning=trace)
        self.store.append(record)
        return record
