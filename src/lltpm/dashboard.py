"""Dashboard API — role-filtered endpoints for program visibility.

FastAPI-based REST API that serves:
- Program health overview
- Role-filtered views (admin, manager, engineer, exec)
- Document generation endpoints
- Risk prediction data
- Engagement and delegation summaries
- JWT authentication with RBAC enforcement

Run with: uvicorn lltpm.dashboard:app --reload
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from fastapi import Depends, FastAPI, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import LLTPMConfig
from lltpm.models.security import JWTClaims, Permission, Role
from lltpm.workflows.security_manager import SecurityManager


def create_app(config: LLTPMConfig | None = None) -> Any:
    """Create the FastAPI dashboard application."""
    if not HAS_FASTAPI:
        raise ImportError(
            "FastAPI is required for the dashboard. "
            "Install with: pip install fastapi uvicorn"
        )

    cfg = config or LLTPMConfig.from_env()
    cfg.ensure_data_dir()
    store = ArtifactStore(cfg.data_dir)
    security = SecurityManager(store)
    bearer_scheme = HTTPBearer(auto_error=False)

    # ── Auth helpers ──────────────────────────────────────

    async def get_current_user(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> JWTClaims | None:
        """Extract and validate JWT from Authorization header.

        Returns None if no token provided (allows public endpoints).
        Raises 401 if token is invalid.
        """
        if credentials is None:
            return None

        claims = security.validate_jwt(credentials.credentials)
        if claims is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return claims

    def require_auth(claims: JWTClaims | None) -> JWTClaims:
        """Ensure user is authenticated."""
        if claims is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return claims

    def require_permission(claims: JWTClaims, permission: Permission, program_id: str = "") -> None:
        """Check RBAC permission or raise 403."""
        if not security.check_permission(claims.role, permission, program_id, claims.scopes):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions: {permission.value} required",
            )

    app = FastAPI(
        title="LLTPM Dashboard",
        description="AI Technical Program Manager — Program Dashboard",
        version="0.1.0",
    )

    # ── Health (public) ───────────────────────────────────

    @app.get("/health")
    def health():
        return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

    # ── Dashboard frontend (public) ──────────────────────

    @app.get("/", response_class=HTMLResponse)
    def dashboard_home():
        """Serve the main dashboard HTML frontend."""
        return HTMLResponse(content=_DASHBOARD_HTML)

    # ── Auth endpoints ────────────────────────────────────

    @app.post("/api/auth/token")
    async def login(request: Request):
        """Exchange an API key for a JWT token pair.

        Body: {"api_key": "lltpm_..."}
        Returns: {"access_token": "...", "refresh_token": "...", "expires_in": ...}
        """
        body = await request.json()
        api_key = body.get("api_key", "")
        if not api_key:
            raise HTTPException(400, "api_key is required")

        ip = request.client.host if request.client else ""
        token_pair = security.authenticate_api_key_to_jwt(api_key, ip_address=ip)
        if not token_pair:
            raise HTTPException(401, "Invalid API key")

        return token_pair.model_dump()

    @app.post("/api/auth/refresh")
    async def refresh_token(request: Request):
        """Exchange a refresh token for a new token pair.

        Body: {"refresh_token": "..."}
        """
        body = await request.json()
        refresh = body.get("refresh_token", "")
        if not refresh:
            raise HTTPException(400, "refresh_token is required")

        token_pair = security.refresh_access_token(refresh)
        if not token_pair:
            raise HTTPException(401, "Invalid or expired refresh token")

        return token_pair.model_dump()

    @app.get("/api/auth/me")
    async def get_current_user_info(
        claims: JWTClaims | None = Depends(get_current_user),
    ):
        """Get current authenticated user info."""
        user = require_auth(claims)
        return {
            "subject": user.sub,
            "role": user.role.value,
            "scopes": user.scopes,
            "permissions": security.get_role_permissions(user.role),
            "expires_at": user.exp.isoformat(),
        }

    # ── Programs ──────────────────────────────────────────

    @app.get("/api/programs")
    def list_programs():
        """List all tracked programs."""
        programs = store.list_programs()
        result = []
        for pid in programs:
            artifacts = store.list_artifacts(pid)
            from lltpm.models.program import Program
            program = store.load(pid, "program", Program)
            result.append({
                "id": pid,
                "name": program.name if program else pid,
                "health": getattr(program, "health", "UNKNOWN") if program else "UNKNOWN",
                "artifact_count": len(artifacts),
                "artifacts": artifacts,
            })
        return {"programs": result}

    @app.get("/api/programs/{program_id}")
    def get_program(program_id: str):
        """Get program details."""
        from lltpm.models.program import Program
        program = store.load(program_id, "program", Program)
        if not program:
            raise HTTPException(404, f"Program {program_id} not found")
        return program.model_dump()

    # ── Role-filtered views ───────────────────────────────

    @app.get("/api/programs/{program_id}/view/{role}")
    def get_role_view(program_id: str, role: str):
        """Get a role-filtered view of the program.

        Roles: admin, manager, engineer, exec
        """
        from lltpm.workflows.personalized_briefing import PersonalizedBriefingEngine
        engine = PersonalizedBriefingEngine(store)

        role_map = {
            "admin": "PM",
            "manager": "TECH_LEAD",
            "engineer": "ENGINEER",
            "exec": "EXEC",
        }

        mapped_role = role_map.get(role.lower())
        if not mapped_role:
            raise HTTPException(400, f"Unknown role: {role}. Use: admin, manager, engineer, exec")

        briefing = engine.generate(
            program_id=program_id,
            role=mapped_role,
            person_name=f"{role.title()} user",
        )

        return {
            "role": role,
            "program_id": program_id,
            "briefing": briefing.model_dump() if hasattr(briefing, "model_dump") else str(briefing),
        }

    # ── Status & Milestones ───────────────────────────────

    @app.get("/api/programs/{program_id}/status")
    def get_status(program_id: str):
        """Get latest status update."""
        from lltpm.artifacts.status_update import StatusUpdateGenerator
        gen = StatusUpdateGenerator(store)
        update = gen.get_latest(program_id)
        if not update:
            return {"message": "No status updates"}
        return update.model_dump()

    @app.get("/api/programs/{program_id}/milestones")
    def get_milestones(program_id: str):
        """Get milestone plan with critical path and at-risk items."""
        from lltpm.models.milestone import MilestonePlan
        plan = store.load(program_id, "milestone_plan", MilestonePlan)
        if not plan:
            return {"message": "No milestone plan"}
        return {
            "total": len(plan.milestones),
            "critical_path": len(plan.critical_path),
            "at_risk": len(plan.at_risk),
            "overdue": len(plan.overdue),
            "milestones": [m.model_dump() for m in plan.milestones],
        }

    # ── RAID & Decisions ──────────────────────────────────

    @app.get("/api/programs/{program_id}/raid")
    def get_raid(program_id: str):
        """Get RAID log summary."""
        from lltpm.models.risk import RAIDLog
        raid = store.load(program_id, "raid_log", RAIDLog)
        if not raid:
            return {"message": "No RAID log"}
        return {
            "risks": len(raid.risks),
            "open_risks": len(raid.open_risks),
            "issues": len(raid.issues),
            "open_issues": len(raid.open_issues),
            "dependencies": len(raid.dependencies),
            "open_dependencies": len(raid.open_dependencies),
            "assumptions": len(raid.assumptions),
            "summary": raid.summary(),
        }

    @app.get("/api/programs/{program_id}/decisions")
    def get_decisions(program_id: str):
        """Get decision log."""
        from lltpm.models.decision import DecisionLog
        log = store.load(program_id, "decision_log", DecisionLog)
        if not log:
            return {"message": "No decision log"}
        return {
            "total": len(log.decisions),
            "pending": len(log.pending),
            "summary": log.summary(),
        }

    # ── Delegations & Engagement ──────────────────────────

    @app.get("/api/programs/{program_id}/delegations")
    def get_delegations(program_id: str):
        """Get delegation summary."""
        from lltpm.workflows.delegation_engine import DelegationEngine
        engine = DelegationEngine(store)
        return {"summary": engine.get_summary(program_id)}

    @app.get("/api/programs/{program_id}/engagement")
    def get_engagement(program_id: str):
        """Get engagement profiles."""
        from lltpm.workflows.engagement_tracker import EngagementTracker
        tracker = EngagementTracker(store)
        return {"summary": tracker.get_summary(program_id)}

    # ── Risk Predictions ──────────────────────────────────

    @app.get("/api/programs/{program_id}/predictions")
    def get_predictions(program_id: str, refresh: bool = False):
        """Get risk predictions. Set refresh=true to rerun prediction models."""
        from lltpm.workflows.risk_predictor import RiskPredictor
        predictor = RiskPredictor(store)
        if refresh:
            predictions = predictor.predict_risks(program_id)
        else:
            predictions = predictor.get_predictions(program_id)
        return {
            "count": len(predictions),
            "predictions": [p.model_dump() for p in predictions],
            "summary": predictor.get_prediction_summary(program_id),
        }

    @app.get("/api/programs/{program_id}/evaluations/{person}")
    def get_evaluation(program_id: str, person: str, quarter: str = ""):
        """Get quarterly evaluation for a person."""
        from lltpm.workflows.risk_predictor import RiskPredictor
        predictor = RiskPredictor(store)
        evaluation = predictor.get_evaluation(program_id, person, quarter)
        if not evaluation:
            raise HTTPException(404, f"No evaluation found for {person}")
        return {
            "evaluation": evaluation.model_dump(),
            "rendered": predictor.render_evaluation(evaluation),
        }

    # ── Document Generation ───────────────────────────────

    @app.get("/api/programs/{program_id}/documents")
    def list_documents(program_id: str):
        """List available documents for rendering."""
        from lltpm.workflows.document_generator import DocumentGenerator
        gen = DocumentGenerator(store)
        return {"documents": gen.list_renderable(program_id)}

    @app.get("/api/programs/{program_id}/documents/{doc_type}", response_class=HTMLResponse)
    def render_document(
        program_id: str,
        doc_type: str,
        fmt: str = Query(default="html", pattern="^(html|markdown)$"),
    ):
        """Render a document as HTML or Markdown.

        doc_type: program_brief, status_update, raid_log, decision_log,
                  milestone_plan, launch_checklist, action_tracker,
                  delegation_plan, full_report
        """
        from lltpm.workflows.document_generator import DocumentGenerator
        gen = DocumentGenerator(store)

        renderers = {
            "program_brief": gen.render_program_brief,
            "status_update": gen.render_status_update,
            "raid_log": gen.render_raid_log,
            "decision_log": gen.render_decision_log,
            "milestone_plan": gen.render_milestone_plan,
            "launch_checklist": gen.render_launch_checklist,
            "action_tracker": gen.render_action_tracker,
            "delegation_plan": gen.render_delegation_plan,
            "task_board": gen.render_task_board,
            "full_report": gen.render_full_report,
        }

        renderer = renderers.get(doc_type)
        if not renderer:
            raise HTTPException(400, f"Unknown doc type: {doc_type}. Options: {list(renderers.keys())}")

        result = renderer(program_id, fmt)
        if fmt == "html":
            return HTMLResponse(content=result)
        return JSONResponse(content={"content": result, "format": "markdown"})

    # ── Memory & Context ──────────────────────────────────

    @app.get("/api/programs/{program_id}/memory")
    def get_memory(program_id: str):
        """Get memory state summary."""
        from lltpm.workflows.persistent_memory import PersistentMemoryEngine
        engine = PersistentMemoryEngine(store)
        return {
            "summary": engine.get_summary(program_id),
            "context": engine.reconstruct_context(program_id),
        }

    # ── Webhooks & Events ─────────────────────────────────

    @app.get("/api/programs/{program_id}/events")
    def get_events(program_id: str, source: str = "", priority: str = ""):
        """Get pending webhook events."""
        from lltpm.workflows.webhook_processor import WebhookProcessor
        processor = WebhookProcessor(store)
        return {"summary": processor.get_summary(program_id)}

    # ── Task Board ─────────────────────────────────────────

    @app.get("/api/programs/{program_id}/tasks")
    def get_tasks(
        program_id: str,
        status: str = "",
        assignee: str = "",
        sprint_id: str = "",
    ):
        """Get tasks from the task board, with optional filters."""
        from lltpm.workflows.task_board_engine import TaskBoardEngine
        engine = TaskBoardEngine(store)
        board = engine._load(program_id)

        tasks = board.tasks
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        if assignee:
            tasks = [t for t in tasks if t.assignee == assignee]
        if sprint_id:
            tasks = [t for t in tasks if t.sprint_id == sprint_id]

        return {
            "total": len(tasks),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "status": t.status.value,
                    "priority": t.priority.value,
                    "assignee": t.assignee,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "effort_hours": t.effort_hours,
                    "source": t.source.value,
                    "sprint_id": t.sprint_id,
                    "is_overdue": t.is_overdue,
                    "tags": t.tags,
                    "confidence": t.confidence,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tasks
            ],
        }

    @app.get("/api/programs/{program_id}/tasks/board")
    def get_task_board_view(program_id: str, sprint_id: str = ""):
        """Get the kanban board view — tasks grouped by status."""
        from lltpm.workflows.task_board_engine import TaskBoardEngine
        engine = TaskBoardEngine(store)
        return {"board": engine.get_board(program_id, sprint_id)}

    @app.get("/api/programs/{program_id}/tasks/workload")
    def get_task_workload(program_id: str):
        """Get workload analysis across team members."""
        from lltpm.workflows.task_board_engine import TaskBoardEngine
        engine = TaskBoardEngine(store)
        workload = engine.get_workload(program_id)
        if not workload:
            return {"message": "No tasks assigned yet."}
        return {"workload": workload}

    @app.get("/api/programs/{program_id}/sprints")
    def get_sprints(program_id: str):
        """List all sprints for a program."""
        from lltpm.workflows.task_board_engine import TaskBoardEngine
        engine = TaskBoardEngine(store)
        board = engine._load(program_id)

        return {
            "sprints": [
                {
                    "id": s.id,
                    "name": s.name,
                    "goal": s.goal,
                    "start_date": s.start_date.isoformat(),
                    "end_date": s.end_date.isoformat(),
                    "is_active": s.is_active,
                    "days_remaining": s.days_remaining,
                    "team_capacity_hours": s.team_capacity_hours,
                    "committed_hours": s.committed_hours,
                    "utilization": s.utilization,
                    "is_over_capacity": s.is_over_capacity,
                }
                for s in board.sprints
            ],
            "active_sprint": board.active_sprint.id if board.active_sprint else None,
        }

    @app.get("/api/programs/{program_id}/sprints/{sprint_id}/metrics")
    def get_sprint_metrics_endpoint(program_id: str, sprint_id: str):
        """Get sprint health metrics — completion rate, velocity, on-track."""
        from lltpm.workflows.task_board_engine import TaskBoardEngine
        engine = TaskBoardEngine(store)
        return engine.get_sprint_metrics(program_id, sprint_id)

    # ── Agent Orchestrator ────────────────────────────────

    @app.get("/api/agents")
    def list_agents():
        """List configured sub-agents."""
        from lltpm.workflows.agent_orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()
        return {"agents": orch.list_agents()}

    @app.get("/api/agents/deployment-config")
    def deployment_config():
        """Get Agent SDK deployment configuration."""
        from lltpm.workflows.agent_orchestrator import AgentOrchestrator
        orch = AgentOrchestrator()
        return orch.get_deployment_config()

    # ── Security & Audit (auth required) ─────────────────

    @app.get("/api/security/audit")
    async def get_audit_log(
        actor: str = "",
        limit: int = Query(default=50, le=500),
        claims: JWTClaims | None = Depends(get_current_user),
    ):
        """Get audit log (requires AUDIT_READ permission)."""
        user = require_auth(claims)
        require_permission(user, Permission.AUDIT_READ)
        entries = security.get_audit_log(actor=actor, limit=limit)
        return {
            "entries": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp.isoformat(),
                    "action": e.action.value,
                    "actor": e.actor,
                    "resource_type": e.resource_type,
                    "resource_id": e.resource_id,
                    "detail": e.detail,
                    "success": e.success,
                }
                for e in entries
            ],
            "count": len(entries),
        }

    @app.get("/api/security/keys")
    async def list_api_keys(
        claims: JWTClaims | None = Depends(get_current_user),
    ):
        """List API keys (requires DASHBOARD_ADMIN permission)."""
        user = require_auth(claims)
        require_permission(user, Permission.DASHBOARD_ADMIN)
        keys = security.list_api_keys()
        return {
            "keys": [
                {
                    "id": k.id,
                    "name": k.name,
                    "role": k.role.value,
                    "owner": k.owner,
                    "key_prefix": k.key_prefix,
                    "created_at": k.created_at.isoformat(),
                    "is_valid": k.is_valid,
                    "revoked": k.revoked,
                }
                for k in keys
            ],
        }

    return app


# ── Dashboard HTML Frontend ────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LLTPM Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     background:#0f172a;color:#e2e8f0;line-height:1.5}
.container{max-width:1200px;margin:0 auto;padding:20px}
header{display:flex;justify-content:space-between;align-items:center;padding:16px 0;
       border-bottom:1px solid #1e293b;margin-bottom:24px}
header h1{font-size:20px;color:#f8fafc}
header .health{padding:4px 12px;border-radius:6px;font-size:13px;font-weight:600}
.green{background:#166534;color:#dcfce7}
.yellow{background:#854d0e;color:#fef9c3}
.red{background:#991b1b;color:#fee2e2}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:24px}
.card{background:#1e293b;border-radius:10px;padding:20px;border:1px solid #334155}
.card h2{font-size:14px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px}
.card .value{font-size:28px;font-weight:700;color:#f8fafc}
.card .sub{font-size:12px;color:#64748b;margin-top:4px}
.section{background:#1e293b;border-radius:10px;padding:20px;border:1px solid #334155;margin-bottom:16px}
.section h2{font-size:16px;margin-bottom:12px;color:#f1f5f9}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 12px;border-bottom:1px solid #334155;color:#94a3b8;font-weight:500}
td{padding:8px 12px;border-bottom:1px solid #1e293b}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge.CRITICAL{background:#7f1d1d;color:#fecaca}
.badge.HIGH{background:#991b1b;color:#fee2e2}
.badge.MEDIUM{background:#854d0e;color:#fef9c3}
.badge.LOW{background:#1e3a5f;color:#bfdbfe}
.badge.DONE{background:#166534;color:#dcfce7}
.badge.IN_PROGRESS{background:#1e40af;color:#dbeafe}
.badge.BLOCKED{background:#7f1d1d;color:#fecaca}
.badge.TODO{background:#475569;color:#e2e8f0}
.badge.BACKLOG{background:#334155;color:#94a3b8}
.badge.IN_REVIEW{background:#854d0e;color:#fef9c3}
.empty{color:#475569;font-style:italic;padding:20px;text-align:center}
.tabs{display:flex;gap:8px;margin-bottom:16px}
.tab{padding:6px 16px;border-radius:6px;cursor:pointer;font-size:13px;
     background:#334155;color:#94a3b8;border:none}
.tab.active{background:#2563eb;color:#fff}
#alerts .alert{padding:8px 12px;border-radius:6px;margin-bottom:8px;font-size:13px}
.alert.critical{background:#450a0a;border:1px solid #7f1d1d}
.alert.high{background:#451a03;border:1px solid #854d0e}
.alert.medium{background:#172554;border:1px solid #1e40af}
select{background:#334155;color:#e2e8f0;border:1px solid #475569;border-radius:6px;
       padding:6px 12px;font-size:13px}
.refresh-btn{background:#2563eb;color:#fff;border:none;padding:6px 16px;
             border-radius:6px;cursor:pointer;font-size:13px}
.refresh-btn:hover{background:#1d4ed8}
</style>
</head>
<body>
<div class="container">
<header>
  <h1>LLTPM Dashboard</h1>
  <div>
    <select id="program-select" onchange="loadProgram()"><option value="">Select program...</option></select>
    <button class="refresh-btn" onclick="loadProgram()">Refresh</button>
  </div>
</header>

<div class="grid" id="stats">
  <div class="card"><h2>Open Tasks</h2><div class="value" id="stat-open">—</div><div class="sub" id="stat-open-sub"></div></div>
  <div class="card"><h2>Overdue</h2><div class="value" id="stat-overdue">—</div><div class="sub" id="stat-overdue-sub"></div></div>
  <div class="card"><h2>Blocked</h2><div class="value" id="stat-blocked">—</div></div>
  <div class="card"><h2>Sprint Progress</h2><div class="value" id="stat-sprint">—</div><div class="sub" id="stat-sprint-sub"></div></div>
</div>

<div id="alerts" class="section" style="display:none">
  <h2>Alerts</h2>
  <div id="alerts-list"></div>
</div>

<div class="section">
  <h2>Task Board</h2>
  <div class="tabs">
    <button class="tab active" onclick="filterTasks('')" data-f="">All</button>
    <button class="tab" onclick="filterTasks('TODO')" data-f="TODO">To Do</button>
    <button class="tab" onclick="filterTasks('IN_PROGRESS')" data-f="IN_PROGRESS">In Progress</button>
    <button class="tab" onclick="filterTasks('BLOCKED')" data-f="BLOCKED">Blocked</button>
    <button class="tab" onclick="filterTasks('DONE')" data-f="DONE">Done</button>
  </div>
  <table>
    <thead><tr><th>ID</th><th>Title</th><th>Priority</th><th>Status</th><th>Assignee</th><th>Due</th></tr></thead>
    <tbody id="task-table"><tr><td colspan="6" class="empty">Select a program to view tasks</td></tr></tbody>
  </table>
</div>

<div class="grid">
  <div class="section">
    <h2>Workload</h2>
    <table>
      <thead><tr><th>Person</th><th>Tasks</th><th>Health</th></tr></thead>
      <tbody id="workload-table"><tr><td colspan="3" class="empty">—</td></tr></tbody>
    </table>
  </div>
  <div class="section">
    <h2>Sprints</h2>
    <div id="sprint-info" class="empty">—</div>
  </div>
</div>

<div class="section">
  <h2>RAID Summary</h2>
  <div id="raid-summary" class="empty">—</div>
</div>
</div>

<script>
let allTasks = [];
const api = path => fetch(path).then(r => r.json());

async function init() {
  const data = await api('/api/programs');
  const sel = document.getElementById('program-select');
  (data.programs || []).forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.id; opt.textContent = p.name || p.id;
    sel.appendChild(opt);
  });
}

async function loadProgram() {
  const pid = document.getElementById('program-select').value;
  if (!pid) return;

  const [tasks, workload, sprints, raid] = await Promise.all([
    api(`/api/programs/${pid}/tasks`),
    api(`/api/programs/${pid}/tasks/workload`),
    api(`/api/programs/${pid}/sprints`),
    api(`/api/programs/${pid}/raid`),
  ]);

  allTasks = tasks.tasks || [];
  const open = allTasks.filter(t => !['DONE','CANCELLED'].includes(t.status));
  const overdue = allTasks.filter(t => t.is_overdue);
  const blocked = allTasks.filter(t => t.status === 'BLOCKED');

  document.getElementById('stat-open').textContent = open.length;
  document.getElementById('stat-open-sub').textContent = `${allTasks.length} total`;
  document.getElementById('stat-overdue').textContent = overdue.length;
  document.getElementById('stat-overdue-sub').textContent = overdue.length > 0 ? 'Needs attention' : 'All clear';
  document.getElementById('stat-blocked').textContent = blocked.length;

  // Sprint
  const active = (sprints.sprints || []).find(s => s.is_active);
  if (active) {
    const metrics = await api(`/api/programs/${pid}/sprints/${active.id}/metrics`);
    const pct = Math.round((metrics.completion_rate || 0) * 100);
    document.getElementById('stat-sprint').textContent = pct + '%';
    document.getElementById('stat-sprint-sub').textContent =
      `${active.name} — ${metrics.days_remaining}d left`;
    document.getElementById('sprint-info').innerHTML =
      `<strong>${active.name}</strong><br>` +
      `Done: ${metrics.done}/${metrics.total_tasks} | ` +
      `Blocked: ${metrics.blocked} | ` +
      `${metrics.on_track ? '<span class="badge DONE">On Track</span>' : '<span class="badge CRITICAL">At Risk</span>'}`;
  } else {
    document.getElementById('stat-sprint').textContent = '—';
    document.getElementById('stat-sprint-sub').textContent = 'No active sprint';
    document.getElementById('sprint-info').innerHTML = '<span class="empty">No active sprint</span>';
  }

  // Alerts (overdue + blocked)
  const alertsDiv = document.getElementById('alerts');
  const alertsList = document.getElementById('alerts-list');
  const alertItems = [];
  overdue.forEach(t => alertItems.push({sev:'critical',msg:`Overdue: ${t.title} (${t.assignee||'unassigned'})`}));
  blocked.forEach(t => alertItems.push({sev:'high',msg:`Blocked: ${t.title} (${t.assignee||'unassigned'})`}));
  if (alertItems.length) {
    alertsDiv.style.display = 'block';
    alertsList.innerHTML = alertItems.map(a =>
      `<div class="alert ${a.sev}">${a.msg}</div>`
    ).join('');
  } else { alertsDiv.style.display = 'none'; }

  filterTasks('');

  // Workload
  const wt = document.getElementById('workload-table');
  const wl = workload.workload || {};
  if (Object.keys(wl).length) {
    wt.innerHTML = Object.entries(wl).map(([p, d]) =>
      `<tr><td>${p}</td><td>${d.total_tasks}</td><td><span class="badge ${
        d.health==='healthy'?'DONE':d.health==='heavy'?'MEDIUM':d.health==='at_risk'?'HIGH':'CRITICAL'
      }">${d.health.toUpperCase()}</span></td></tr>`
    ).join('');
  } else { wt.innerHTML = '<tr><td colspan="3" class="empty">No assigned tasks</td></tr>'; }

  // RAID
  const rs = document.getElementById('raid-summary');
  if (raid.message) { rs.textContent = raid.message; }
  else { rs.innerHTML = `Risks: <strong>${raid.open_risks||0}</strong> open | Issues: <strong>${raid.open_issues||0}</strong> | Dependencies: <strong>${raid.open_dependencies||0}</strong>`; }
}

function filterTasks(status) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.f === status));
  const filtered = status ? allTasks.filter(t => t.status === status) : allTasks;
  const tbody = document.getElementById('task-table');
  if (!filtered.length) { tbody.innerHTML = '<tr><td colspan="6" class="empty">No tasks</td></tr>'; return; }
  tbody.innerHTML = filtered.map(t =>
    `<tr><td>${t.id}</td><td>${t.title}</td>` +
    `<td><span class="badge ${t.priority}">${t.priority}</span></td>` +
    `<td><span class="badge ${t.status}">${t.status}</span></td>` +
    `<td>${t.assignee||'—'}</td><td>${t.due_date||'—'}</td></tr>`
  ).join('');
}

init();
</script>
</body>
</html>"""


# Default app instance for uvicorn
try:
    app = create_app()
except Exception:
    # FastAPI not installed — app won't work but module can still import
    app = None
