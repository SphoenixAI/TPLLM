"""Tool-native surfaces.

Vendor-neutral payloads rendered into whatever tool the team already
uses (Slack, Teams, Jira, Linear, Notion, email, dashboard) via
renderer protocols. Bring the intelligence to the work.

Nothing is surfaced automatically for every ingestion event — only
material changes, decision requests, significant KPI or readiness
changes, and contradictions requiring action.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from lltpm.integrations.runtime import (
    IntelligenceUpdate,
    IntelligenceUpdateType,
)


class SurfacePayloadType(str, Enum):
    WORKSTREAM_UPDATE = "workstream_update"
    DECISION_CAPSULE = "decision_capsule"
    ACTION_REQUEST = "action_request"
    KPI_ALERT = "kpi_alert"
    MEETING_AGENDA = "meeting_agenda"
    EXECUTIVE_BRIEF = "executive_brief"
    CONTEXT_BRIEF = "context_brief"


class ToolSurfacePayload(BaseModel):
    payload_type: SurfacePayloadType
    project_id: str
    title: str
    summary: str
    sections: list[dict[str, Any]] = Field(default_factory=list)
    action_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    urgency: str


class ToolSurfaceRenderer(Protocol):
    renderer_id: str
    target_system: str

    def render(self, payload: ToolSurfacePayload) -> object:
        ...


_SURFACE_WORTHY_TYPES = {
    IntelligenceUpdateType.DECISION_CHANGED,
    IntelligenceUpdateType.ACTION_REQUIRED,
    IntelligenceUpdateType.KPI_CHANGED,
    IntelligenceUpdateType.LAUNCH_READINESS_CHANGED,
}


def should_surface_update(update: IntelligenceUpdate) -> bool:
    """Surface only material change worth attention — never every
    ingestion event."""
    if not update.material:
        return False
    return any(t in _SURFACE_WORTHY_TYPES for t in update.update_types)


_UPDATE_PAYLOAD_TYPE = {
    IntelligenceUpdateType.DECISION_CHANGED: SurfacePayloadType.DECISION_CAPSULE,
    IntelligenceUpdateType.ACTION_REQUIRED: SurfacePayloadType.ACTION_REQUEST,
    IntelligenceUpdateType.KPI_CHANGED: SurfacePayloadType.KPI_ALERT,
    IntelligenceUpdateType.LAUNCH_READINESS_CHANGED: (
        SurfacePayloadType.WORKSTREAM_UPDATE
    ),
    IntelligenceUpdateType.WORKSTREAM_CHANGED: (
        SurfacePayloadType.WORKSTREAM_UPDATE
    ),
}


def build_surface_payload_for_update(
    *,
    update: IntelligenceUpdate,
    title: str,
    urgency: str = "normal",
    sections: list[dict[str, Any]] | None = None,
) -> ToolSurfacePayload:
    payload_type = SurfacePayloadType.WORKSTREAM_UPDATE
    for update_type in update.update_types:
        mapped = _UPDATE_PAYLOAD_TYPE.get(update_type)
        if mapped is not None:
            payload_type = mapped
            break
    return ToolSurfacePayload(
        payload_type=payload_type,
        project_id=update.project_id,
        title=title,
        summary=update.summary,
        sections=list(sections or []),
        action_refs=list(update.changed_object_ids),
        evidence_refs=list(update.evidence_envelope_ids),
        urgency=urgency,
    )
