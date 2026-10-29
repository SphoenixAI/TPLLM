"""Product acceptance: sources change -> context changes -> bounded follow-through.

No test reaches a provider. Injected readers exercise production collection and
execution paths; simulated receipts are never counted as real delivery.
"""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jsonschema")
from tpllm.cli_support import now
from tpllm.config import TPLLMConfig, IntegrationConfig
from tpllm.product_api import ProductWorkspace
from tpllm.product_demo import seed_coordination, advance_coordination
from tpllm.product_flow import accept_collection, cycle, derive
from tpllm.product_sources import Collection, SourceRecord, collect_github, collect_slack, collect_calendar, collect_zoom
from tpllm.product_authority import eligible, execute_prepared


@pytest.fixture
def ws(tmp_path):
    space = ProductWorkspace(TPLLMConfig(data_dir=tmp_path), demo=True)
    space.seed_demo()
    seed_coordination(space)
    return space


def action(ws):
    return deepcopy(ws.get("coordination")["actions"][0])


def reset_attempts(ws):
    ws.put("action_receipts", [])
    return action(ws)


def test_sources_produce_meeting_intervention_kpis_and_typed_context(ws):
    data = ws.snapshot()["coordination"]
    assert {r["provider"] for r in data["records"]} == {"github", "slack", "calendar", "zoom"}
    assert data["meetings"][0]["assessment"] == "inferred"
    assert "asynchronously" in data["meetings"][0]["recommendation"]
    assert "vision inference" in data["actions"][0]["body"]
    assert data["kpis"][0]["trend"]["latest_value"] == 89
    assert data["kpis"][0]["committed_target"] == "95%"
    assert {k["name"]: k["trend"]["latest_value"] for k in data["kpis"]} == {
        "Request match accuracy": 89, "Recognition accuracy": 96, "Package alignment": 75,
    }
    assert any(c["target_candidate"] == 97 for c in data["kpis"][0]["claims"])
    assert not any(r["resolved_observation_id"] for r in data["kpis"][0]["reconciliation"])
    assert not any("authoritative" in r["explanation"] for r in data["kpis"][0]["reconciliation"])
    assert "insufficient" in data["kpis"][0]["trend"]["direction"]
    assert {r["relation"] for r in data["relationships"]} >= {"references", "authored", "assigned", "review_requested"}
    assert data["receipts"][0]["status"] == "simulated"
    assert "No external" in data["receipts"][0]["result"]


def test_paragraph_kpis_preserve_values_and_target_ownership(ws):
    raw = next(r for r in ws.get("source_records").values() if r["provider"] == "zoom")
    record = SourceRecord.model_validate(raw)
    record.text = "Request match accuracy is 89%. Target is 97% before beta. Recognition accuracy is 96%. Target is 98%. Package alignment is 75%. Target is 100%."
    record.observed_at = now()
    accept_collection(ws, Collection(records=[record]))
    derive(ws)
    kpis = {k["name"]: k for k in ws.get("coordination")["kpis"]}
    expected = {"Request match accuracy": (89, 97, "95%"), "Recognition accuracy": (96, 98, "98%"), "Package alignment": (75, 100, "100%")}
    for name, (value, proposed, committed) in expected.items():
        assert kpis[name]["trend"]["latest_value"] == value
        assert {c["target_candidate"] for c in kpis[name]["claims"] if c["claim_type"] == "target"} == {proposed}
        assert kpis[name]["committed_target"] == committed


def test_replay_never_duplicates_notes_commitments_or_action(ws):
    before = ws.snapshot()
    cycle(ws)
    cycle(ws)
    after = ws.snapshot()
    assert before["evidence"] == after["evidence"]
    assert len(after["coordination"]["receipts"]) == 1
    assert [p["artifacts"].get("task_board") for p in before["programs"]] == [p["artifacts"].get("task_board") for p in after["programs"]]


def test_source_updates_advance_work_and_brief_without_changing_target(ws):
    before = ws.snapshot()
    result = advance_coordination(ws)
    assert result["changed"] == 2
    data = ws.snapshot()
    assert len(data["evidence"]) == len(before["evidence"]) + 2
    pr = data["coordination"]["engineering"][0]
    assert pr["data"]["state"] == "merged"
    assert not any("open PR" in x or "remain open" in x for x in pr["estimate_basis"])
    task = next(t for p in data["programs"] for t in p["artifacts"].get("task_board", {}).get("tasks", []) if t["source"] == "GITHUB_ISSUE")
    assert task["status"] == "DONE"
    assert "Checks have not completed" in data["coordination"]["briefs"][0]["summary"]
    assert data["coordination"]["kpis"][0]["committed_target"] == "95%"
    assert [k["trend"]["latest_value"] for k in data["coordination"]["kpis"]] == [89, 96, 75]
    assert any(c["status"] == "queued" for c in pr["data"]["checks"])
    assert len(data["coordination"]["receipts"]) == 1  # frequency limit, no second approval request
    assert "interval" in data["coordination"]["actions"][0]["authority_reason"]
    assert advance_coordination(ws) == {"already_applied": True}


def test_journal_reconstructs_source_projection_after_restart(ws):
    expected = ws.snapshot()["coordination"]["records"]
    ws.put("source_records", {})
    restarted = ProductWorkspace(ws.cfg, demo=True)
    assert {r["key"] for r in restarted.snapshot()["coordination"]["records"]} == {r["key"] for r in expected}
    assert len(restarted.get("action_receipts")) == 1


@pytest.mark.parametrize("boundary", ["revoked", "expired", "paused", "wrong_project", "wrong_destination", "wrong_purpose", "visibility", "stale", "changed", "reserved", "uncertainty", "limit"])
def test_each_authority_boundary_prevents_execution(ws, boundary):
    a = reset_attempts(ws)
    grants = ws.get("delegations")
    grant = grants[0]
    if boundary == "revoked": grant["enabled"] = False
    if boundary == "expired": grant["expires_at"] = (now() - timedelta(seconds=1)).isoformat()
    if boundary == "paused": ws.put("coordination_paused", True)
    if boundary == "wrong_project": a["project_id"] = "vision-model"
    if boundary == "wrong_destination": a["destination"] = "different-channel"
    if boundary == "wrong_purpose": a["purpose"] = "customer_promise"
    if boundary == "visibility": grant["source_scopes"] = ["slack:visual-search-launch-demo"]
    if boundary == "reserved": a["reserved"] = True
    if boundary == "uncertainty": a["uncertainty"] = True
    if boundary == "changed": a["source_versions"][0]["version"] = "outdated"
    if boundary == "limit":
        ws.put("action_receipts", [{"grant_id": grant["id"], "at": now().isoformat(), "action_id": str(i), "status": "accepted"} for i in range(grant["max_per_day"])])
    ws.put("delegations", grants)
    calls = []
    result = execute_prepared(ws, a, at=now() + (timedelta(days=2) if boundary == "stale" else timedelta()), sender=lambda **kw: calls.append(kw))
    assert result["status"] == "held", result
    assert calls == []


def test_provider_acknowledgement_and_ambiguous_failure_do_not_retry(ws):
    a = reset_attempts(ws)
    ws.demo = False  # same isolated store, injected sender only
    ws.cfg.slack = IntegrationConfig(enabled=True)
    calls = []
    def sender(**kwargs):
        calls.append(kwargs)
        return "provider-message-id"
    first = execute_prepared(ws, a, at=now(), sender=sender)
    assert first["status"] == "accepted"
    assert len(first["transitions"]) == 2
    assert "comprehension is not confirmed" in first["result"]
    assert execute_prepared(ws, a, at=now(), sender=sender)["status"] == "held"
    assert len(calls) == 1
    ws.put("action_receipts", [])
    def timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError()
    assert execute_prepared(ws, a, at=now(), sender=timeout)["status"] == "needs_reconciliation"
    assert execute_prepared(ws, a, at=now(), sender=timeout)["status"] == "held"
    assert len(calls) == 2
    a["id"] += ":new"
    a["body"] += "\nAnother revision of the update."
    assert execute_prepared(ws, a, at=now(), sender=timeout)["status"] == "held"
    assert len(calls) == 2


def test_frequency_interval_does_not_reset_at_midnight(ws):
    a = reset_attempts(ws)
    at = (now() + timedelta(days=1)).replace(hour=0, minute=2, second=0)
    grant = ws.get("delegations")[0]
    grant["max_evidence_age_hours"] = 48
    ws.put("delegations", [grant])
    ws.put("action_receipts", [{"grant_id": grant["id"], "at": (at - timedelta(minutes=4)).isoformat(), "action_id": "earlier", "status": "accepted"}])
    matched, reason = eligible(ws, a, at)
    assert matched is None
    assert "interval" in reason


def test_late_source_does_not_roll_back_current_state(ws):
    r = SourceRecord.model_validate(ws.snapshot()["coordination"]["records"][0])
    old_version = r.version
    r.observed_at -= timedelta(days=1)
    r.data["state"] = "closed"
    result = accept_collection(ws, Collection([r]))
    assert result["changed"] == 1
    assert ws.get("source_records")[r.key]["version"] == old_version


def test_unlinked_status_request_does_not_trigger_an_intervention(ws):
    r = next(r for r in ws.get("source_records").values() if r["provider"] == "slack")
    changed = SourceRecord.model_validate(r)
    changed.text = "A separate project needs a launch date update."
    changed.observed_at = now()
    accept_collection(ws, Collection([changed]))
    assert ws.get("coordination")["actions"] == []


def test_meeting_title_alone_does_not_authorize_agenda_routing(ws):
    raw = next(r for r in ws.get("source_records").values() if r["kind"] == "meeting")
    r = SourceRecord.model_validate(raw)
    r.text = "Engineering discussion."
    r.observed_at = now()
    ws.put("action_receipts", [])
    accept_collection(ws, Collection([r]))
    cycle(ws)
    data = ws.get("coordination")
    assert data["meetings"][0]["assessment"] == "unknown"
    assert data["actions"][0]["status"] == "held"
    assert ws.get("action_receipts") == []


def test_past_meeting_and_failed_collection_hold_automatic_interventions(ws):
    ws.put("action_receipts", [])
    ws.put("collection:slack", {"status": "unavailable"})
    cycle(ws)
    assert ws.get("coordination")["actions"][0]["status"] == "held"
    assert ws.get("action_receipts") == []
    ws.put("collection:slack", {"status": "demo"})
    derive(ws, at=now() + timedelta(hours=4))
    assert ws.get("coordination")["actions"][0]["status"] == "held"


def test_prior_revision_checks_cannot_imply_current_readiness(ws):
    from tpllm.product_flow import engineering
    r = deepcopy(next(r for r in ws.get("source_records").values() if r["kind"] == "pull_request"))
    r["data"]["state"] = "merged"
    r["data"]["head"] = "new-unchecked-head"
    result = engineering([r])[0]
    assert "No check evidence for the current revision." in result["estimate_basis"]


def test_changed_source_owner_and_title_replace_previous_task_fields(ws):
    raw = next(r for r in ws.get("source_records").values() if r["kind"] == "issue")
    r = SourceRecord.model_validate(raw)
    r.title = "Reclassified retry defect"
    r.data["assignees"] = []
    r.observed_at = now()
    accept_collection(ws, Collection([r]))
    tasks = ws.artifacts("visual-search")["task_board"]["tasks"]
    imported = [t for t in tasks if t["source"] == "GITHUB_ISSUE"]
    assert len(imported) == 1
    assert imported[0]["title"] == r.title
    assert imported[0]["assignee"] == ""


def test_calendar_keeps_source_agenda_and_cancellation_without_inventing_time():
    event = {"id": "review", "summary": "Engineering review", "description": "Launch updates belong in a separate channel.",
             "updated": now().isoformat() + "Z", "status": "cancelled", "organizer": {"email": "organizer@example.test"}}
    class Service:
        def events(self): return self
        def list(self, **kwargs):
            assert kwargs["calendarId"] == "engineering"
            assert kwargs["showDeleted"] is True
            return self
        def execute(self): return {"items": [event, {"id": "untimed", "summary": "Missing source update time"}]}
    batch = collect_calendar(IntegrationConfig(), "engineering", "visual-search", service=Service())
    assert len(batch.records) == 1
    assert batch.records[0].data["status"] == "cancelled"
    assert batch.records[0].data["organizer"] == "organizer@example.test"
    assert batch.limitations


def test_conflicting_reported_values_do_not_silently_become_a_measurement(ws):
    r = SourceRecord(provider="slack", scope="visual-search-launch-demo", project_id="visual-search", source_id="conflicting-metric",
        kind="conversation", title="Request match accuracy report", text="Request match accuracy is 62%.", observed_at=now())
    accept_collection(ws, Collection([r]))
    kpi = ws.get("coordination")["kpis"][0]
    assert kpi["trend"]["latest_value"] is None
    assert kpi["trend"]["health"] == "unknown"
    assert any(r["status"] == "contested" for r in kpi["reconciliation"])


def test_partial_collection_is_visible_and_disabled_sources_are_never_read(ws):
    ws.demo = False
    ws.cfg.github = IntegrationConfig(enabled=True)
    ws.put("collection_settings", {"github": {"enabled": True, "interval_seconds": 300}, "slack": {"enabled": False}})
    calls = []
    def reader(cfg, scope, project):
        calls.append(scope)
        return Collection(limitations=["Checks unavailable"])
    cycle(ws, collectors={"github": reader}, force=True)
    assert calls == ["example/visual-search"]
    assert ws.get("collection:github")["status"] == "partial"
    assert len(ws.get("source_records")) == 5


def test_revoking_collection_during_read_discards_unapproved_result(ws):
    ws.demo = False
    ws.cfg.github = IntegrationConfig(enabled=True)
    ws.put("collection_settings", {"github": {"enabled": True}})
    count = len(ws.evidence())
    def reader(cfg, scope, project):
        ws.put("collection_settings", {"github": {"enabled": False}})
        r = SourceRecord(provider="github", scope=scope, project_id=project, source_id="issue:999", kind="issue", title="Unapproved read", observed_at=now())
        return Collection([r])
    cycle(ws, collectors={"github": reader}, force=True)
    assert len(ws.evidence()) == count


def test_github_reader_preserves_diff_roles_and_current_revision():
    stamp = now().isoformat() + "Z"
    pr = {"number": 42, "title": "Retry fix", "body": "Context", "updated_at": stamp, "state": "open", "head": {"sha": "abc"}, "user": {"login": "author"}, "assignees": [{"login": "owner"}], "requested_reviewers": [{"login": "reviewer"}]}
    class Reader:
        def get(self, path):
            path = path.split('?')[0]
            if path.endswith('/pulls'): return [pr]
            if path.endswith('/pulls/42'): return pr
            if path.endswith('/files'): return [{"filename": "retry.py", "patch": "+ fix()", "additions": 1, "deletions": 0}]
            if path.endswith('/reviews'): return [{"user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED", "commit_id": "old", "submitted_at": stamp}, {"user": {"login": "reviewer"}, "state": "COMMENTED", "commit_id": "old", "submitted_at": stamp}]
            if path.endswith('/check-runs'): return {"check_runs": [{"head_sha": "abc", "name": "test", "conclusion": "success"}]}
            if path.endswith('/issues'): return []
            raise AssertionError(path)
    batch = collect_github(IntegrationConfig(), "example/repo", "visual-search", reader=Reader())
    r = batch.records[0]
    assert r.data["author"] == "author"
    assert r.data["assignees"] == ["owner"]
    assert r.data["requested_reviewers"] == ["reviewer"]
    assert r.data["files"][0]["patch"] == "+ fix()"
    assert r.data["reviews"][0]["commit"] == "old"
    assert not batch.limitations


def test_slack_threads_are_collected_with_source_time():
    stamp = str(now().replace(tzinfo=__import__('datetime').timezone.utc).timestamp())
    class Reader:
        def get(self, path):
            return {"ok": True, "messages": [{"ts": stamp, "text": "Already answered", "user": "U1", "reply_count": 1}]}
    batch = collect_slack(IntegrationConfig(), "C1", "visual-search", reader=Reader())
    assert len(batch.records) == 1
    assert batch.records[0].data["author"] == "U1"


def test_zoom_collects_existing_ai_summary_only_for_authorized_host():
    class Client:
        def get(self, path, params=None):
            if path == '/meetings/meeting_summaries':
                return {"summaries": [{"meeting_host_email": "allowed@example.test", "meeting_id": 12}, {"meeting_host_email": "outside@example.test", "meeting_id": 13}]}
            assert path == '/meetings/12/meeting_summary'
            return {"summary_content": "Request match accuracy is 89%", "summary_last_modified_time": now().isoformat(), "meeting_topic": "Review"}
    batch = collect_zoom(IntegrationConfig(), "allowed@example.test", "visual-search", client=Client())
    assert len(batch.records) == 1
    assert batch.records[0].text == "Request match accuracy is 89%"


def test_nested_diff_credentials_are_redacted_before_storage():
    raw = 'ghp_' + 'x' * 30
    batch = Collection()
    batch.add(provider="github", scope="example/repo", project_id="visual-search", source_id="pr:1", kind="pull_request", title="test", observed_at=now(), data={"files": [{"patch": raw}]})
    assert raw not in batch.records[0].model_dump_json()


def test_registry_external_tool_calls_cannot_bypass_gateway():
    from tpllm.tools.registry import ToolRegistry
    called = []
    registry = ToolRegistry()
    registry.register_external(name="send_slack_message", description="Send", input_schema={}, executor=lambda **kw: called.append(kw))
    assert "standing authority" in registry.execute("send_slack_message", {"channel": "C1", "text": "No"})
    assert called == []
