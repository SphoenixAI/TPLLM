"""Use the existing bounded state builder; retain a richer, traceable view."""
from __future__ import annotations

from tpllm.decisions.models import canonical, fingerprint, utc
from tpllm.decisions.state import DecisionStateBuilder, _date
from tpllm.integrations.envelopes import redact_secret_text
from tpllm.intelligence_tasks.models import CohortAssignment, EvidenceExcerpt, EvidencePacket


def assign_cohort(*, ontology_cohort=None, manual_cohort=None, entity_type=None, state=None):
    # These arguments are trusted application metadata, never connector prose.
    if manual_cohort:
        return CohortAssignment(cohort_id=manual_cohort, assignment_method="manual", basis="Explicit evaluation selection")
    if ontology_cohort:
        return CohortAssignment(cohort_id=ontology_cohort, assignment_method="ontology", basis="Trusted ontology mapping")
    types = {"deployment": "software.deployments", "incident": "software.reliability",
             "dependency": "project.dependencies", "milestone": "project.schedule", "security_event": "security.events",
             "capacity": "team.capacity", "cost": "cost.variance"}
    if entity_type in types:
        return CohortAssignment(cohort_id=types[entity_type], assignment_method="entity_type", basis=entity_type)
    if state and any(f.attributes.get("deployment_status") is not None for f in state.facts):
        return CohortAssignment(cohort_id="software.deployments", assignment_method="deterministic_rule", basis="deployment_status field present")
    return CohortAssignment(cohort_id="general.operations", assignment_method="fallback", basis="No trusted specific cohort")


def _within(value, valid_at, known_at):
    try:
        return _date(value["valid_at"]) <= utc(valid_at) and _date(value["known_at"]) <= utc(known_at)
    except (KeyError, ValueError, TypeError):
        return False


class EvidencePacketBuilder:
    def build(self, *, project_id, entity_id, valid_at, known_at, records=(), observations=(),
              kpi=None, relationships=(), prior_events=(), insight=None, include_excerpts=False,
              ontology_cohort=None, manual_cohort=None, entity_type=None):
        limitations = []
        if kpi and not _within(kpi, valid_at, known_at):
            limitations.append("Unstamped or future KPI projection omitted; source observations retained.")
            observations = [o for o in observations if o.get("kpi_id") == kpi["id"]]
            kpi = None
        allowed_edges = [e for e in relationships if _within(e, valid_at, known_at)]
        if len(allowed_edges) != len(relationships):
            limitations.append("Relationships without temporal provenance omitted.")
        # Reuse Phase 1 selection, redaction, field allowlist, and hash semantics.
        state = DecisionStateBuilder(max_facts=20).build(project_id=project_id, entity_id=entity_id,
            valid_at=valid_at, known_at=known_at, records=records, observations=observations,
            kpi=kpi, relationships=allowed_edges, prior_events=prior_events)
        # Prior events also need source grounding; Phase 1 did not enforce this.
        facts = tuple(f for f in state.facts if set(f.evidence_refs) <= set(state.evidence_refs))
        state = state.model_copy(update={"facts": facts})
        excerpts = []
        if include_excerpts:
            selected = {f.id for f in facts if f.kind not in {"dependency", "kpi_trend", "project_definition", "kpi_observation"}}
            for r in sorted(records, key=lambda r: r.get("key", "")):
                if r.get("key") in selected and r.get("evidence_id") in state.evidence_refs:
                    excerpts.append(EvidenceExcerpt(evidence_ref=r["evidence_id"], text=(redact_secret_text(r.get("text", ""))[0] or "")[:1000]))
        else:
            limitations.append("Source excerpts disabled by data-minimization setting.")
        return EvidencePacket(state=state, cohort=assign_cohort(ontology_cohort=ontology_cohort,
            manual_cohort=manual_cohort, entity_type=entity_type, state=state), excerpts=tuple(excerpts),
            entity_refs=tuple(sorted({entity_id, *[f.id for f in facts if f.kind != "dependency"]})),
            kpi_refs=tuple(sorted({f.attributes["kpi_id"] for f in facts if f.kind == "kpi_observation" and f.attributes.get("kpi_id")} | ({kpi["id"]} if kpi else set()))),
            trend_refs=tuple(f.id for f in facts if f.kind == "kpi_trend"),
            dependency_refs=tuple(f.id for f in facts if f.kind == "dependency"),
            task_refs=tuple(f.id for f in facts if f.kind in {"issue", "task"}), insight=insight,
            limitations=tuple(limitations))


class ReasoningContextBuilder:
    def build(self, packet):
        packet = EvidencePacket.model_validate_json(packet.model_dump_json())
        context = {"evidence_packet_id": packet.id, "state": packet.state.model_dump(mode="json"),
            "untrusted_evidence_excerpts": [e.model_dump() for e in packet.excerpts],
            "allowed_entity_refs": packet.entity_refs, "allowed_ontology_refs": packet.state.ontology_refs,
            "allowed_task_refs": packet.task_refs, "insight": packet.insight.model_dump(mode="json") if packet.insight else None,
            "limitations": packet.limitations}
        content = canonical(context)
        if len(content.encode()) > 64000:
            raise ValueError("Reasoning context too large")
        return content, fingerprint(context)
