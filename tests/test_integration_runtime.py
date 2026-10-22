"""Event-driven runtime: idempotency, scoped
refresh, interpretation guardrails, serverless shape."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from tpllm.integrations import (
    InMemoryStateStore,
    IntelligenceUpdateType,
    RefreshScope,
    determine_refresh_scope,
    make_envelope,
    process_evidence_envelopes,
)

_OBSERVED = datetime(2026, 8, 10, 9, 30, 0)
_AS_OF = datetime(2026, 8, 10, 12, 0, 0)
_PROJECT = "atlas"


class FakeServices:
    """Duck-typed intelligence services with call counters."""

    def __init__(self):
        self.calls: dict[str, list[str]] = defaultdict(list)

    def _record(self, name, project_id, envelope):
        self.calls[name].append(envelope.id)
        return [f"{name}-{project_id}"]

    def refresh_understanding(self, *, project_id, envelope):
        return self._record("understanding", project_id, envelope)

    def refresh_workstreams(self, *, project_id, envelope):
        return self._record("workstreams", project_id, envelope)

    def refresh_kpi(self, *, project_id, envelope):
        return self._record("kpi", project_id, envelope)

    def refresh_coordination(self, *, project_id, envelope):
        return self._record("coordination", project_id, envelope)

    def refresh_launch_readiness(self, *, project_id, envelope):
        return self._record("launch_readiness", project_id, envelope)


def _envelope(event_type="chat_message", source_id="C123/1", **overrides):
    defaults = dict(
        source_system="slack",
        source_type="chat",
        source_id=source_id,
        event_type=event_type,
        observed_at=_OBSERVED,
        project_ids=[_PROJECT],
        content="some content",
        source_pointer="https://source.example/item",
        received_at=_OBSERVED,
    )
    defaults.update(overrides)
    return make_envelope(**defaults)


def _run(envelopes, services=None, store=None):
    services = services or FakeServices()
    store = store or InMemoryStateStore()
    updates = process_evidence_envelopes(
        envelopes=envelopes,
        project_state_store=store,
        intelligence_services=services,
        as_of=_AS_OF,
    )
    return updates, services, store


class TestIdempotency:
    def test_duplicate_webhook_does_not_duplicate(self):
        duplicate = _envelope(
            event_type="kpi_observation", source_id="metric-1",
        )
        again = _envelope(event_type="kpi_observation", source_id="metric-1")
        assert duplicate.idempotency_key == again.idempotency_key

        updates, services, store = _run([duplicate, again])
        assert len(updates) == 2
        assert updates[1].update_types == [
            IntelligenceUpdateType.NO_MATERIAL_CHANGE
        ]
        assert updates[1].material is False
        # The KPI refresh ran exactly once — no recomputation on the dup.
        assert len(services.calls["kpi"]) == 1
        # Only one evidence pointer stored.
        assert len(store.list("evidence:")) == 1


class TestScopedRefresh:
    def test_kpi_event_triggers_kpi_refresh(self):
        updates, services, _ = _run(
            [_envelope(event_type="kpi_observation", source_id="metric-1",
                       source_system="analytics", source_type="ontology")],
        )
        assert services.calls["kpi"]
        assert IntelligenceUpdateType.KPI_CHANGED in updates[0].update_types

    def test_decision_event_triggers_coordination_refresh(self):
        updates, services, _ = _run(
            [_envelope(event_type="decision_recorded", source_id="dec-1")],
        )
        assert services.calls["coordination"]
        assert IntelligenceUpdateType.DECISION_CHANGED in (
            updates[0].update_types
        )
        assert updates[0].material is True

    def test_chat_message_stays_evidence_only(self):
        # A Slack opinion is not a decision and triggers no heavy
        # recomputation.
        updates, services, _ = _run([_envelope(event_type="chat_message")])
        assert services.calls == {}
        assert updates[0].material is False
        assert IntelligenceUpdateType.DECISION_CHANGED not in (
            updates[0].update_types
        )

    def test_meeting_transcript_is_evidence_not_truth(self):
        updates, services, _ = _run(
            [_envelope(event_type="meeting_transcript", source_id="meet-9",
                       source_system="zoom", source_type="meeting")],
        )
        # Understanding and KPI extraction refresh from the transcript…
        assert services.calls["understanding"]
        assert services.calls["kpi"]
        # …but nothing is recorded as a decision automatically.
        assert not services.calls["coordination"]
        assert IntelligenceUpdateType.DECISION_CHANGED not in (
            updates[0].update_types
        )

    def test_ticket_completion_never_touches_readiness(self):
        updates, services, _ = _run(
            [_envelope(event_type="ticket_completed", source_id="JIRA-42",
                       source_system="jira", source_type="ticket")],
        )
        assert services.calls["workstreams"]
        assert not services.calls["launch_readiness"]
        assert IntelligenceUpdateType.LAUNCH_READINESS_CHANGED not in (
            updates[0].update_types
        )

    def test_pr_merge_is_technical_evidence_only(self):
        scopes = determine_refresh_scope(
            _envelope(event_type="pr_merged", source_id="pr-7",
                      source_system="github", source_type="repository")
        )
        assert scopes == {RefreshScope.EVIDENCE_ONLY}


class TestRoutingAndPointers:
    def test_project_id_routing(self):
        updates, _, _ = _run(
            [_envelope(project_ids=["phoenix"], source_id="C9/1")],
        )
        assert updates[0].project_id == "phoenix"

    def test_source_pointer_preserved_in_store(self):
        envelope = _envelope(event_type="decision_recorded")
        _, _, store = _run([envelope])
        stored = store.get(f"evidence:{envelope.id}")
        assert stored["source_pointer"] == "https://source.example/item"
        assert stored["content_hash"] is not None
        # The pointer record carries no full content copy.
        assert "content" not in stored


class TestServerlessShape:
    def test_single_call_no_server(self):
        # One event in, state loaded via the store protocol, affected
        # intelligence updated, results returned, done — nothing running.
        store = InMemoryStateStore()
        updates, _, _ = _run(
            [_envelope(event_type="kpi_observation", source_id="m-1")],
            store=store,
        )
        assert len(updates) == 1
        assert store.list("idempotency:")  # state persisted for next call

    def test_no_heavy_backend_dependencies(self):
        import inspect
        import tpllm.integrations.runtime as runtime
        import tpllm.integrations.envelopes as envelopes
        import tpllm.integrations.registry as registry

        for mod in (runtime, envelopes, registry):
            source = inspect.getsource(mod).lower()
            for dependency in ("sqlalchemy", "kafka", "celery", "django",
                               "psycopg", "rabbitmq"):
                assert dependency not in source, (
                    f"{mod.__name__} must not depend on '{dependency}' — "
                    f"the runtime stays lightweight."
                )

    def test_services_missing_methods_tolerated(self):
        class MinimalServices:
            pass

        updates = process_evidence_envelopes(
            envelopes=[_envelope(event_type="decision_recorded")],
            project_state_store=InMemoryStateStore(),
            intelligence_services=MinimalServices(),
            as_of=_AS_OF,
        )
        assert len(updates) == 1  # graceful, still records evidence
