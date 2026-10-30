"""Strict, temporally bounded selection of existing TPLLM evidence."""
from __future__ import annotations

from datetime import datetime

from tpllm.decisions.models import DecisionFact, DecisionState, utc
from tpllm.integrations.envelopes import redact_secret_text
from tpllm.temporal.models import TemporalFactWindow

# Explicit operational facts only. No authors, addresses, URLs, patches, raw
# metadata, instructions, tools, access grants or arbitrary source dictionaries.
SOURCE_FIELDS = frozenset({"state", "status", "merged", "blocked", "deployment_status",
    "delivery_state", "meeting_occurred", "synthetic", "observation_basis", "due_date",
    "missed_delivery_dates", "dependent_tasks_slipped", "retry_passed", "correlated_failures",
    "error_rate_within_baseline", "version_current", "checks_passed"})


def _date(value):
    return utc(value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00")))


def _clean(value):
    if isinstance(value, str):
        return (redact_secret_text(value)[0] or "")[:400]
    if value is None or type(value) in (float, int, bool):
        return value
    return None


class DecisionStateBuilder:
    def __init__(self, *, include_source_excerpts=False, max_facts=40):
        self.include_source_excerpts = include_source_excerpts
        self.max_facts = max_facts

    def build(self, *, project_id: str, entity_id: str, valid_at: datetime, known_at: datetime,
              records=(), observations=(), kpi=None, relationships=(), prior_events=()) -> DecisionState:
        valid_at, known_at = utc(valid_at), utc(known_at)
        facts, obs_refs, evidence = [], [], set()
        limitations = {"Source assertions are evidence, not independently verified truth."}
        selected = []
        for r in records:
            if r.get("project_id") != project_id or not r.get("evidence_id"):
                continue
            try:
                valid, known = _date(r["observed_at"]), _date(r["collected_at"])
            except (ValueError, TypeError, KeyError):
                limitations.add("Excluded source with invalid temporal provenance.")
                continue
            if valid > valid_at or known > known_at:
                continue
            selected.append((r, valid, known))
        selected.sort(key=lambda item: (item[1], item[0]["key"]), reverse=True)
        if len(selected) > self.max_facts:
            limitations.add("Source window truncated to bounded recent evidence.")
        for r, valid, known in selected[:self.max_facts]:
            attrs = {k: _clean(v) for k, v in r.get("data", {}).items() if k in SOURCE_FIELDS}
            attrs.update(source_system=_clean(r.get("provider", "unknown")), source_id=_clean(r["source_id"]),
                         source_version=_clean(r["version"]))
            if self.include_source_excerpts:
                attrs["untrusted_excerpt"] = _clean(r.get("text", ""))
            evidence.add(r["evidence_id"])
            facts.append(DecisionFact(id=r["key"], kind=r["kind"], evidence_refs=(r["evidence_id"],),
                valid_time=TemporalFactWindow(valid_from=valid, known_from=known), attributes=attrs))
        if not self.include_source_excerpts:
            limitations.add("Source prose omitted by data-minimization policy.")
        selected_observations = []
        for raw in observations:
            o = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
            if kpi and o.get("kpi_id") != kpi["id"]:
                continue
            refs = set(o.get("evidence_ids", []))
            if not refs or not refs <= evidence:
                continue
            try:
                valid, known = _date(o["observed_at"]), _date(o["reported_at"])
            except (ValueError, TypeError, KeyError):
                limitations.add("Excluded observation with invalid temporal provenance.")
                continue
            if valid > valid_at or known > known_at:
                continue
            selected_observations.append((o, valid, known, refs))
        selected_observations.sort(key=lambda item: (item[1], item[2], item[0]["id"]), reverse=True)
        if len(selected_observations) > 80:
            limitations.add("Observation window truncated to 80 recent measurements.")
        for o, valid, known, refs in selected_observations[:80]:
            obs_refs.append(o["id"])
            facts.append(DecisionFact(id=o["id"], kind="kpi_observation", evidence_refs=tuple(sorted(refs)),
                valid_time=TemporalFactWindow(valid_from=valid, known_from=known),
                attributes={k: _clean(o.get(k)) for k in ("kpi_id", "value", "unit", "confidence", "source_system", "source_id")}))
        if kpi:
            trend = kpi["trend"]
            facts.append(DecisionFact(id=kpi["id"], kind="kpi_trend", evidence_refs=tuple(sorted(evidence)),
                valid_time=TemporalFactWindow(valid_from=valid_at, known_from=known_at),
                attributes={"name": _clean(kpi["name"]),
                    **{k: _clean(trend.get(k)) for k in ("direction", "health", "latest_value")}}))
            facts.append(DecisionFact(id=kpi["id"]+":declared-target", kind="project_definition", evidence_refs=(),
                valid_time=TemporalFactWindow(valid_from=valid_at, known_from=known_at),
                attributes={"committed_target": _clean(kpi.get("committed_target")), "origin":"declared_program_success_metric"}))
            limitations.add("Declared target comes from the current program projection; its historical revision is not established here.")
        keys = {f.id for f in facts}
        selected_edges = []
        for edge in relationships:
            if edge.get("project_id") != project_id or edge.get("from") not in keys or edge.get("to") not in keys:
                continue
            if edge.get("relation") not in {"references", "depends_on", "blocks", "relates_to"}:
                continue
            selected_edges.append(edge)
        if len(selected_edges) > 40:
            limitations.add("Relationship window truncated to 40 supported edges.")
        for edge in sorted(selected_edges, key=lambda e: (e["from"], e["to"], e["relation"]))[:40]:
            facts.append(DecisionFact(id=f"edge:{edge['from']}:{edge['to']}:{edge['relation']}", kind="dependency",
                evidence_refs=tuple(sorted(evidence)), valid_time=TemporalFactWindow(valid_from=valid_at, known_from=known_at),
                attributes={k: _clean(edge[k]) for k in ("from", "to", "relation")}))
        events = [event for event in prior_events if event.project_id == project_id
            and utc(event.known_at) <= known_at and utc(event.occurred_at) <= valid_at]
        if len(events) > 20:
            limitations.add("Prior-event window truncated to 20 recent events.")
        for event in sorted(events, key=lambda e: (utc(e.occurred_at), e.id), reverse=True)[:20]:
            facts.append(DecisionFact(id=event.id, kind="prior_event", evidence_refs=tuple(event.evidence_ids),
                valid_time=TemporalFactWindow(valid_from=utc(event.occurred_at), known_from=utc(event.known_at)),
                attributes={"event_type": event.event_type.value, "source_object_id": _clean(event.source_object_id)}))
        return DecisionState(project_id=project_id, entity_id=entity_id, valid_at=valid_at, known_at=known_at,
            facts=tuple(sorted(facts, key=lambda f: (f.kind, f.id))), evidence_refs=tuple(sorted(evidence)),
            observation_refs=tuple(sorted(obs_refs)), ontology_refs=(entity_id,), limitations=tuple(sorted(limitations)))
