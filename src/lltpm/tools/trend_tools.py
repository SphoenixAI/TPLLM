"""Tool definitions for historical trend analysis."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.trend_analyzer import TrendAnalyzer


def get_trend_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    """Return tool definitions for trend analysis."""
    analyzer = TrendAnalyzer(store)

    def record_snapshot(program_id: str) -> str:
        snap = analyzer.record_health_snapshot(program_id)
        return json.dumps({
            "program_id": snap.program_id,
            "health": snap.health,
            "milestone_completion": f"{snap.milestone_completion:.0%}",
            "open_risks": snap.open_risks,
            "timestamp": snap.timestamp.isoformat(),
        })

    def record_velocity(
        program_id: str,
        period_start: str,
        period_end: str,
        milestones_completed: int = 0,
        milestones_planned: int = 0,
        items_closed: int = 0,
        items_opened: int = 0,
    ) -> str:
        point = analyzer.record_velocity(
            program_id,
            date.fromisoformat(period_start),
            date.fromisoformat(period_end),
            milestones_completed, milestones_planned,
            items_closed, items_opened,
        )
        return json.dumps({
            "completion_rate": f"{point.completion_rate:.0%}",
            "burn_rate": point.burn_rate,
        })

    def get_trend_report(program_id: str) -> str:
        return analyzer.get_trend_summary(program_id)

    def analyze_trends(program_id: str) -> str:
        report = analyzer.analyze_trends(program_id)
        return json.dumps({
            "health_trend": report.health_trend.value,
            "velocity_trend": report.velocity_trend.value,
            "risk_trend": report.risk_trend.value,
            # Program-level time-to-reply. The `degrading_people` list
            # this replaced (Commit 41) handed the model a ranked list of
            # colleagues to comment on.
            "response_latency_trend": report.response_latency_trend.value,
            "median_response_hours": report.median_response_hours,
            "warnings": report.warnings,
            "recommendations": report.recommendations,
        })

    return [
        {
            "name": "record_health_snapshot",
            "description": "Record a point-in-time health snapshot of a program for trend tracking",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": record_snapshot,
        },
        {
            "name": "record_velocity",
            "description": "Record a velocity measurement for a time period",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "period_start": {"type": "string", "description": "ISO date"},
                    "period_end": {"type": "string", "description": "ISO date"},
                    "milestones_completed": {"type": "integer"},
                    "milestones_planned": {"type": "integer"},
                    "items_closed": {"type": "integer"},
                    "items_opened": {"type": "integer"},
                },
                "required": ["program_id", "period_start", "period_end"],
            },
            "executor": record_velocity,
        },
        {
            "name": "get_trend_report",
            "description": "Get a comprehensive trend analysis for a program — health, velocity, risk, and engagement trajectories",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_trend_report,
        },
        {
            "name": "analyze_trends",
            "description": "Analyze trends and get structured trend data with warnings and recommendations",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": analyze_trends,
        },
    ]
