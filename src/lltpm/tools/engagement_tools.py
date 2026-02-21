"""Engagement, quick response, briefing, and transcript tools."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.engagement_tracker import EngagementTracker
from lltpm.workflows.quick_response import QuickResponseProtocol
from lltpm.workflows.personalized_briefing import PersonalizedBriefingEngine
from lltpm.workflows.transcript_processor import TranscriptProcessor


def get_engagement_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    tracker = EngagementTracker(store)
    qr = QuickResponseProtocol()
    briefings = PersonalizedBriefingEngine(store)
    transcript = TranscriptProcessor()

    # ── Engagement tracking ───────────────────────────────────

    def record_engagement(
        program_id: str,
        person: str,
        request_type: str = "",
        response_hours: float | None = None,
        channel: str = "SLACK",
        had_quick_options: bool = False,
        used_quick_option: bool = False,
        ignored: bool = False,
    ) -> str:
        profile = tracker.record_response(
            program_id, person, request_type, response_hours,
            channel, had_quick_options, used_quick_option, ignored,
        )
        return profile.summary()

    def get_engagement_summary(program_id: str) -> str:
        return tracker.get_summary(program_id)

    def get_outreach_recommendation(program_id: str, person: str) -> str:
        rec = tracker.get_outreach_recommendation(program_id, person)
        return json.dumps(rec)

    # ── Quick responses ───────────────────────────────────────

    def generate_quick_response(
        request_type: str,
        item_name: str = "",
        target_date: str = "",
        options: list[str] | None = None,
        owner: str = "",
    ) -> str:
        kwargs: dict[str, Any] = {}
        if request_type == "status_check":
            kwargs = {"item_name": item_name, "target_date": target_date}
        elif request_type == "review_request":
            kwargs = {"artifact": item_name, "deadline": target_date}
        elif request_type == "decision_needed":
            kwargs = {"decision": item_name, "options": options}
        elif request_type == "timeline_confirmation":
            kwargs = {"milestone": item_name, "proposed_date": target_date}
        elif request_type == "scope_change_approval":
            kwargs = {"change_title": item_name}
        elif request_type == "blocker_triage":
            kwargs = {"blocker": item_name}
        elif request_type == "commitment_check":
            kwargs = {"commitment": item_name, "owner": owner}

        response_set = qr.generate_for_request(request_type, **kwargs)
        return json.dumps({
            "type": response_set.request_type,
            "context": response_set.context,
            "options_inline": response_set.render_inline(),
            "options_numbered": response_set.render_numbered(),
            "options": [{"label": o.label, "value": o.value} for o in response_set.options],
        })

    # ── Personalized briefings ────────────────────────────────

    def generate_briefing(program_id: str, person: str, role: str = "ENGINEER") -> str:
        briefing = briefings.generate(program_id, person, role)
        return briefing.render()

    # ── Transcript processing ─────────────────────────────────

    def analyze_transcript(
        content: str,
        attendees: list[str] | None = None,
        meeting_title: str = "",
    ) -> str:
        flags = transcript.analyze(content, attendees, meeting_title)
        if not flags:
            return "No ambiguities detected — meeting content is clear."

        summary = transcript.get_summary(flags)
        clarifications = transcript.generate_clarifications(flags, meeting_title)

        result = summary
        if clarifications:
            result += f"\n\n# Clarification Requests ({len(clarifications)})"
            for cr in clarifications:
                target = cr.target_person or "UNASSIGNED"
                result += (
                    f"\n- → **{target}** [{cr.urgency}]: {cr.question[:120]}"
                )
                if cr.quick_options:
                    result += f"\n  Options: {' | '.join(cr.quick_options[:4])}"

        return result

    return [
        # ── Engagement ────────────────────────────────────────
        {
            "name": "record_engagement",
            "description": (
                "Record how someone responded (or didn't) to a request. "
                "Builds their engagement profile for smarter outreach."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "person": {"type": "string"},
                    "request_type": {"type": "string", "description": "e.g. 'status_check', 'review_request'"},
                    "response_hours": {"type": "number", "description": "How many hours until they responded (null if no response)"},
                    "channel": {"type": "string", "enum": ["SLACK", "GCHAT", "EMAIL", "PR_COMMENT", "TICKET"]},
                    "had_quick_options": {"type": "boolean"},
                    "used_quick_option": {"type": "boolean"},
                    "ignored": {"type": "boolean"},
                },
                "required": ["program_id", "person"],
            },
            "executor": record_engagement,
        },
        {
            "name": "get_engagement_summary",
            "description": "Get engagement profiles for all team members — response rates, best channels, timing.",
            "input_schema": {
                "type": "object",
                "properties": {"program_id": {"type": "string"}},
                "required": ["program_id"],
            },
            "executor": get_engagement_summary,
        },
        {
            "name": "get_outreach_recommendation",
            "description": "Get a recommendation for how to best reach a specific person.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "person": {"type": "string"},
                },
                "required": ["program_id", "person"],
            },
            "executor": get_outreach_recommendation,
        },
        # ── Quick responses ───────────────────────────────────
        {
            "name": "generate_quick_response",
            "description": (
                "Generate one-click response options for a request. "
                "Reduces friction — engineers can respond with a single click "
                "instead of writing a full reply. Like vibe coding for PM."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "request_type": {
                        "type": "string",
                        "enum": ["status_check", "review_request", "decision_needed",
                                 "timeline_confirmation", "scope_change_approval",
                                 "blocker_triage", "commitment_check"],
                        "description": "Type of request to generate options for",
                    },
                    "item_name": {"type": "string", "description": "What the request is about"},
                    "target_date": {"type": "string", "description": "Relevant date if applicable"},
                    "options": {"type": "array", "items": {"type": "string"}, "description": "Decision options if applicable"},
                    "owner": {"type": "string", "description": "Owner for commitment checks"},
                },
                "required": ["request_type"],
            },
            "executor": generate_quick_response,
        },
        # ── Personalized briefings ────────────────────────────
        {
            "name": "generate_briefing",
            "description": (
                "Generate a role-aware personalized briefing. "
                "Engineers get their items. Leads get team status. "
                "Execs get risks + asks. Nobody gets noise."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "person": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["ENGINEER", "TECH_LEAD", "ENGINEERING_MANAGER",
                                 "PRODUCT_MANAGER", "DESIGNER", "EXECUTIVE", "STAKEHOLDER"],
                    },
                },
                "required": ["program_id", "person"],
            },
            "executor": generate_briefing,
        },
        # ── Transcript processing ─────────────────────────────
        {
            "name": "analyze_transcript",
            "description": (
                "Scan a meeting transcript for ambiguity: vague commitments, "
                "missing owners, unclear timelines, unresolved disagreements. "
                "Generates clarification requests with quick-response options."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Meeting transcript or notes text"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "meeting_title": {"type": "string"},
                },
                "required": ["content"],
            },
            "executor": analyze_transcript,
        },
    ]
