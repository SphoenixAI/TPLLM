"""Hosted durability boundaries, independent of a paid cloud account."""
import io
from pathlib import Path
import sqlite3
import tarfile

from fastapi.testclient import TestClient
import pytest

from tpllm.hosted import (
    PostgresWorkspace, WorkspaceBusy, create_hosted_app,
    pack_workspace, restore_workspace,
)


class MemoryStore:
    payload = b""
    saves = 0
    fail = False
    busy = False

    def __init__(self, *_):
        pass

    def __enter__(self):
        if type(self).busy:
            raise WorkspaceBusy()
        return self

    def save(self, root):
        if type(self).fail:
            raise RuntimeError("credential-that-must-not-leak")
        type(self).payload = pack_workspace(root)
        type(self).saves += 1

    def __exit__(self, *_):
        pass


@pytest.fixture
def client(monkeypatch):
    MemoryStore.payload, MemoryStore.saves = b"", 0
    MemoryStore.fail = MemoryStore.busy = False
    monkeypatch.setenv("TPLLM_DATABASE_URL", "postgresql://unused.example/database")
    monkeypatch.setenv("TPLLM_WORKSPACE_ID", "test")
    with TestClient(create_hosted_app(factory=MemoryStore)) as client:
        yield client


def test_snapshot_includes_committed_wal_and_artifacts(tmp_path):
    root, restored = tmp_path / "source", tmp_path / "restored"
    root.mkdir()
    restored.mkdir()
    db = sqlite3.connect(root / "tpllm.db")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE evidence (value TEXT)")
        db.execute("INSERT INTO evidence VALUES ('committed')")
        db.commit()
        (root / "note.json").write_text('{"source": "test"}')
        restore_workspace(pack_workspace(root), restored)
        assert not (restored / "tpllm.db-wal").exists()
        with sqlite3.connect(restored / "tpllm.db") as result:
            assert result.execute("SELECT value FROM evidence").fetchone()[0] == "committed"
        assert (restored / "note.json").read_text() == '{"source": "test"}'
    finally:
        db.close()


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/escape", "file"), ("safe", "link")])
def test_archive_rejects_unsafe_members(tmp_path, name, kind):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        if kind == "link":
            member.type = tarfile.SYMTYPE
            member.linkname = "/tmp"
        archive.addfile(member)
    with pytest.raises(ValueError):
        restore_workspace(stream.getvalue(), tmp_path)


def test_transaction_pooling_is_rejected():
    with pytest.raises(ValueError, match="session pooling"):
        PostgresWorkspace("postgresql://db.example:6543/postgres", "test")


def test_preview_cannot_share_production_workspace(monkeypatch):
    from tpllm.hosted import workspace_id
    monkeypatch.setenv("TPLLM_WORKSPACE_ID", "production")
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "a" * 40)
    preview = workspace_id()
    monkeypatch.setenv("VERCEL_ENV", "production")
    assert workspace_id() == "production"
    assert preview.startswith("production:preview:")


def test_hosted_key_and_auth_survive_fresh_invocations(client):
    from tpllm.hosted import create_hosted_key
    from tpllm.models.security import Role
    create = dict(name="Test administrator", role=Role.ADMIN, owner="test-operator", factory=MemoryStore)
    _, key = create_hosted_key(**create)
    token = client.post("/api/auth/token", json={"api_key": key})
    assert token.status_code == 200, token.text
    headers = {"X-TPLLM-Client": "dashboard", "Authorization": "Bearer " + token.json()["access_token"]}
    response = client.post("/api/product/coordination/pause?workspace=live", headers=headers, json={"paused": True})
    assert response.status_code == 200, response.text
    assert client.get("/api/product/state?workspace=live").json()["coordination"]["paused"] is True


def test_missing_configuration_fails_closed(client, monkeypatch):
    monkeypatch.delenv("TPLLM_DATABASE_URL")
    assert client.get("/health").status_code == 503
    assert client.get("/api/product/state").status_code == 503


def test_separate_invocations_restore_demo_edits(client):
    headers = {"X-TPLLM-Client": "dashboard"}
    response = client.post("/api/product/demo", headers=headers, json={})
    assert response.status_code == 200, response.text
    first = client.get("/api/product/state?workspace=demo").json()
    assert first["programs"]
    response = client.post("/api/product/coordination/pause?workspace=demo", headers=headers, json={"paused": True})
    assert response.status_code == 200, response.text
    with TestClient(create_hosted_app(factory=MemoryStore)) as other:
        second = other.get("/api/product/state?workspace=demo").json()
    assert second["coordination"]["paused"] is True
    assert {p["id"] for p in first["programs"]} == {p["id"] for p in second["programs"]}
    assert MemoryStore.saves > 4


def test_checkpoint_failure_prevents_external_action(tmp_path):
    from tpllm.config import IntegrationConfig, TPLLMConfig
    from tpllm.product_api import ProductWorkspace
    from tpllm.product_authority import execute_prepared
    from tpllm.product_demo import seed_coordination
    from tpllm.cli_support import now
    ws = ProductWorkspace(TPLLMConfig(data_dir=tmp_path), demo=True)
    try:
        ws.seed_demo()
        seed_coordination(ws)
        ws.put("action_receipts", [])
        action = ws.get("coordination")["actions"][0]
        ws.demo = False
        ws.cfg.slack = IntegrationConfig(enabled=True)
        calls = []
        def fail():
            raise RuntimeError("Remote persistence failed")
        ws.checkpoint = fail
        with pytest.raises(RuntimeError, match="Remote persistence failed"):
            execute_prepared(ws, action, at=now(), sender=lambda **kw: calls.append(kw))
        assert calls == []
    finally:
        ws.db.close()


def test_storage_failure_returns_no_success_or_secret(client):
    MemoryStore.fail = True
    response = client.get("/api/product/state")
    assert response.status_code == 503
    assert "credential-that-must-not-leak" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_busy_workspace_returns_retryable_error(client):
    MemoryStore.busy = True
    response = client.get("/api/product/state")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "3"


def test_cron_requires_its_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-only-cron-secret")
    assert client.get("/api/cron/collect").status_code == 401
    assert client.get("/api/cron/collect", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/api/cron/collect", headers={"Authorization": "Bearer test-only-cron-secret"})
    assert response.status_code == 200, response.text
