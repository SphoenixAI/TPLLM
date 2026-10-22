"""Integration registry and legacy-adapter wrapping.

Existing integrations (Slack, Jira, GitHub, calendar, …) keep their
OAuth flows and clients; WrappedToolAdapter puts them behind the
normalized ToolEvidenceAdapter protocol without duplicating anything.
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from tpllm.integrations.envelopes import EvidenceEnvelope


class IntegrationRegistry(BaseModel):
    adapters: dict[str, object] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


def register_adapter(
    *,
    registry: IntegrationRegistry,
    adapter: object,
) -> IntegrationRegistry:
    adapter_id = getattr(adapter, "adapter_id", None)
    if not adapter_id:
        raise ValueError("An adapter must expose adapter_id.")
    updated = dict(registry.adapters)
    updated[adapter_id] = adapter
    return registry.model_copy(update={"adapters": updated})


def get_adapter(
    *,
    registry: IntegrationRegistry,
    adapter_id: str,
) -> object | None:
    return registry.adapters.get(adapter_id)


def list_available_sources(*, registry: IntegrationRegistry) -> list[str]:
    sources: list[str] = []
    for adapter in registry.adapters.values():
        source = str(getattr(adapter, "source_system", "unknown"))
        if source not in sources:
            sources.append(source)
    return sorted(sources)


class WrappedToolAdapter:
    """Adapts an existing integration object to ToolEvidenceAdapter via a
    normalize function — reuse, not reimplementation."""

    def __init__(
        self,
        *,
        adapter_id: str,
        source_system: str,
        legacy_integration: object,
        normalize: Callable[[object, object], list[EvidenceEnvelope]],
    ):
        self.adapter_id = adapter_id
        self.source_system = source_system
        self.legacy_integration = legacy_integration
        self._normalize = normalize

    def normalize_event(self, event: object) -> list[EvidenceEnvelope]:
        return self._normalize(self.legacy_integration, event)
