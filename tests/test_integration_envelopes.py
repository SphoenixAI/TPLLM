"""Evidence envelopes, wrapping, retention, and
security guardrails."""

from __future__ import annotations

from datetime import datetime

import pytest

from lltpm.integrations import (
    EvidenceEnvelope,
    IntegrationRegistry,
    RetentionClass,
    WrappedToolAdapter,
    build_idempotency_key,
    envelope_to_project_evidence,
    get_adapter,
    list_available_sources,
    make_envelope,
    minimize_envelope,
    register_adapter,
    sanitize_metadata,
)
from lltpm.integrations.slack_integration import SlackMessage

_OBSERVED = datetime(2026, 8, 10, 9, 30, 0)
_PROJECT = "atlas"


def _envelope(**overrides):
    defaults = dict(
        source_system="slack",
        source_type="chat",
        source_id="C123/1723280000.1",
        event_type="chat_message",
        observed_at=_OBSERVED,
        project_ids=[_PROJECT],
        content="Payments requests approval of v3 for enterprise accounts.",
        title="#payments-launch",
        source_pointer="https://slack.example/archives/C123/p1723280000",
        received_at=_OBSERVED,
    )
    defaults.update(overrides)
    return make_envelope(**defaults)


class TestEnvelopes:
    def test_envelope_validates(self):
        envelope = _envelope()
        assert envelope.idempotency_key
        assert envelope.project_ids == [_PROJECT]
        assert envelope.source_pointer.startswith("https://")

    def test_idempotency_key_deterministic(self):
        key_a = build_idempotency_key(
            source_system="slack", source_id="C123/1",
            event_type="chat_message", observed_at=_OBSERVED,
        )
        key_b = build_idempotency_key(
            source_system="slack", source_id="C123/1",
            event_type="chat_message", observed_at=_OBSERVED,
        )
        key_c = build_idempotency_key(
            source_system="slack", source_id="C123/2",
            event_type="chat_message", observed_at=_OBSERVED,
        )
        assert key_a == key_b
        assert key_a != key_c
        assert _envelope().idempotency_key == _envelope().idempotency_key

    def test_envelope_converts_to_project_evidence(self):
        evidence = envelope_to_project_evidence(
            _envelope(), project_id=_PROJECT,
        )
        assert evidence is not None
        assert evidence.source.source_type.value == "chat"
        assert evidence.content.startswith("Payments requests")

    def test_contentless_envelope_yields_no_evidence_text(self):
        assert envelope_to_project_evidence(
            _envelope(content=None), project_id=_PROJECT,
        ) is None


class TestSecrets:
    def test_tokens_stripped_by_sanitizer(self):
        cleaned = sanitize_metadata(
            {"channel": "C123", "access_token": "xoxb-1", "api_key": "k",
             "webhook_signing_key": "s"},
        )
        assert cleaned == {"channel": "C123"}

    def test_direct_secret_metadata_rejected(self):
        with pytest.raises(ValueError, match="secret"):
            EvidenceEnvelope(
                id="env-1", source_system="slack", source_type="chat",
                source_id="C1", event_type="chat_message",
                observed_at=_OBSERVED, received_at=_OBSERVED,
                project_ids=[_PROJECT],
                metadata={"oauth_token": "xoxb-1"},
                idempotency_key="k", retention_class="excerpt",
            )

    def test_no_secret_fields_on_model(self):
        field_names = set(EvidenceEnvelope.model_fields)
        for forbidden in ("token", "secret", "signing_key", "password"):
            assert not any(forbidden in name for name in field_names)


class TestWrappingExistingIntegrations:
    def test_existing_slack_type_wrapped(self):
        # Reuse the existing SlackMessage type from the legacy
        # integration — no duplicated OAuth, no new client.
        class LegacySlack:
            source = "slack"

        def normalize(legacy, event: SlackMessage):
            return [
                make_envelope(
                    source_system="slack", source_type="chat",
                    source_id=f"{event.channel}/{event.thread_ts or 'root'}",
                    event_type="chat_message", observed_at=_OBSERVED,
                    project_ids=[_PROJECT], content=event.text,
                    received_at=_OBSERVED,
                )
            ]

        adapter = WrappedToolAdapter(
            adapter_id="slack-wrap", source_system="slack",
            legacy_integration=LegacySlack(), normalize=normalize,
        )
        envelopes = adapter.normalize_event(
            SlackMessage(channel="C123", text="Mobile stays on v2 for now"),
        )
        assert len(envelopes) == 1
        assert envelopes[0].content == "Mobile stays on v2 for now"
        assert envelopes[0].source_system == "slack"

    def test_registry_register_and_list(self):
        registry = IntegrationRegistry()

        class JiraAdapter:
            adapter_id = "jira-main"
            source_system = "jira"

            def normalize_event(self, event):
                return []

        registry = register_adapter(registry=registry, adapter=JiraAdapter())
        assert get_adapter(
            registry=registry, adapter_id="jira-main"
        ).source_system == "jira"
        assert list_available_sources(registry=registry) == ["jira"]


class TestRetention:
    def test_pointer_only_drops_content_keeps_pointer(self):
        envelope = _envelope()
        minimized, pointer = minimize_envelope(
            envelope=envelope, retention_class=RetentionClass.POINTER_ONLY,
        )
        assert minimized.content is None
        assert pointer.source_pointer == envelope.source_pointer
        assert pointer.content_hash is not None
        # Original envelope untouched.
        assert envelope.content is not None

    def test_excerpt_truncates(self):
        envelope = _envelope(content="x" * 2000)
        minimized, _ = minimize_envelope(
            envelope=envelope, retention_class=RetentionClass.EXCERPT,
        )
        assert len(minimized.content) == 500
