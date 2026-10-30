"""Background, review-only task seam. No access to any execution capability."""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone

from tpllm.application.intelligence_evaluation import report
from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.engine import DecisionEngine
from tpllm.decisions.providers import TypeSafeJevProvider
from tpllm.decisions.registry import default_registry as decisions
from tpllm.intelligence_tasks.config import IntelligenceConfig
from tpllm.intelligence_tasks.evidence import EvidencePacketBuilder
from tpllm.intelligence_tasks.models import InsightContext
from tpllm.decisions.models import utc
from tpllm.intelligence_tasks.registry import default_registry
from tpllm.reasoning.engine import ReasoningEngine
from tpllm.reasoning.providers import ReasoningProviderError, StructuredLLMProvider
from tpllm.runtime.decision_journal import JournalDecisionStore
from tpllm.runtime.intelligence_journal import JournalIntelligenceStore
from tpllm.runtime.intelligence_service import IntelligenceTaskService

logger = logging.getLogger(__name__)


class WorkspaceJournal:
    """Hold the existing workspace lock only for storage, never inference."""
    def __init__(self, ws):
        self.ws = ws

    def __getattr__(self, name):
        if name not in {"append", "read_stream", "read_from"}:
            raise AttributeError(name)
        def call(**kwargs):
            with self.ws.lock:
                if self.ws.closed:
                    raise RuntimeError("Workspace closed")
                result = getattr(self.ws.db.journal, name)(**kwargs)
                if name == "append" and self.ws.checkpoint:
                    self.ws.checkpoint()
                return result
        return call


def settings(ws):
    return getattr(ws, "intelligence_config", None) or (IntelligenceConfig() if ws.demo else IntelligenceConfig.from_env())


def schedule(ws, projection, *, at):
    """Called after baseline projection; queues snapshots without model calls."""
    try:
        cfg = settings(ws)
        if cfg.mode == "off":
            return
        store = JournalIntelligenceStore(ws.db.journal, ws.organization)
        jobs = getattr(ws, "intelligence_jobs", None)
        if jobs is None:
            jobs = ws.intelligence_jobs = {}
        for project in sorted({r["project_id"] for r in projection["records"]}):
            records = [r for r in projection["records"] if r["project_id"] == project]
            cutoff = max(datetime.fromisoformat(r["collected_at"]) for r in records)
            cutoff = utc(cutoff)
            insight = None
            if "reasoning.insight_explanation.v1" in cfg.reasoning_tasks:
                history = JournalDecisionStore(ws.db.journal, ws.organization)
                effects = history.effects(project_id=project)
                if effects:
                    effect = effects[-1]
                    original = effect["insight"]
                    trace = history.get(effect["trace_ids"][0])
                    detected = utc(datetime.fromisoformat(original["detected_at"]))
                    if (trace and detected <= utc(at)
                            and set(trace.evidence_refs) <= {r["evidence_id"] for r in records}):
                        insight = InsightContext(id=original["id"], summary=original["summary"][:2000],
                            evidence_refs=trace.evidence_refs, valid_at=detected, known_at=detected,
                            validation_outcome=effect["validation"]["outcome"])
                        cutoff = max(cutoff, detected)
            from tpllm.product_decisions import _evidence_aliases
            aliases = _evidence_aliases(records)
            knowledge = {r["evidence_id"]: r["collected_at"] for r in records}
            observations = []
            for raw in ws.get("collected_kpi_inputs", {}).get("observations", []):
                refs = [aliases.get(e, e) for e in raw["evidence_ids"]]
                if refs and all(e in knowledge for e in refs):
                    observations.append({**raw, "evidence_ids": refs, "reported_at": max(knowledge[e] for e in refs)})
            # Product relationship edges are rederived solely from selected
            # source versions. Give this snapshot their latest knowledge time.
            keys = {r["key"] for r in records}
            edges = [{**e, "known_at": cutoff.isoformat(), "valid_at": cutoff.isoformat()}
                for e in projection["relationships"] if e["from"] in keys and e["to"] in keys and e["relation"] == "references"]
            packet = EvidencePacketBuilder().build(project_id=project, entity_id=project,
                valid_at=cutoff, known_at=cutoff, records=records, relationships=edges, observations=observations,
                include_excerpts=cfg.include_excerpts, insight=insight)
            selected, reason = cfg.selection(packet, fixture=ws.demo)
            if not selected:
                seen = getattr(ws, "intelligence_skipped", set())
                key = (packet.id, reason)
                if key not in seen:
                    store.coverage(packet_id=packet.id, project_id=project,
                        task_ids=cfg.decision_tasks+cfg.reasoning_tasks, reason=reason, at=at)
                    ws.intelligence_skipped = (seen | {key}) if len(seen) < 1000 else {key}
                continue
            if packet.id not in jobs and len(jobs) >= 8:
                store.coverage(packet_id=packet.id, project_id=project, task_ids=cfg.decision_tasks+cfg.reasoning_tasks, reason="queue_full", at=at)
                continue
            jobs[packet.id] = (packet, cfg)
    except Exception:
        logger.warning("intelligence scheduling unavailable")
        # Failure is review telemetry only; no coordination projection changes.
        try:
            store = JournalIntelligenceStore(ws.db.journal, ws.organization)
            store.coverage(packet_id="unbuilt", project_id="workspace", task_ids=("packet.build",), reason="state_builder_failure", at=at)
        except Exception:
            pass


def drain_one(ws):
    with ws.lock:
        jobs = getattr(ws, "intelligence_jobs", {})
        if ws.closed or not jobs:
            return False
        packet, cfg = jobs.pop(next(iter(jobs)))
        current = settings(ws)
        if current.mode == "off":
            return False
        if current != cfg:
            JournalIntelligenceStore(ws.db.journal, ws.organization).coverage(packet_id=packet.id,
                project_id=packet.state.project_id, task_ids=cfg.decision_tasks+cfg.reasoning_tasks,
                reason="configuration_changed", at=datetime.now(timezone.utc))
            return False
        model_cfg = copy.deepcopy(ws.cfg)
        injected_decision = getattr(ws, "intelligence_decision_provider", None)
        injected_reasoning = getattr(ws, "intelligence_reasoning_provider", None)
    journal = WorkspaceJournal(ws)
    store = JournalIntelligenceStore(journal, ws.organization)
    decision_cfg = DecisionConfig() if ws.demo else DecisionConfig.from_env()
    dprovider = injected_decision or (TypeSafeJevProvider(decision_cfg) if decision_cfg.active else None)
    rprovider = injected_reasoning
    if rprovider is None and not ws.demo:
        try:
            rprovider = StructuredLLMProvider.from_config(model_cfg, cfg)
        except ReasoningProviderError:
            pass
    dname = "mock" if injected_decision else "typesafe"
    rname = "mock" if injected_reasoning else model_cfg.provider
    dengine = DecisionEngine(providers={dname: dprovider}, registry=decisions(),
        store=JournalDecisionStore(journal, ws.organization), mode="shadow") if dprovider else None
    rengine = ReasoningEngine(providers={rname: rprovider}, registry=default_registry(), store=store) if rprovider else None
    try:
        IntelligenceTaskService(store=store, decision_engine=dengine, reasoning_engine=rengine).run(
            packet=packet, task_ids=cfg.decision_tasks+cfg.reasoning_tasks,
            decision_provider=dname, decision_model=decision_cfg.model,
            reasoning_provider=rname, reasoning_model=model_cfg.model,
            provenance="mock" if ws.demo else "live",
            decision_provenance="mock" if injected_decision or ws.demo else "live",
            reasoning_provenance="mock" if injected_reasoning or ws.demo else "live")
    except Exception:
        logger.warning("intelligence background evaluation unavailable")
        try:
            store.coverage(packet_id=packet.id, project_id=packet.state.project_id,
                task_ids=cfg.decision_tasks+cfg.reasoning_tasks, reason="evaluation_failure", at=datetime.now(timezone.utc))
        except Exception:
            pass
    return True


def snapshot(ws, *, project_id=None):
    store = JournalIntelligenceStore(ws.db.journal, ws.organization)
    records = store.records(project_id=project_id)
    ids = {r.id for r in records}
    # Reports must honor the same project scope as their source records.
    class ScopedStore:
        def records(self, **kwargs): return records
        def reviews(self, **kwargs): return [r for r in store.reviews(**kwargs) if r.record_id in ids]
        def outcomes(self, **kwargs): return [r for r in store.outcomes(**kwargs) if r.record_id in ids]
        def comparisons(self, **kwargs): return [r for r in store.comparisons(**kwargs) if r.left_id in ids]
        def coverage_events(self): return [e for e in store.coverage_events() if not project_id or e["project_id"] == project_id]
    try:
        mode = settings(ws).mode
    except ValueError:
        mode = "unavailable"
    return {"mode": mode, "catalog": [t.model_dump(mode="json") for t in default_registry().all()],
        "records": [r.model_dump(mode="json") for r in records[-200:]],
        "comparisons": [c.model_dump(mode="json") for c in ScopedStore().comparisons()][-200:],
        "reviews": [r.model_dump(mode="json") for r in ScopedStore().reviews()][-200:],
        "outcomes": [r.model_dump(mode="json") for r in ScopedStore().outcomes()][-200:],
        "evaluation": report(ScopedStore()), "queue_depth": len(getattr(ws, "intelligence_jobs", {})),
        "authority": "Jev judges. Reasoning models synthesize. TPLLM governs."}
