"""Explicit tasks and shadow comparisons; never automatic model selection."""
from datetime import datetime, timezone

from tpllm.decisions.models import utc
from tpllm.intelligence_tasks.comparison import compare_lanes
from tpllm.intelligence_tasks.models import ComparisonRecord, ExecutionMetadata, IntelligenceRecord
from tpllm.intelligence_tasks.registry import default_registry


class IntelligenceTaskService:
    def __init__(self, *, store, decision_engine=None, reasoning_engine=None, registry=None, clock=None):
        self.store, self.decision_engine, self.reasoning_engine = store, decision_engine, reasoning_engine
        self.registry = registry or default_registry()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if decision_engine and decision_engine.mode != "shadow":
            raise ValueError("Phase 2 evaluation requires a shadow decision engine")

    def run(self, *, packet, task_ids, decision_provider="typesafe", decision_model="jev-latest",
            reasoning_provider="anthropic", reasoning_model="", provenance="live", force=False,
            decision_provenance=None, reasoning_provenance=None):
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Duplicate task selection")
        tasks, results = [], []
        at = utc(self.clock())
        decision_provenance = decision_provenance or provenance
        reasoning_provenance = reasoning_provenance or provenance
        for key in task_ids:
            try:
                task = self.registry.get(key)
            except KeyError:
                self.store.coverage(packet_id=packet.id, project_id=packet.state.project_id, task_ids=(key,), reason="unsupported_task", at=at)
                continue
            tasks.append(task)
        self.store.packet(packet)
        old = self.store.records(project_id=packet.state.project_id)
        pending = []
        for task in tasks:
            provider, model = (decision_provider, decision_model) if task.lane == "decision" else (reasoning_provider, reasoning_model)
            source = decision_provenance if task.lane == "decision" else reasoning_provenance
            cached = next((r for r in reversed(old) if r.task == task and r.evidence_packet_id == packet.id
                and r.provider == provider and r.requested_model == model and r.execution.provenance == source), None)
            if cached and not force:
                results.append(cached)
            else:
                pending.append(task)
        decisions = [t for t in pending if t.lane == "decision"]
        if decisions and self.decision_engine and packet.state.evidence_refs:
            traces = self.decision_engine.evaluate(provider=decision_provider, model=decision_model, state=packet.state,
                definitions=[t.definition_id for t in decisions], force=force)
            for trace in traces:
                record = IntelligenceRecord(task=self.registry.get(trace.definition.definition_id), project_id=trace.project_id,
                    entity_id=trace.entity_id, cohort=packet.cohort, provider=trace.provider, requested_model=trace.model_requested,
                    resolved_model=trace.model_resolved, evidence_packet_id=packet.id, context_hash=trace.state_hash,
                    evidence_refs=trace.evidence_refs, created_at=trace.created_at, valid_time=packet.state.valid_at,
                    transaction_time=packet.state.known_at, execution=ExecutionMetadata(request_id=trace.batch_id,
                        latency_ms=trace.latency_ms, input_bytes=len(packet.state.model_dump_json().encode()),
                        usage=trace.usage if trace.result else None, attempts=trace.attempts, provenance=decision_provenance),
                    status="available" if trace.result else "unavailable", failure_kind="provider_failure" if trace.error else None,
                    reason=trace.error, decision=trace)
                self.store.append(record)
                results.append(record)
                if trace.disagreement is not None:
                    self.store.compare(ComparisonRecord(left_id=record.id, right_id="existing_tpllm", kind="existing_tpllm",
                        state="conflict" if trace.disagreement else "agree", method="phase1-baseline.v1",
                        explanation="Comparison with the existing deterministic TPLLM assessment.", created_at=at))
        elif decisions:
            self.store.coverage(packet_id=packet.id, project_id=packet.state.project_id,
                task_ids=[t.definition_id for t in decisions], reason="provider_disabled" if not self.decision_engine else "insufficient_state", at=at)
        for task in pending:
            if task.lane != "reasoning":
                continue
            if self.reasoning_engine:
                results.append(self.reasoning_engine.evaluate(task_id=task.definition_id, packet=packet,
                    provider=reasoning_provider, model=reasoning_model, provenance=reasoning_provenance))
            else:
                self.store.coverage(packet_id=packet.id, project_id=packet.state.project_id,
                    task_ids=(task.definition_id,), reason="provider_disabled", at=at)
        comparisons = {(c.left_id, c.right_id) for c in self.store.comparisons()}
        for decision in results:
            if decision.task.task_id != "risk.family":
                continue
            for reasoning in results:
                if reasoning.task.task_id == "reasoning.root_cause_analysis" and (decision.id, reasoning.id) not in comparisons:
                    self.store.compare(compare_lanes(decision, reasoning, at=max(at, decision.created_at, reasoning.created_at)))
        return results
