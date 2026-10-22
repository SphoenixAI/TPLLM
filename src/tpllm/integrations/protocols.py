"""Adapter and store protocols.

Domain intelligence stays vendor-neutral: tools implement these
protocols, existing integrations are wrapped behind them, and no heavy
database is required — any persistence can implement the store
protocols.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tpllm.integrations.envelopes import (
    EvidenceEnvelope,
    envelope_to_project_evidence,
)
from tpllm.intelligence.project_understanding import ProjectEvidence


class ToolEvidenceAdapter(Protocol):
    adapter_id: str
    source_system: str

    def normalize_event(self, event: object) -> list[EvidenceEnvelope]:
        ...


class ToolQueryAdapter(Protocol):
    adapter_id: str
    source_system: str

    def search(
        self,
        *,
        query: str,
        project_id: str,
        as_of: datetime,
        max_results: int,
    ) -> list[EvidenceEnvelope]:
        ...


class ProjectStateStore(Protocol):
    def get(self, key: str) -> object | None:
        ...

    def put(self, key: str, value: object) -> None:
        ...

    def list(self, prefix: str) -> list[str]:
        ...


class EvidenceReferenceStore(Protocol):
    def get(self, evidence_id: str) -> object | None:
        ...

    def put(self, evidence_id: str, reference: object) -> None:
        ...

    def list(self, prefix: str) -> list[str]:
        ...


class ProjectionStore(Protocol):
    def get(self, projection_id: str) -> object | None:
        ...

    def put(self, projection_id: str, projection: object) -> None:
        ...

    def list(self, prefix: str) -> list[str]:
        ...


class QueryAdapterEvidenceSearchProvider:
    """Wraps a ToolQueryAdapter as an ``EvidenceSearchProvider`` so
    self-research can search the tool ecosystem directly."""

    def __init__(self, *, adapter: ToolQueryAdapter, stage):
        self.provider_id = f"query-{adapter.adapter_id}"
        self.stage = stage
        self._adapter = adapter

    def search(
        self,
        *,
        project_id: str,
        query: str,
        as_of: datetime,
        max_results: int,
    ) -> list[ProjectEvidence]:
        envelopes = self._adapter.search(
            query=query,
            project_id=project_id,
            as_of=as_of,
            max_results=max_results,
        )
        evidence: list[ProjectEvidence] = []
        for envelope in envelopes:
            if envelope.observed_at > as_of:
                continue
            item = envelope_to_project_evidence(
                envelope, project_id=project_id
            )
            if item is not None:
                evidence.append(item)
        return evidence
