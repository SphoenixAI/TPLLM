"""Document generation tools — render TPM artifacts as formatted documents."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.document_generator import DocumentGenerator


def get_document_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    gen = DocumentGenerator(store)

    def render_document(
        program_id: str,
        doc_type: str,
        fmt: str = "markdown",
    ) -> str:
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
            return json.dumps({"error": f"Unknown doc type: {doc_type}", "options": list(renderers.keys())})
        return renderer(program_id, fmt)

    def list_documents(program_id: str) -> str:
        docs = gen.list_renderable(program_id)
        return json.dumps({"documents": docs})

    def render_full_report(program_id: str, fmt: str = "markdown") -> str:
        return gen.render_full_report(program_id, fmt)

    return [
        {
            "name": "render_document",
            "description": (
                "Render a TPM artifact as a formatted document (HTML or Markdown). "
                "Supported types: program_brief, status_update, raid_log, decision_log, "
                "milestone_plan, launch_checklist, action_tracker, delegation_plan, full_report"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "doc_type": {
                        "type": "string",
                        "enum": [
                            "program_brief", "status_update", "raid_log",
                            "decision_log", "milestone_plan", "launch_checklist",
                            "action_tracker", "delegation_plan", "full_report",
                        ],
                    },
                    "fmt": {"type": "string", "enum": ["html", "markdown"]},
                },
                "required": ["program_id", "doc_type"],
            },
            "executor": render_document,
        },
        {
            "name": "list_documents",
            "description": "List all document types available for rendering for a program.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": list_documents,
        },
        {
            "name": "render_full_report",
            "description": "Render a comprehensive program report combining all artifacts into one document.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "fmt": {"type": "string", "enum": ["html", "markdown"]},
                },
                "required": ["program_id"],
            },
            "executor": render_full_report,
        },
    ]
