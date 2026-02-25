"""Memory tools — persistent context management and gap-filling."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.persistent_memory import PersistentMemoryEngine


def get_memory_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    engine = PersistentMemoryEngine(store)

    def record_memory_turn(
        program_id: str,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
        context_references: list[str] | None = None,
    ) -> str:
        turn = engine.record_turn(program_id, role, content, tool_calls, context_references)
        return json.dumps({"turn_id": turn.id, "total_turns": turn.program_id})

    def search_memory(
        program_id: str,
        query: str,
        limit: int = 10,
    ) -> str:
        results = engine.search_turns(program_id, query, limit)
        if not results:
            return json.dumps({"results": [], "message": "No matching turns found"})
        return json.dumps({
            "results": [
                {"id": t.id, "role": t.role, "content": t.content[:300], "timestamp": str(t.timestamp)}
                for t in results
            ]
        })

    def get_context(program_id: str) -> str:
        return engine.reconstruct_context(program_id)

    def take_context_snapshot(
        program_id: str,
        summary: str = "",
        active_milestones: list[str] | None = None,
        open_risks: list[str] | None = None,
        pending_decisions: list[str] | None = None,
        key_people: dict[str, str] | None = None,
    ) -> str:
        snapshot = engine.take_snapshot(
            program_id, summary, active_milestones, open_risks,
            pending_decisions, key_people=key_people,
        )
        return json.dumps({"snapshot_id": snapshot.id, "timestamp": str(snapshot.timestamp)})

    def add_memory_anchor(
        program_id: str,
        fact: str,
        source: str = "",
        source_type: str = "conversation",
        people: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        anchor = engine.add_anchor(program_id, fact, source, source_type, people, tags)
        return json.dumps({"anchor_id": anchor.id, "fact": anchor.fact})

    def detect_memory_gaps(program_id: str) -> str:
        gaps = engine.detect_gaps(program_id)
        if not gaps:
            return json.dumps({"gaps": [], "message": "No gaps detected"})
        return json.dumps({
            "gaps": [
                {
                    "topic": g.topic,
                    "priority": g.priority,
                    "sources_to_check": [s.value for s in g.sources_to_check],
                    "questions": g.questions,
                }
                for g in gaps
            ]
        })

    def fill_memory_gap(
        program_id: str,
        topic: str,
        resolution: str,
        source_type: str = "artifact",
    ) -> str:
        filled = engine.fill_gap(program_id, topic, resolution, source_type)
        return json.dumps({"filled": filled, "topic": topic})

    def get_memory_summary(program_id: str) -> str:
        return engine.get_summary(program_id)

    return [
        {
            "name": "record_memory_turn",
            "description": "Record a conversation turn to persistent memory for cross-session context.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "role": {"type": "string", "enum": ["user", "assistant", "system"]},
                    "content": {"type": "string"},
                    "tool_calls": {"type": "array", "items": {"type": "object"}},
                    "context_references": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id", "role", "content"],
            },
            "executor": record_memory_turn,
        },
        {
            "name": "search_memory",
            "description": "Search conversation history for relevant context about a topic.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["program_id", "query"],
            },
            "executor": search_memory,
        },
        {
            "name": "get_context",
            "description": "Reconstruct full program context from all data sources. Use at session start or when context is stale.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": get_context,
        },
        {
            "name": "take_context_snapshot",
            "description": "Take a snapshot of current program state for fast context reconstruction later.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "active_milestones": {"type": "array", "items": {"type": "string"}},
                    "open_risks": {"type": "array", "items": {"type": "string"}},
                    "pending_decisions": {"type": "array", "items": {"type": "string"}},
                    "key_people": {"type": "object"},
                },
                "required": ["program_id"],
            },
            "executor": take_context_snapshot,
        },
        {
            "name": "add_memory_anchor",
            "description": "Pin a key fact that must be preserved across context windows and sessions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "fact": {"type": "string"},
                    "source": {"type": "string"},
                    "source_type": {"type": "string"},
                    "people": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["program_id", "fact"],
            },
            "executor": add_memory_anchor,
        },
        {
            "name": "detect_memory_gaps",
            "description": "Detect gaps in context by checking all data sources (artifacts, meetings, delegations, etc.).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": detect_memory_gaps,
        },
        {
            "name": "fill_memory_gap",
            "description": "Record that a detected memory gap has been filled with verified information.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "resolution": {"type": "string"},
                    "source_type": {"type": "string"},
                },
                "required": ["program_id", "topic", "resolution"],
            },
            "executor": fill_memory_gap,
        },
        {
            "name": "get_memory_summary",
            "description": "Get a summary of the persistent memory state for a program.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": get_memory_summary,
        },
    ]
