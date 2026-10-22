"""Lightweight event-driven intelligence runtime.

Pipeline: incoming event → normalize → idempotency check → evidence
reference → determine affected scope → refresh only the affected
intelligence → detect material change → surface payload only if
warranted.

Serverless-compatible by construction: one function call takes events
in, loads state through a store protocol, updates the affected
intelligence via duck-typed services, and returns. No always-on
process, no heavy backend, no full-graph recomputation per Slack
message.

Interpretation guardrails live here too: a chat opinion is not a
decision; a meeting transcript is evidence, not automatic truth; ticket
completion never directly changes launch readiness; merged code is
technical evidence, not feature completion.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from tpllm.integrations.envelopes import EvidenceEnvelope
from tpllm.integrations.retention import build_artifact_pointer


class IntelligenceUpdateType(str, Enum):
    NO_MATERIAL_CHANGE = "no_material_change"
    UNDERSTANDING_CHANGED = "understanding_changed"
    WORKSTREAM_CHANGED = "workstream_changed"
    KPI_CHANGED = "kpi_changed"
    DECISION_CHANGED = "decision_changed"
    ACTION_REQUIRED = "action_required"
    LAUNCH_READINESS_CHANGED = "launch_readiness_changed"


class IntelligenceUpdate(BaseModel):
    project_id: str
    update_types: list[IntelligenceUpdateType] = Field(default_factory=list)
    material: bool
    evidence_envelope_ids: list[str] = Field(default_factory=list)
    changed_object_ids: list[str] = Field(default_factory=list)
    summary: str


class RefreshScope(str, Enum):
    NONE = "none"
    EVIDENCE_ONLY = "evidence_only"
    PROJECT_UNDERSTANDING = "project_understanding"
    WORKSTREAM = "workstream"
    KPI = "kpi"
    LAUNCH_READINESS = "launch_readiness"
    COORDINATION = "coordination"


# Event-type → refresh-scope policy. Interpretation guardrails:
# chat is evidence (an opinion is not a decision); tickets touch the
# workstream but never launch readiness directly; merged code is
# technical evidence only.
_EVENT_SCOPE_MAP: dict[str, set[RefreshScope]] = {
    "chat_message": {RefreshScope.EVIDENCE_ONLY},
    "message": {RefreshScope.EVIDENCE_ONLY},
    "meeting_transcript": {
        RefreshScope.EVIDENCE_ONLY,
        RefreshScope.PROJECT_UNDERSTANDING,
        RefreshScope.KPI,
    },
    "decision_recorded": {
        RefreshScope.WORKSTREAM,
        RefreshScope.COORDINATION,
        RefreshScope.LAUNCH_READINESS,
    },
    "architecture_decision": {
        RefreshScope.PROJECT_UNDERSTANDING,
        RefreshScope.WORKSTREAM,
        RefreshScope.LAUNCH_READINESS,
    },
    "kpi_observation": {RefreshScope.KPI, RefreshScope.LAUNCH_READINESS},
    "metric_observation": {RefreshScope.KPI, RefreshScope.LAUNCH_READINESS},
    "ticket_updated": {RefreshScope.EVIDENCE_ONLY, RefreshScope.WORKSTREAM},
    "ticket_completed": {RefreshScope.EVIDENCE_ONLY, RefreshScope.WORKSTREAM},
    "pr_merged": {RefreshScope.EVIDENCE_ONLY},
    "deployment": {RefreshScope.EVIDENCE_ONLY},
    "document_updated": {
        RefreshScope.EVIDENCE_ONLY,
        RefreshScope.PROJECT_UNDERSTANDING,
    },
}


def determine_refresh_scope(envelope: EvidenceEnvelope) -> set[RefreshScope]:
    return set(
        _EVENT_SCOPE_MAP.get(envelope.event_type, {RefreshScope.EVIDENCE_ONLY})
    )


_SCOPE_UPDATE_TYPE = {
    RefreshScope.PROJECT_UNDERSTANDING: (
        IntelligenceUpdateType.UNDERSTANDING_CHANGED
    ),
    RefreshScope.WORKSTREAM: IntelligenceUpdateType.WORKSTREAM_CHANGED,
    RefreshScope.KPI: IntelligenceUpdateType.KPI_CHANGED,
    RefreshScope.COORDINATION: IntelligenceUpdateType.DECISION_CHANGED,
    RefreshScope.LAUNCH_READINESS: (
        IntelligenceUpdateType.LAUNCH_READINESS_CHANGED
    ),
}

_SCOPE_SERVICE_METHOD = {
    RefreshScope.PROJECT_UNDERSTANDING: "refresh_understanding",
    RefreshScope.WORKSTREAM: "refresh_workstreams",
    RefreshScope.KPI: "refresh_kpi",
    RefreshScope.COORDINATION: "refresh_coordination",
    RefreshScope.LAUNCH_READINESS: "refresh_launch_readiness",
}

_MATERIAL_SCOPES = {
    RefreshScope.WORKSTREAM,
    RefreshScope.KPI,
    RefreshScope.COORDINATION,
    RefreshScope.LAUNCH_READINESS,
}


class InMemoryStateStore:
    """Reference ProjectStateStore for tests and single-process use."""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def get(self, key: str) -> object | None:
        return self._data.get(key)

    def put(self, key: str, value: object) -> None:
        self._data[key] = value

    def list(self, prefix: str) -> list[str]:
        return sorted(k for k in self._data if k.startswith(prefix))


def process_evidence_envelopes(
    *,
    envelopes: list[EvidenceEnvelope],
    project_state_store: object,
    intelligence_services: object,
    as_of: datetime,
) -> list[IntelligenceUpdate]:
    """One pass, event-driven: idempotent, scope-limited, and silent on
    duplicates. Only the affected intelligence refreshes — never the
    whole company graph per message."""
    updates: list[IntelligenceUpdate] = []

    for envelope in envelopes:
        project_id = envelope.project_ids[0] if envelope.project_ids else "unknown"

        idempotency_state_key = f"idempotency:{envelope.idempotency_key}"
        if project_state_store.get(idempotency_state_key) is not None:
            updates.append(
                IntelligenceUpdate(
                    project_id=project_id,
                    update_types=[IntelligenceUpdateType.NO_MATERIAL_CHANGE],
                    material=False,
                    evidence_envelope_ids=[envelope.id],
                    summary=(
                        "Duplicate delivery — evidence already recorded; "
                        "no reprocessing."
                    ),
                )
            )
            continue
        project_state_store.put(idempotency_state_key, envelope.id)

        # Persist the reference/pointer, not a copy of the source system.
        pointer = build_artifact_pointer(envelope)
        project_state_store.put(
            f"evidence:{envelope.id}", pointer.model_dump()
        )

        scopes = determine_refresh_scope(envelope)
        changed_object_ids: list[str] = []
        update_types: list[IntelligenceUpdateType] = []

        for scope in sorted(scopes, key=lambda s: s.value):
            method_name = _SCOPE_SERVICE_METHOD.get(scope)
            if method_name is None:
                continue
            method = getattr(intelligence_services, method_name, None)
            if method is None:
                continue
            result = method(project_id=project_id, envelope=envelope)
            if result:
                for object_id in result:
                    if object_id not in changed_object_ids:
                        changed_object_ids.append(object_id)
            mapped = _SCOPE_UPDATE_TYPE.get(scope)
            if mapped is not None and mapped not in update_types:
                update_types.append(mapped)

        material = any(scope in _MATERIAL_SCOPES for scope in scopes)
        if not update_types:
            update_types = [IntelligenceUpdateType.NO_MATERIAL_CHANGE]

        updates.append(
            IntelligenceUpdate(
                project_id=project_id,
                update_types=update_types,
                material=material,
                evidence_envelope_ids=[envelope.id],
                changed_object_ids=changed_object_ids,
                summary=(
                    f"{envelope.event_type} from {envelope.source_system}: "
                    f"refreshed {', '.join(sorted(s.value for s in scopes))}."
                ),
            )
        )

    return updates
