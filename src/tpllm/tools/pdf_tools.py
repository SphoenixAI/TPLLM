"""Tool definitions for PDF export pipeline."""

from __future__ import annotations

import json
from typing import Any

from tpllm.artifacts.store import ArtifactStore


def get_pdf_tools(store: ArtifactStore) -> list[dict[str, Any]]:
    """Return tool definitions for PDF export."""
    from tpllm.workflows.pdf_exporter import PDFExporter

    exporter = PDFExporter(store)

    def export_program_report_pdf(program_id: str, filename: str = "") -> str:
        result = exporter.export_program_report(program_id, filename)
        return json.dumps(result.to_dict())

    def export_program_brief_pdf(program_id: str, filename: str = "") -> str:
        result = exporter.export_program_brief(program_id, filename)
        return json.dumps(result.to_dict())

    def export_raid_log_pdf(program_id: str, filename: str = "") -> str:
        result = exporter.export_raid_log(program_id, filename)
        return json.dumps(result.to_dict())

    def export_html_to_pdf(html: str, filename: str) -> str:
        result = exporter.export_html_to_pdf(html, filename)
        return json.dumps(result.to_dict())

    def list_pdf_backends() -> str:
        backends = exporter.list_available_backends()
        return json.dumps(backends)

    def get_pdf_summary() -> str:
        return exporter.get_summary()

    return [
        {
            "name": "export_program_report_pdf",
            "description": "Export a full program report as PDF",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "filename": {"type": "string", "description": "Custom filename"},
                },
                "required": ["program_id"],
            },
            "executor": export_program_report_pdf,
        },
        {
            "name": "export_program_brief_pdf",
            "description": "Export a program brief as PDF",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": export_program_brief_pdf,
        },
        {
            "name": "export_raid_log_pdf",
            "description": "Export a RAID log as PDF",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": export_raid_log_pdf,
        },
        {
            "name": "export_html_to_pdf",
            "description": "Convert any HTML content to PDF",
            "input_schema": {
                "type": "object",
                "properties": {
                    "html": {"type": "string", "description": "HTML content to convert"},
                    "filename": {"type": "string", "description": "Output filename"},
                },
                "required": ["html", "filename"],
            },
            "executor": export_html_to_pdf,
        },
        {
            "name": "list_pdf_backends",
            "description": "List available PDF export backends",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": list_pdf_backends,
        },
        {
            "name": "get_pdf_summary",
            "description": "Get PDF export pipeline status",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": get_pdf_summary,
        },
    ]
