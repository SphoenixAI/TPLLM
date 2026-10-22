"""Tool definitions for cross-program intelligence."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.cross_program_intel import CrossProgramIntel


def get_portfolio_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    """Return tool definitions for cross-program intelligence."""
    intel = CrossProgramIntel(store)

    def get_portfolio_view() -> str:
        view = intel.generate_portfolio_view()
        return json.dumps({
            "overall_health": view.overall_health.value,
            "program_count": len(view.programs),
            "programs": [{
                "id": p.program_id, "name": p.program_name,
                "health": p.health,
                "progress": f"{p.milestone_progress:.0%}",
                "at_risk": p.at_risk_milestones,
                "open_risks": p.open_risks,
                "pending_decisions": p.pending_decisions,
            } for p in view.programs],
            "resource_conflicts": len(view.resource_conflicts),
            "shared_dependencies": len(view.shared_dependencies),
            "cross_program_risks": len(view.cross_program_risks),
        })

    def get_portfolio_summary() -> str:
        return intel.get_portfolio_summary()

    def get_resource_conflicts() -> str:
        conflicts = intel.get_resource_conflicts()
        return json.dumps([{
            "person": c.person,
            "programs": c.programs,
            "total_items": c.total_active_items,
            "per_program": c.per_program,
            "severity": c.severity,
            "recommendation": c.recommendation,
        } for c in conflicts])

    def get_shared_dependencies() -> str:
        deps = intel.get_shared_dependencies()
        return json.dumps([{
            "title": d.title,
            "upstream_team": d.upstream_team,
            "affected_programs": d.affected_programs,
            "impact": d.impact,
            "recommendation": d.recommendation,
        } for d in deps])

    return [
        {
            "name": "get_portfolio_view",
            "description": "Get aggregate health view across all programs — resource conflicts, shared dependencies, cross-program risks",
            "input_schema": {"type": "object", "properties": {}},
            "executor": lambda: get_portfolio_view(),
        },
        {
            "name": "get_portfolio_summary",
            "description": "Get a human-readable summary of portfolio health and cross-program intelligence",
            "input_schema": {"type": "object", "properties": {}},
            "executor": lambda: get_portfolio_summary(),
        },
        {
            "name": "get_resource_conflicts",
            "description": "Detect people overloaded across multiple programs",
            "input_schema": {"type": "object", "properties": {}},
            "executor": lambda: get_resource_conflicts(),
        },
        {
            "name": "get_shared_dependencies",
            "description": "Find dependencies shared across multiple programs that create systemic risk",
            "input_schema": {"type": "object", "properties": {}},
            "executor": lambda: get_shared_dependencies(),
        },
    ]
