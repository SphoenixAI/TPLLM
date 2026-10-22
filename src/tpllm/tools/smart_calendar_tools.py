"""Smart calendar tools — intelligent scheduling with availability optimization."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.calendar import (
    AttendeeInfo,
    AttendeeRole,
    MeetingPriority,
    SmartScheduleRequest,
)
from tpllm.workflows.smart_calendar import SmartCalendarEngine


def get_smart_calendar_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = SmartCalendarEngine(store)

    def smart_schedule(
        program_id: str,
        title: str,
        attendees: list[dict[str, Any]],
        duration_minutes: int = 30,
        priority: str = "medium",
        description: str = "",
        avoid_fridays: bool = True,
        avoid_lunch: bool = True,
        meeting_gate_approved: bool = False,
        agenda_items: list[str] | None = None,
    ) -> str:
        att_list = []
        for a in attendees:
            att_list.append(AttendeeInfo(
                person=a.get("person", ""),
                email=a.get("email", ""),
                role=AttendeeRole(a.get("role", "required")),
                scope_relevance=a.get("scope_relevance", 1.0),
                preferred_hours=tuple(a.get("preferred_hours", [9, 17])),
                timezone=a.get("timezone", "UTC"),
                meeting_load_today=a.get("meeting_load_today", 0),
                max_daily_meetings=a.get("max_daily_meetings", 6),
            ))

        try:
            pri = MeetingPriority(priority)
        except ValueError:
            pri = MeetingPriority.MEDIUM

        request = SmartScheduleRequest(
            title=title,
            description=description,
            program_id=program_id,
            duration_minutes=duration_minutes,
            priority=pri,
            attendees=att_list,
            avoid_fridays=avoid_fridays,
            avoid_lunch=avoid_lunch,
            meeting_gate_approved=meeting_gate_approved,
            agenda_items=agenda_items or [],
        )

        result = engine.schedule(request)
        slots = []
        for s in result.recommended_slots[:5]:
            slots.append({
                "start": str(s.start),
                "end": str(s.end),
                "score": round(s.score, 2),
                "available": s.attendees_available,
                "unavailable": s.attendees_unavailable,
            })

        return json.dumps({
            "title": result.request_title,
            "recommended_slots": slots,
            "best_slot": {
                "start": str(result.best_slot.start),
                "end": str(result.best_slot.end),
                "score": round(result.best_slot.score, 2),
            } if result.best_slot else None,
            "notes": result.scheduling_notes,
            "attendee_optimization": result.attendee_optimization,
        })

    def find_optimal_attendees(
        program_id: str,
        topic: str,
        candidates: list[dict[str, Any]],
    ) -> str:
        att_list = [
            AttendeeInfo(
                person=c.get("person", ""),
                scope_relevance=c.get("scope_relevance", 0.5),
            )
            for c in candidates
        ]

        optimized = engine.find_optimal_attendees(program_id, topic, att_list)
        return json.dumps({
            "attendees": [
                {
                    "person": a.person,
                    "role": a.role.value,
                    "scope_relevance": a.scope_relevance,
                }
                for a in optimized
            ]
        })

    def get_meeting_load(
        person: str,
        meetings_today: int = 0,
        max_daily: int = 6,
    ) -> str:
        result = engine.get_meeting_load(person, meetings_today, max_daily)
        return json.dumps(result)

    def block_focus_time(
        program_id: str,
        person: str,
        hours: int = 2,
        reason: str = "Focus time",
    ) -> str:
        from datetime import datetime
        block = engine.block_focus_time(program_id, person, datetime.utcnow(), hours, reason)
        return json.dumps({
            "person": block.person,
            "title": block.title,
            "start": str(block.start),
            "end": str(block.end),
            "block_type": block.block_type,
        })

    def suggest_reschedule(
        program_id: str,
        event_id: str,
        reason: str,
    ) -> str:
        reschedule = engine.suggest_reschedule(program_id, event_id, reason)
        return json.dumps({
            "event_id": reschedule.event_id,
            "reason": reschedule.reason,
            "message": reschedule.message,
            "new_slots_count": len(reschedule.new_slots),
        })

    return [
        {
            "name": "smart_schedule",
            "description": (
                "Intelligently schedule a meeting considering attendee availability, "
                "engagement patterns, scope relevance, and meeting load. "
                "Returns ranked time slots."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "title": {"type": "string"},
                    "attendees": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "person": {"type": "string"},
                                "email": {"type": "string"},
                                "role": {"type": "string", "enum": ["required", "optional", "informed"]},
                                "scope_relevance": {"type": "number"},
                                "preferred_hours": {"type": "array", "items": {"type": "integer"}},
                                "timezone": {"type": "string"},
                                "meeting_load_today": {"type": "integer"},
                                "max_daily_meetings": {"type": "integer"},
                            },
                        },
                    },
                    "duration_minutes": {"type": "integer"},
                    "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "description": {"type": "string"},
                    "avoid_fridays": {"type": "boolean"},
                    "avoid_lunch": {"type": "boolean"},
                    "meeting_gate_approved": {"type": "boolean"},
                    "agenda_items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id", "title", "attendees"],
            },
            "executor": smart_schedule,
        },
        {
            "name": "find_optimal_attendees",
            "description": (
                "Determine who actually needs to be in a meeting based on "
                "scope relevance and engagement scores. Categorizes as required/optional/informed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "person": {"type": "string"},
                                "scope_relevance": {"type": "number"},
                            },
                        },
                    },
                },
                "required": ["program_id", "topic", "candidates"],
            },
            "executor": find_optimal_attendees,
        },
        {
            "name": "get_meeting_load",
            "description": "Assess a person's meeting load and get scheduling recommendation.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "person": {"type": "string"},
                    "meetings_today": {"type": "integer"},
                    "max_daily": {"type": "integer"},
                },
                "required": ["person"],
            },
            "executor": get_meeting_load,
        },
        {
            "name": "block_focus_time",
            "description": "Block focus time on someone's calendar to protect deep work.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "person": {"type": "string"},
                    "hours": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["program_id", "person"],
            },
            "executor": block_focus_time,
        },
        {
            "name": "suggest_reschedule",
            "description": "Suggest rescheduling a meeting with a reason and new optimal slots.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "event_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["program_id", "event_id", "reason"],
            },
            "executor": suggest_reschedule,
        },
    ]
