"""Tool definitions for Google Drive export."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import LLTPMConfig


def get_gdrive_tools(config: LLTPMConfig, store: ArtifactStore) -> list[dict[str, Any]]:
    """Return tool definitions for Google Drive integration."""
    from lltpm.integrations.gdrive_integration import GoogleDriveIntegration
    from lltpm.workflows.document_generator import DocumentGenerator

    gdrive = GoogleDriveIntegration(config.gdrive)
    doc_gen = DocumentGenerator(store)

    def export_program_brief(
        program_id: str, folder_id: str = "", share_with: str = "",
    ) -> str:
        html = doc_gen.render_program_brief(program_id, "html")
        if "not found" in html.lower():
            return json.dumps({"error": f"Program {program_id} not found"})

        emails = [e.strip() for e in share_with.split(",") if e.strip()] if share_with else None
        result = gdrive.export_document(
            f"LLTPM Brief — {program_id}", html, folder_id, emails,
        )
        return json.dumps(result.to_dict())

    def export_full_report(
        program_id: str, folder_id: str = "", share_with: str = "",
    ) -> str:
        html = doc_gen.render_full_report(program_id, "html")
        emails = [e.strip() for e in share_with.split(",") if e.strip()] if share_with else None
        result = gdrive.export_document(
            f"LLTPM Report — {program_id}", html, folder_id, emails,
        )
        return json.dumps(result.to_dict())

    def export_raid_log(
        program_id: str, folder_id: str = "",
    ) -> str:
        """Export RAID log as a Google Sheet."""
        raid = store.load(program_id, "raid_log")
        if not raid:
            return json.dumps({"error": "No RAID log found"})

        headers = ["ID", "Type", "Title", "Severity", "Status", "Owner", "Mitigation"]
        rows = []
        for item in raid.get("items", []):
            rows.append([
                item.get("id", ""),
                item.get("item_type", ""),
                item.get("title", ""),
                item.get("severity", ""),
                item.get("status", ""),
                item.get("owner", ""),
                item.get("mitigation", ""),
            ])

        result = gdrive.export_spreadsheet(
            f"LLTPM RAID Log — {program_id}", headers, rows, folder_id,
        )
        return json.dumps(result.to_dict())

    def list_drive_exports(folder_id: str = "") -> str:
        files = gdrive.list_exports(folder_id)
        return json.dumps(files)

    def get_gdrive_summary() -> str:
        return gdrive.get_summary()

    return [
        {
            "name": "export_program_brief_to_drive",
            "description": "Export a program brief to Google Docs and optionally share with stakeholders",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "folder_id": {"type": "string", "description": "Drive folder ID"},
                    "share_with": {"type": "string", "description": "Comma-separated emails to share with"},
                },
                "required": ["program_id"],
            },
            "executor": export_program_brief,
        },
        {
            "name": "export_full_report_to_drive",
            "description": "Export a full program report to Google Docs",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "folder_id": {"type": "string"},
                    "share_with": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": export_full_report,
        },
        {
            "name": "export_raid_log_to_sheets",
            "description": "Export RAID log as a Google Sheet for collaborative editing",
            "input_schema": {
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                    "folder_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
            "executor": export_raid_log,
        },
        {
            "name": "list_drive_exports",
            "description": "List LLTPM documents in Google Drive",
            "input_schema": {
                "type": "object",
                "properties": {
                    "folder_id": {"type": "string"},
                },
            },
            "executor": list_drive_exports,
        },
        {
            "name": "get_gdrive_summary",
            "description": "Get Google Drive integration status",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": get_gdrive_summary,
        },
    ]
