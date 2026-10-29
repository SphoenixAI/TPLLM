"""Single-operator Vercel adapter backed by Supabase PostgreSQL.

The established SQLite/JSON format remains the workspace format. PostgreSQL
stores compressed snapshots; a session advisory lock serializes invocations.
Use Supabase's direct connection or SESSION pooler, never transaction pooling.
All routes must sit behind Vercel Authentication (including production).
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import hmac
import io
import logging
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tarfile
import tempfile
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from tpllm.config import TPLLMConfig

logger = logging.getLogger(__name__)
MAX_WORKSPACE_BYTES = 64 * 1024 * 1024
MAX_FILES = 10000
MAX_REQUEST_BYTES = 4 * 1024 * 1024
WEB_ROOT = Path(__file__).parent / "web"


class WorkspaceBusy(Exception):
    pass


def workspace_id() -> str:
    name = os.environ["TPLLM_WORKSPACE_ID"]
    if os.getenv("VERCEL_ENV") == "preview":
        revision = os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ["VERCEL_URL"]
        name += ":preview:" + hashlib.sha256(revision.encode()).hexdigest()[:20]
    return name


def pack_workspace(root: Path) -> bytes:
    """Use SQLite backup so committed WAL records are included atomically."""
    output = io.BytesIO()
    total = 0
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    if len(paths) > MAX_FILES:
        raise ValueError("Workspace file limit exceeded")
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Workspace cannot contain symbolic links")
            if path.name.endswith(("-wal", "-shm", "-journal")):
                continue
            if path.suffix in {".db", ".sqlite", ".sqlite3"}:
                with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(":memory:")) as copy:
                    source.backup(copy)
                    data = copy.serialize()
            else:
                if path.stat().st_size > MAX_WORKSPACE_BYTES:
                    raise ValueError("Workspace file limit exceeded")
                data = path.read_bytes()
            total += len(data)
            if total > MAX_WORKSPACE_BYTES:
                raise ValueError("Workspace size limit exceeded")
            info = tarfile.TarInfo(path.relative_to(root).as_posix())
            info.size = len(data)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def restore_workspace(payload: bytes, root: Path) -> None:
    """Reject traversal, links and oversized archives before writing files."""
    if not payload:
        return
    if len(payload) > MAX_WORKSPACE_BYTES:
        raise ValueError("Workspace archive limit exceeded")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_FILES or sum(m.size for m in members) > MAX_WORKSPACE_BYTES:
            raise ValueError("Workspace archive limit exceeded")
        seen = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or ".." in path.parts
                    or "\\" in member.name or member.name in seen):
                raise ValueError("Unsafe workspace archive")
            seen.add(member.name)
        for member in members:
            target = root / member.name
            if not target.resolve().is_relative_to(root.resolve()):
                raise ValueError("Unsafe workspace destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            assert stream is not None
            target.write_bytes(stream.read())
            target.chmod(0o600)


class PostgresWorkspace:
    """Commit snapshots without releasing the request's session-level lock."""
    def __init__(self, url: str, workspace: str):
        parsed = urlparse(url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ValueError("TPLLM_DATABASE_URL must be a PostgreSQL connection")
        if parsed.port == 6543:
            raise ValueError("Use Supabase session pooling on port 5432")
        self.url, self.workspace = url, workspace
        self.connection = None

    def __enter__(self):
        import psycopg
        self.connection = psycopg.connect(
            self.url, autocommit=True, connect_timeout=10, sslmode="require",
        )
        try:
            self.connection.execute("SET statement_timeout = '15s'")
            self.connection.execute("SET idle_session_timeout = '240s'")
            key = int.from_bytes(hashlib.sha256(self.workspace.encode()).digest()[:8], "big", signed=True)
            try:
                # The browser loads state and catalogue concurrently. Queue
                # short requests instead of making every refresh conflict.
                self.connection.execute("SELECT pg_advisory_lock(%s)", (key,))
            except psycopg.errors.QueryCanceled as exc:
                raise WorkspaceBusy() from exc
            row = self.connection.execute(
                "SELECT payload FROM tpllm_hosting.workspaces WHERE id = %s", (self.workspace,),
            ).fetchone()
            self.payload = bytes(row[0]) if row else b""
            return self
        except BaseException:
            self.connection.close()
            raise

    def save(self, root: Path):
        payload = pack_workspace(root)
        self.connection.execute(
            "INSERT INTO tpllm_hosting.workspaces (id, payload) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, "
            "revision = tpllm_hosting.workspaces.revision + 1, updated_at = now()",
            (self.workspace, payload),
        )

    def __exit__(self, *_):
        # Closing the dedicated session releases its advisory lock even after
        # errors. Never return this connection to a transaction-mode pool.
        self.connection.close()


async def invoke(app, scope, body):
    messages = []
    delivered = False
    finished = asyncio.Event()

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            finished.set()

    await app(scope, receive, send)
    return messages


def create_hosted_key(*, name, role, owner, factory=PostgresWorkspace):
    """Bootstrap through the CLI; never expose this operation as an HTTP route."""
    from tpllm.artifacts.store import ArtifactStore
    from tpllm.workflows.security_manager import SecurityManager
    with factory(os.environ["TPLLM_DATABASE_URL"], workspace_id()) as store:
        with tempfile.TemporaryDirectory(prefix="tpllm-key-") as temporary:
            root = Path(temporary)
            restore_workspace(store.payload, root)
            key, raw_key = SecurityManager(ArtifactStore(root)).create_api_key(name=name, role=role, owner=owner)
            store.save(root)
            return key, raw_key


def process_request(scope, body, *, factory=PostgresWorkspace, cron=False):
    # Import inside invocation: the dashboard's local default app must never
    # become the hosted request handler or hold a cross-request data cache.
    from tpllm.dashboard import create_app

    with factory(os.environ["TPLLM_DATABASE_URL"], workspace_id()) as store:
        with tempfile.TemporaryDirectory(prefix="tpllm-request-") as temporary:
            root = Path(temporary)
            restore_workspace(store.payload, root)
            cfg = TPLLMConfig.from_env(data_dir=root)
            app = create_app(cfg, checkpoint=lambda: store.save(root), collect_in_background=False)
            try:
                if cron:
                    from tpllm.product_flow import cycle
                    workspace = app.state.product_space("live")
                    result = cycle(workspace)
                    response = JSONResponse({"status": "ok", "result": result})
                    messages = asyncio.run(invoke(response, scope, body))
                else:
                    messages = asyncio.run(invoke(app, scope, body))
                # Never send success until the durable snapshot is committed.
                store.save(root)
                return messages
            finally:
                for workspace in list(app.state.product_workspaces.values()):
                    if not workspace.closed:
                        workspace.closed = True
                        workspace.db.close()


def create_hosted_app(*, factory=PostgresWorkspace):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/health")
    def health():
        configured = bool(os.getenv("TPLLM_DATABASE_URL") and os.getenv("TPLLM_WORKSPACE_ID"))
        return JSONResponse({"status": "ok" if configured else "unconfigured", "storage": "supabase"}, status_code=200 if configured else 503)

    @app.get("/")
    def home():
        return Response((WEB_ROOT / "index.html").read_text(), media_type="text/html")

    app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def dispatch(path: str, request: Request):
        cron = path == "api/cron/collect"
        if cron:
            secret = os.getenv("CRON_SECRET", "")
            if not secret or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            if request.method != "GET":
                return JSONResponse({"detail": "Method not allowed"}, status_code=405)
        if not os.getenv("TPLLM_DATABASE_URL") or not os.getenv("TPLLM_WORKSPACE_ID"):
            return JSONResponse({"detail": "Hosted storage is not configured."}, status_code=503)
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > MAX_REQUEST_BYTES:
                return JSONResponse({"detail": "Request is too large."}, status_code=413)
        try:
            messages = await run_in_threadpool(process_request, dict(request.scope), bytes(body), factory=factory, cron=cron)
            start = next(m for m in messages if m["type"] == "http.response.start")
            content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
            response = Response(content, status_code=start["status"])
            response.raw_headers = start["headers"]
            return response
        except WorkspaceBusy:
            return JSONResponse({"detail": "Workspace is busy. Please retry shortly."}, status_code=503, headers={"Retry-After": "3"})
        except Exception as exc:
            # Connection strings, source text and provider errors stay out of
            # browser responses and logs, including exception tracebacks.
            logger.error("Hosted request failed: %s", type(exc).__name__)
            return JSONResponse({"detail": "Workspace operation failed; no success was confirmed."}, status_code=503)

    return app
