"""Analytics tools — program pulse, health metrics, impact simulation."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.update_aggregator import UpdateAggregator


def get_analytics_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    aggregator = UpdateAggregator(store)

    def get_program_pulse(program_id: str) -> str:
        pulse = aggregator.aggregate(program_id)
        return pulse.summary()

    def simulate_slip(program_id: str, milestone_name: str, slip_days: int) -> str:
        """Simulate the impact of a milestone slipping by N days."""
        from tpllm.models.milestone import MilestonePlan, MilestoneStatus
        plan = store.load(program_id, "milestone_plan", MilestonePlan)
        if plan is None:
            return "No milestone plan found."

        target = None
        for m in plan.milestones:
            if m.name.lower() == milestone_name.lower() or m.id == milestone_name:
                target = m
                break
        if target is None:
            return f"Milestone '{milestone_name}' not found."

        if target.target_date is None:
            return f"Milestone '{target.name}' has no target date."

        from datetime import timedelta
        new_date = target.target_date + timedelta(days=slip_days)

        # Find downstream milestones that depend on this one
        affected = []
        for m in plan.milestones:
            if target.id in m.dependencies and m.target_date:
                if m.target_date < new_date:
                    affected.append(m)

        lines = [
            f"## Slip Simulation: {target.name}",
            f"Original date: {target.target_date}",
            f"Slipped date: {new_date} (+{slip_days} days)",
        ]
        if affected:
            lines.append(f"\n**Downstream impact**: {len(affected)} milestones affected:")
            for m in affected:
                lines.append(f"- {m.name} (due {m.target_date}) — would need to move")
        else:
            lines.append("\nNo downstream milestones directly affected.")

        return "\n".join(lines)

    return [
        {
            "name": "get_program_pulse",
            "description": (
                "Get the aggregated program pulse: milestone progress, RAID summary, "
                "action item status, and overall health signal."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_program_pulse,
        },
        {
            "name": "simulate_slip",
            "description": (
                "Simulate the impact of a milestone slipping by N days. "
                "Shows which downstream milestones would be affected."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "milestone_name": {"type": "string", "description": "Name or ID of the milestone"},
                    "slip_days": {"type": "integer", "description": "Number of days to slip"},
                },
                "required": ["program_id", "milestone_name", "slip_days"],
            },
            "executor": simulate_slip,
        },
    ]
