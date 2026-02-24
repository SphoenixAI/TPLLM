"""Dashboard API — role-filtered endpoints for program visibility.

FastAPI-based REST API that serves:
- Program health overview
- Role-filtered views (admin, manager, engineer, exec)
- Document generation endpoints
- Risk prediction data
- Engagement and delegation summaries

Run with: uvicorn lltpm.dashboard:app --reload
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import LLTPMConfig


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

    app = FastAPI(
        title="LLTPM Dashboard",
        description="AI Technical Program Manager — Program Dashboard",
        version="0.1.0",
    )

    # ── Health ────────────────────────────────────────────

    @app.get("/health")
    def health():
        return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

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
        fmt: str = Query(default="html", regex="^(html|markdown)$"),
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

    return app


# Default app instance for uvicorn
try:
    app = create_app()
except Exception:
    # FastAPI not installed — app won't work but module can still import
    app = None
