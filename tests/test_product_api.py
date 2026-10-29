"""Product-level journeys: persistent mutations, provenance and access boundaries.

No provider or model network calls occur in these tests. The demo and real
workspace both execute the existing domain engines in isolated directories.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jsonschema")
from fastapi.testclient import TestClient

from tpllm.artifacts.store import ArtifactStore
from tpllm.config import TPLLMConfig
from tpllm.dashboard import create_app
from tpllm.models.security import Role
from tpllm.workflows.security_manager import SecurityManager

HEADER = {"X-TPLLM-Client": "dashboard"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(TPLLMConfig(data_dir=tmp_path)))


def post(client, path, body=None, workspace="demo", headers=None):
    return client.post(f"/api/product{path}?workspace={workspace}", json=body or {}, headers={**HEADER, **(headers or {})})


def state(client, workspace="demo"):
    response = client.get(f"/api/product/state?workspace={workspace}")
    assert response.status_code == 200, response.text
    return response.json()


def seed(client):
    response = post(client, "/demo")
    assert response.status_code == 200, response.text
    return state(client)


def action(client, name, arguments, **kwargs):
    return post(client, f"/actions/{name}", {"arguments": arguments}, **kwargs)


def token(client, root, role=Role.MANAGER, scopes=None):
    _, key = SecurityManager(ArtifactStore(root)).create_api_key("test", role, "operator", scopes=scopes)
    response = client.post("/api/auth/token", json={"api_key": key})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_demo_is_explicit_isolated_and_idempotent(client, tmp_path):
    assert state(client)["programs"] == []
    first = seed(client)
    assert len(first["programs"]) == 4
    assert len(first["evidence"]) == 10
    assert {r["provider"] for r in first["coordination"]["records"]} == {"github", "slack", "calendar", "zoom"}
    assert state(client, "live")["programs"] == []
    assert seed(client)["evidence"] == first["evidence"]
    assert (tmp_path / "product-demo" / "tpllm.db").exists()
    assert not any(c["configured"] for c in first["connections"])


def test_task_status_and_cleared_fields_survive_server_restart(client, tmp_path):
    seed(client)
    result = action(client, "create_board_task", {"program_id": "visual-search", "title": "Test handoff", "assignee": "Alex", "due_date": "2026-11-01"})
    assert result.status_code == 200, result.text
    task_id = result.json()["result"]["task_id"]
    result = action(client, "update_board_task", {"program_id": "visual-search", "task_id": task_id, "status": "DONE", "assignee": "", "due_date": ""})
    assert result.status_code == 200, result.text
    restarted = TestClient(create_app(TPLLMConfig(data_dir=tmp_path)))
    task = next(t for p in state(restarted)["programs"] for t in p["artifacts"].get("task_board", {}).get("tasks", []) if t["id"] == task_id)
    assert task["status"] == "DONE"
    assert task["assignee"] == ""
    assert task["due_date"] is None


def test_live_mutations_require_auth_and_role(client, tmp_path):
    args = {"program_id": "new", "name": "New project", "goal": "Verify product"}
    assert action(client, "create_program", args, workspace="live").status_code == 401
    viewer = token(client, tmp_path, Role.VIEWER)
    assert action(client, "create_program", args, workspace="live", headers=viewer).status_code == 403
    manager = token(client, tmp_path)
    assert action(client, "create_program", args, workspace="live", headers=manager).status_code == 200
    assert state(client, "live")["programs"][0]["outlook"] == "Unknown"
    assert action(client, "create_program", args, workspace="live", headers=manager).status_code == 409


def test_task_review_preserves_source_and_can_be_cleared(client, tmp_path):
    first = seed(client)
    original = next(p for p in first["programs"] if p["id"] == "visual-search")["artifacts"]["task_board"]["tasks"][0]
    args = {"program_id": "visual-search", "task_id": original["id"], "title": "Reviewed commitment", "description": "Clarified acceptance", "reviewer": "Demo reviewer", "review_confirmed": True}
    assert action(client, "update_board_task", args).status_code == 200
    restarted = TestClient(create_app(TPLLMConfig(data_dir=tmp_path)))
    def task():
        return next(t for p in state(restarted)["programs"] for t in p["artifacts"].get("task_board", {}).get("tasks", []) if t["id"] == original["id"])
    updated = task()
    assert updated["title"] == "Reviewed commitment"
    assert updated["reviewer"] == "Demo reviewer"
    assert updated["review"]["actor"] == "local-operator"
    for field in ("source_id", "source_context", "confidence"):
        assert updated[field] == original[field]
    assert action(restarted, "update_board_task", {**args, "review_confirmed": False}).status_code == 200
    assert task()["review"] is None


def test_scoped_key_cannot_mutate_another_program_or_global_state(client, tmp_path):
    scoped = token(client, tmp_path, scopes=["mine"])
    assert action(client, "create_program", {"program_id": "other", "name": "Other", "goal": "No"}, workspace="live", headers=scoped).status_code == 403
    assert post(client, "/analyze", workspace="live", headers=scoped).status_code == 403


@pytest.mark.parametrize("program_id", ["../outside", "/tmp/escape", "a/b", 23, ""])
def test_tool_path_traversal_and_bad_identifiers_rejected(client, program_id):
    response = action(client, "create_program", {"program_id": program_id, "name": "No", "goal": "No"})
    assert response.status_code == 422, response.text


def test_same_origin_write_guard(client):
    assert client.post("/api/product/demo").status_code == 403
    assert post(client, "/demo", headers={"Origin": "https://evil.example"}).status_code == 403
    assert post(client, "/demo", headers={"Origin": "http://testserver"}).status_code == 200


def test_unknown_action_schema_errors_and_unavailable_operations(client):
    seed(client)
    assert action(client, "not_a_tool", {}).status_code == 404
    assert action(client, "create_board_task", {"program_id": "visual-search", "title": "x", "admin": True}).status_code == 422
    assert action(client, "create_board_task", {"program_id": "visual-search"}).status_code == 422
    assert action(client, "record_decision", {}).status_code == 403
    assert action(client, "export_program_report_pdf", {"program_id": "visual-search", "filename": "/tmp/unsafe"}).status_code == 403
    assert action(client, "create_api_key", {}).status_code == 404


def decision_from(data):
    p = next(p for p in data["programs"] if p["id"] == "visual-search")
    return p["artifacts"]["decision_log"]["decisions"][0]


def test_validate_decision_updates_log_and_temporal_journal(client):
    d = decision_from(seed(client))
    response = post(client, f"/decisions/{d['id']}/review", {"program_id": "visual-search", "action": "validate", "option": d["options"][0]["label"], "rationale": "Owner reviewed tradeoffs."})
    assert response.status_code == 200, response.text
    assert decision_from(state(client))["status"] == "DECIDED"
    journal = client.get("/api/product/history?workspace=demo").json()["records"]
    assert any(r["event_type"] == "human_recommendation_validated" for r in journal)
    assert any(r["event_type"] == "organizational_decision_recorded" for r in journal)
    assert post(client, f"/decisions/{d['id']}/review", {"program_id": "visual-search", "action": "validate", "option": d["options"][0]["label"], "rationale": "Duplicate"}).status_code == 409
    assert post(client, "/analyze").json()["complete"] is True


def test_defer_keeps_decision_open_and_can_be_revisited(client):
    d = decision_from(seed(client))
    for rationale in ("Need reviewer confirmation", "Need the source record"):
        response = post(client, f"/decisions/{d['id']}/review", {"program_id": "visual-search", "action": "defer", "rationale": rationale})
        assert response.status_code == 200, response.text
    assert decision_from(state(client))["status"] == "PROPOSED"
    assert post(client, f"/decisions/{d['id']}/review", {"program_id": "visual-search", "action": "reject", "rationale": "Insufficient authority"}).status_code == 200
    assert post(client, f"/decisions/{d['id']}/review", {"program_id": "visual-search", "action": "validate", "option": d["options"][0]["label"], "rationale": "No"}).status_code == 409


def test_invalid_or_future_review_does_not_append_a_record(client):
    d = decision_from(seed(client))
    before = client.get("/api/product/history?workspace=demo").json()
    for body in [
        {"action": "validate", "option": "Invented option"},
        {"action": "modify", "option": "New proposal"},
        {"action": "modify", "option": "New proposal", "effective_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()},
    ]:
        assert post(client, f"/decisions/{d['id']}/review", {"program_id": "visual-search", "rationale": "Review", **body}).status_code == 422
    assert client.get("/api/product/history?workspace=demo").json() == before


def test_existing_target_confirmation_preserves_opposing_sources(client):
    first = seed(client)
    response = post(client, "/clarifications/request-accuracy-target", {"arguments": {"answer": "97%"}})
    assert response.status_code == 200, response.text
    data = state(client)
    visual_search = next(p for p in data["programs"] if p["id"] == "visual-search")
    assert visual_search["success_metrics"][0]["target"] == "97%"
    assert [m["target"] for m in visual_search["success_metrics"][1:]] == ["98%", "100%"]
    assert data["coordination"]["kpis"][0]["committed_target"] == "97%"
    assert len(data["evidence"]) == len(first["evidence"]) + 1
    assert any("95%" in e["normalized_content"] for e in data["evidence"])
    assert post(client, "/clarifications/request-accuracy-target", {"arguments": {"answer": "95%"}}).status_code == 409


def test_not_sure_preserves_target_and_uncertainty(client):
    seed(client)
    assert post(client, "/clarifications/request-accuracy-target", {"arguments": {"answer": "Not sure"}}).json()["status"] == "needs_research"
    visual_search = next(p for p in state(client)["programs"] if p["id"] == "visual-search")
    assert visual_search["success_metrics"][0]["target"] == "95%"


def test_source_time_deduplication_and_rich_analysis_outputs(client):
    seed(client)
    body = {"program_id": "visual-search", "title": "Launch source", "content": "Architecture approved the contract.", "source_id": "review-42", "observed_at": "2026-10-20T12:00:00-07:00"}
    first = post(client, "/evidence", body)
    assert first.status_code == 200, first.text
    assert post(client, "/evidence", body).json()["evidence_records_deduplicated"] == 1
    s = state(client)
    e = next(e for e in s["evidence"] if e["source_id"] == "review-42")
    assert e["observed_at"] == "2026-10-20T19:00:00"
    assert e["received_at"] != e["observed_at"]
    assert any(a["artifact_type"] == "project_understanding" and a["payload"]["snapshots"] for a in s["runtime_artifacts"])
    assert any(a["artifact_type"] == "scenario_lattice" and a["payload"]["probability_confidence"] == 0 for a in s["runtime_artifacts"])


def test_meeting_extracts_source_linked_tasks_and_document_renders(client):
    seed(client)
    result = action(client, "extract_tasks_from_meeting", {"program_id": "visual-search", "meeting_id": "M-test", "meeting_topic": "Review", "raw_notes": "Alex will review the contract by Friday.", "action_items": [{"owner": "Alex", "description": "Review contract"}]})
    assert result.status_code == 200, result.text
    assert result.json()["result"]["tasks_created"] >= 1
    s = state(client)
    ts = next(p for p in s["programs"] if p["id"] == "visual-search")["artifacts"]["task_board"]["tasks"]
    assert any(t["source_id"] == "M-test" for t in ts)
    response = action(client, "render_document", {"program_id": "visual-search", "doc_type": "program_brief", "fmt": "markdown"})
    assert response.status_code == 200, response.text
    assert "AI visual search" in str(response.json()["result"])


def test_mapping_does_not_claim_a_live_connection(client):
    seed(client)
    response = post(client, "/connections/github", {"arguments": {"program_id": "visual-search", "scope": "example/repository"}})
    assert response.status_code == 200
    conn = next(c for c in state(client)["connections"] if c["id"] == "github")
    assert conn["mapping"]["scope"] == "example/repository"
    assert conn["configured"] is False
    assert conn["live_verified"] is False
    assert post(client, "/connections/github/read", {"confirm_external": True}).status_code == 409


def test_project_edit_and_outcome_persist(client):
    seed(client)
    body = {"name": "AI visual search v2", "goal": "A new goal", "authority": "local-operator", "health": "YELLOW", "health_rationale": "Approval is pending", "non_goals": ["Pricing changes"]}
    assert post(client, "/projects/visual-search", body).status_code == 200
    p = next(p for p in state(client)["programs"] if p["id"] == "visual-search")
    assert p["name"] == "AI visual search v2"
    assert p["outlook_basis"] == "Approval is pending"
    response = post(client, "/outcomes", {"arguments": {"program_id": "visual-search", "observation": "Approval completed after handover."}})
    assert response.status_code == 200, response.text
    assert response.json()["event_type"] == "outcome_recorded"


def test_outcome_links_exact_decision_and_rejects_cross_project_reference(client):
    decision = decision_from(seed(client))
    args = {"program_id": "visual-search", "decision_id": decision["id"], "observation": "Reviewed the observed result."}
    response = post(client, "/outcomes", {"arguments": args})
    assert response.status_code == 200, response.text
    assert response.json()["payload"]["decision_id"] == decision["id"]
    assert post(client, "/outcomes", {"arguments": {**args, "program_id": "vision-model"}}).status_code == 404


def test_empty_analysis_and_model_requests_are_honest(client):
    assert post(client, "/analyze").status_code == 409
    assert post(client, "/ask", {"question": "What changed?", "confirm_external": True}).status_code == 401


def test_catalogue_covers_workflow_families_and_never_exposes_executors(client):
    catalogue = client.get("/api/product/catalogue?workspace=demo").json()
    assert len(catalogue["groups"]) == 32
    assert len(catalogue["tools"]) > 100
    assert all("executor" not in t for t in catalogue["tools"])
    assert not any(t["external"] for t in catalogue["tools"])


def test_nested_workflow_filename_is_confined_to_workspace(client):
    response = action(client, "get_notification_preferences", {"person": "../../outside"})
    assert response.status_code == 422, response.text


def test_demo_restart_archives_previous_run_and_does_not_touch_live(client, tmp_path):
    seed(client)
    assert action(client, "create_board_task", {"program_id": "visual-search", "title": "Archive this test"}).status_code == 200
    before_live = state(client, "live")
    response = post(client, "/demo/restart")
    assert response.status_code == 200, response.text
    archive = tmp_path / "demo-archives" / response.json()["archived_run"]
    assert (archive / "tpllm.db").is_file()
    assert "Archive this test" in (archive / "programs/visual-search/task_board.json").read_text()
    assert "Archive this test" not in str(state(client))
    assert state(client, "live")["programs"] == before_live["programs"]


def test_product_route_inventory_and_mutation_auth_dependencies(client):
    from tpllm.product_api import PRODUCT_UNAUTHENTICATED_READ_ROUTES
    reads = set()
    routes = []
    for candidate in client.app.routes:
        nested = getattr(candidate, "original_router", None)
        routes.extend(nested.routes if nested else [candidate])
    for route in routes:
        if not getattr(route, "path", "").startswith("/api/product"):
            continue
        if "GET" in route.methods:
            reads.add(route.path)
        if "POST" in route.methods:
            assert any(d.call.__name__ == "get_current_user" for d in route.dependant.dependencies), route.path
    assert reads == set(PRODUCT_UNAUTHENTICATED_READ_ROUTES)
