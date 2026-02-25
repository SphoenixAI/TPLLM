"""Tracking and workflow tools — meeting gate, dependency scanning, risk engine."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import LLTPMConfig
from lltpm.workflows.meeting_gate import MeetingGate, MeetingProposal
from lltpm.workflows.dependency_tracker import DependencyTracker
from lltpm.workflows.risk_engine import RiskEngine
from lltpm.workflows.daily_cadence import DailyCadence
from lltpm.workflows.weekly_planner import WeeklyPlanner
from lltpm.workflows.bug_triage import BugTriageEngine


def get_tracking_tools(store: ArtifactStore, config: LLTPMConfig) -> list[dict[str, Any]]:
    gate = MeetingGate()
    dep_tracker = DependencyTracker(store, config.stale_ticket_threshold_hours)
    risk_engine = RiskEngine(store)
    daily = DailyCadence(store, config.stale_ticket_threshold_hours)
    weekly = WeeklyPlanner()
    bugs = BugTriageEngine(store)

    def evaluate_meeting(**kwargs) -> str:
        proposal = MeetingProposal(
            topic=kwargs.get("topic", ""),
            reason=kwargs.get("reason", ""),
            decision_owner=kwargs.get("decision_owner", ""),
            options_documented=kwargs.get("options_documented", False),
            pre_read_available=kwargs.get("pre_read_available", False),
            decide_by_deadline=kwargs.get("decide_by_deadline", ""),
        )
        result = gate.evaluate(proposal)
        return json.dumps({
            "verdict": result.verdict.value,
            "failures": result.failures,
            "async_alternative": result.async_alternative,
        })

    def run_daily_brief(program_id: str) -> str:
        brief = daily.run(program_id)
        return brief.render()

    def scan_dependencies(program_id: str) -> str:
        alerts = dep_tracker.scan(program_id)
        if not alerts:
            return "No dependency drift detected."
        lines = []
        for a in alerts:
            lines.append(f"[{a.severity}] {a.title}: {a.detail}\n  Action: {a.suggested_action}")
        return "\n\n".join(lines)

    def scan_risks(program_id: str) -> str:
        signals = risk_engine.scan(program_id)
        if not signals:
            return "No risk signals detected."
        lines = []
        for s in signals:
            lines.append(f"[{s.severity}] {s.title}: {s.detail}\n  Action: {s.recommended_action}")
        return "\n\n".join(lines)

    def get_today_focus() -> str:
        focus = weekly.get_today_focus()
        lines = [f"**{focus['theme']}**"]
        for f in focus["focus"]:
            lines.append(f"- {f}")
        return "\n".join(lines)

    def generate_weekly_plan(program_id: str, weekly_goals: list[str] | None = None, carryover: list[str] | None = None) -> str:
        plan = weekly.generate(program_id, weekly_goals, carryover)
        return plan.render()

    def add_bug(program_id: str, bug_id: str, title: str, severity: str = "P2", owner: str = "", component: str = "", description: str = "") -> str:
        bug = bugs.add_bug(program_id, bug_id, title, severity, owner, component, description)
        return json.dumps(bug.model_dump(), default=str)

    def get_bug_summary(program_id: str) -> str:
        return bugs.get_summary(program_id)

    def check_sla_violations(program_id: str) -> str:
        violations = bugs.check_sla_violations(program_id)
        if not violations:
            return "No SLA violations."
        lines = []
        for v in violations:
            lines.append(f"[{v.severity}] {v.title} — {v.days_open}d open (SLA: {v.sla_days}d) — Owner: {v.owner}")
        return "\n".join(lines)

    return [
        {
            "name": "evaluate_meeting",
            "description": (
                "Evaluate whether a proposed meeting should happen or be replaced "
                "with an async alternative. The meeting gate enforces: decision owner, "
                "options documented, pre-read available, decide-by deadline."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "What the meeting is about"},
                    "reason": {"type": "string", "description": "Why synchronous time is needed"},
                    "decision_owner": {"type": "string", "description": "Who will make the decision"},
                    "options_documented": {"type": "boolean", "description": "Are options/tradeoffs written?"},
                    "pre_read_available": {"type": "boolean", "description": "Is a pre-read doc available?"},
                    "decide_by_deadline": {"type": "string", "description": "Deadline for the decision"},
                },
                "required": ["topic", "reason"],
            },
            "executor": evaluate_meeting,
        },
        {
            "name": "run_daily_brief",
            "description": "Run the full daily TPM cadence: aggregate pulse, check dependencies, scan risks, recommend actions.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": run_daily_brief,
        },
        {
            "name": "scan_dependencies",
            "description": "Scan for dependency drift: stale milestones, overdue dependencies, unowned items.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": scan_dependencies,
        },
        {
            "name": "scan_risks",
            "description": "Scan for risk signals: schedule compression, unmitigated risks, dependency clustering, approaching deadlines.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": scan_risks,
        },
        {
            "name": "get_today_focus",
            "description": "Get the daily theme and focus areas based on the weekly cadence (e.g. Monday=Plan Integrity, Wednesday=Quality).",
            "input_schema": {"type": "object", "properties": {}},
            "executor": get_today_focus,
        },
        {
            "name": "generate_weekly_plan",
            "description": "Generate a weekly plan with daily themes, goals, and carryover items.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "weekly_goals": {"type": "array", "items": {"type": "string"}},
                    "carryover": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id"],
            },
            "executor": generate_weekly_plan,
        },
        {
            "name": "add_bug",
            "description": "Add a bug to the bug dashboard.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "bug_id": {"type": "string"},
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                    "owner": {"type": "string"},
                    "component": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["program_id", "bug_id", "title"],
            },
            "executor": add_bug,
        },
        {
            "name": "get_bug_summary",
            "description": "Get the bug dashboard summary with burn-down and stop-ship bugs.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_bug_summary,
        },
        {
            "name": "check_sla_violations",
            "description": "Check for bugs that have exceeded their SLA based on severity.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": check_sla_violations,
        },
    ]
