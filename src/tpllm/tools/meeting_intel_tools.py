"""Meeting intelligence tools — extract and query meeting context."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.workflows.meeting_intelligence import MeetingIntelligenceEngine


def get_meeting_intel_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = MeetingIntelligenceEngine(store)

    def analyze_meeting(
        program_id: str,
        title: str,
        content: str,
        attendees: list[str] | None = None,
        meeting_date: str | None = None,
        source: str = "manual",
    ) -> str:
        intel, prompt = engine.analyze_meeting(
            program_id, title, content, attendees, meeting_date, source,
        )
        return json.dumps({
            "meeting_id": intel.meeting_id,
            "status": "awaiting_analysis",
            "analysis_prompt": prompt,
            "instruction": (
                "Use the analysis prompt above to extract structured intelligence "
                "from this meeting. Then call update_meeting_intelligence with the "
                "resulting JSON to persist the analysis."
            ),
        })

    def ingest_parsed_meeting(
        program_id: str,
        title: str,
        parsed_data: dict[str, Any],
        attendees: list[str] | None = None,
        meeting_date: str | None = None,
        source: str = "ai_tool",
    ) -> str:
        intel = engine.ingest_parsed(
            program_id, title, parsed_data, attendees, meeting_date, source,
        )
        return intel.summary()

    def update_meeting_intelligence(
        program_id: str,
        meeting_id: str,
        analysis: dict[str, Any],
    ) -> str:
        intel = engine.update_from_analysis(program_id, meeting_id, analysis)
        return intel.summary()

    def get_meeting_intelligence(program_id: str, meeting_id: str) -> str:
        intel = engine.get_intelligence(program_id, meeting_id)
        if intel is None:
            return json.dumps({"error": f"No intelligence found for meeting {meeting_id}"})
        return intel.summary()

    def get_meeting_intel_report(program_id: str) -> str:
        report = engine.get_program_report(program_id)
        return report.render()

    def get_all_commitments(program_id: str) -> str:
        commitments = engine.extract_all_commitments(program_id)
        if not commitments:
            return "No commitments extracted from meetings yet."
        lines = [f"# Meeting Commitments ({len(commitments)} total)"]
        for c in commitments:
            due = c.get("due_date") or "TBD"
            lines.append(
                f"- **{c['owner']}**: {c['description']} — "
                f"Due: {due} — From: {c['meeting_title']} ({c['meeting_date']})"
            )
        return "\n".join(lines)

    return [
        {
            "name": "analyze_meeting",
            "description": (
                "Analyze raw meeting notes, transcript, or AI meeting tool output. "
                "Extracts commitments, risks, decisions, dependencies, timelines, "
                "urgency, and value assessment. Returns an analysis prompt for deep "
                "extraction — process it and call update_meeting_intelligence with results."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string", "description": "Meeting title"},
                    "content": {"type": "string", "description": "Raw meeting notes, transcript, or AI notes"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "meeting_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "source": {
                        "type": "string",
                        "description": "Source of the notes (e.g. 'google_meet', 'otter_ai', 'fireflies', 'manual')",
                    },
                },
                "required": ["program_id", "title", "content"],
            },
            "executor": analyze_meeting,
        },
        {
            "name": "ingest_parsed_meeting",
            "description": (
                "Ingest pre-parsed meeting data from an AI meeting tool. "
                "Use this when the meeting tool already provides structured output "
                "(e.g. Otter.ai action items, Fireflies summaries). "
                "Accepts a JSON object with commitments, risks, decisions, etc."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "parsed_data": {
                        "type": "object",
                        "description": "Pre-parsed meeting data with fields: executive_summary, commitments, risks, decisions, dependencies, timelines, open_questions",
                    },
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "meeting_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "source": {"type": "string"},
                },
                "required": ["program_id", "title", "parsed_data"],
            },
            "executor": ingest_parsed_meeting,
        },
        {
            "name": "update_meeting_intelligence",
            "description": (
                "Update a meeting intelligence record with analysis results. "
                "Call this after processing the analysis prompt from analyze_meeting."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "meeting_id": {"type": "string"},
                    "analysis": {
                        "type": "object",
                        "description": "The extracted analysis JSON with: executive_summary, commitments, risks, decisions, dependencies, timelines, open_questions, overall_urgency",
                    },
                },
                "required": ["program_id", "meeting_id", "analysis"],
            },
            "executor": update_meeting_intelligence,
        },
        {
            "name": "get_meeting_intelligence",
            "description": "Get the extracted intelligence for a specific meeting.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "meeting_id": {"type": "string"},
                },
                "required": ["program_id", "meeting_id"],
            },
            "executor": get_meeting_intelligence,
        },
        {
            "name": "get_meeting_intel_report",
            "description": (
                "Get an aggregate intelligence report across all analyzed meetings. "
                "Shows total commitments, risks, decisions, dependencies, and timeline slips."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_meeting_intel_report,
        },
        {
            "name": "get_all_commitments",
            "description": "Get all commitments extracted from meetings, with owners and due dates.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_all_commitments,
        },
    ]
