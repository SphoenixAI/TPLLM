"""Tool-native surfaces and the no-noise policy."""

from __future__ import annotations

from datetime import datetime

from tpllm.integrations import (
    IntelligenceUpdate,
    IntelligenceUpdateType,
    QueryAdapterEvidenceSearchProvider,
    SurfacePayloadType,
    ToolSurfacePayload,
    build_surface_payload_for_update,
    make_envelope,
    should_surface_update,
)
from tpllm.intelligence import ResearchStage

_OBSERVED = datetime(2026, 8, 10, 9, 30, 0)
_PROJECT = "atlas"


def _update(update_types, material=True):
    return IntelligenceUpdate(
        project_id=_PROJECT,
        update_types=update_types,
        material=material,
        evidence_envelope_ids=["env-1"],
        changed_object_ids=["dec-1"],
        summary="Contract v3 decision recorded.",
    )


class TestSurfacePolicy:
    def test_material_decision_triggers_surface(self):
        update = _update([IntelligenceUpdateType.DECISION_CHANGED])
        assert should_surface_update(update) is True
        payload = build_surface_payload_for_update(
            update=update, title="Billing contract v3 decided",
            urgency="high",
        )
        assert payload.payload_type == SurfacePayloadType.DECISION_CAPSULE
        assert payload.evidence_refs == ["env-1"]
        assert payload.action_refs == ["dec-1"]

    def test_low_value_event_stays_silent(self):
        chat = _update(
            [IntelligenceUpdateType.NO_MATERIAL_CHANGE], material=False,
        )
        assert should_surface_update(chat) is False
        # Even a "changed" understanding without materiality stays quiet.
        quiet = _update(
            [IntelligenceUpdateType.UNDERSTANDING_CHANGED], material=False,
        )
        assert should_surface_update(quiet) is False

    def test_understanding_change_alone_not_surfaced(self):
        update = _update([IntelligenceUpdateType.UNDERSTANDING_CHANGED])
        assert should_surface_update(update) is False


class TestVendorNeutrality:
    def test_payload_has_no_vendor_fields(self):
        field_names = set(ToolSurfacePayload.model_fields)
        for vendor in ("slack", "jira", "teams", "notion", "channel",
                       "webhook"):
            assert not any(vendor in name for name in field_names)

    def test_renderer_protocol_isolates_vendor(self):
        class SlackRenderer:
            renderer_id = "slack-blocks"
            target_system = "slack"

            def render(self, payload):
                return {
                    "blocks": [
                        {"type": "header", "text": payload.title},
                        {"type": "section", "text": payload.summary},
                    ]
                }

        payload = build_surface_payload_for_update(
            update=_update([IntelligenceUpdateType.KPI_CHANGED]),
            title="Activation below beta gate",
        )
        rendered = SlackRenderer().render(payload)
        assert rendered["blocks"][0]["text"] == "Activation below beta gate"
        # The domain payload itself never learned about Slack.
        assert payload.payload_type == SurfacePayloadType.KPI_ALERT


class TestQueryAdapterBridge:
    def test_query_adapter_backs_self_research(self):
        class FakeJiraQueryAdapter:
            adapter_id = "jira-query"
            source_system = "jira"

            def search(self, *, query, project_id, as_of, max_results):
                return [
                    make_envelope(
                        source_system="jira", source_type="ticket",
                        source_id="JIRA-42", event_type="ticket_updated",
                        observed_at=_OBSERVED, project_ids=[project_id],
                        content="Prepaid compatibility validation complete",
                        received_at=_OBSERVED,
                    )
                ]

        provider = QueryAdapterEvidenceSearchProvider(
            adapter=FakeJiraQueryAdapter(), stage=ResearchStage.TICKETS,
        )
        results = provider.search(
            project_id=_PROJECT, query="prepaid validation",
            as_of=datetime(2026, 8, 11), max_results=5,
        )
        assert provider.stage == ResearchStage.TICKETS
        assert len(results) == 1
        assert results[0].content == (
            "Prepaid compatibility validation complete"
        )
        assert results[0].source.source_type.value == "ticket"
