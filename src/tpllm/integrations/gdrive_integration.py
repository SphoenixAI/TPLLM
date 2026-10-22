"""Google Drive / Docs export integration.

Exports TPLLM documents to Google Drive as:
- Google Docs (from HTML/Markdown)
- Google Sheets (for tabular data like RAID logs, milestone plans)
- PDF files uploaded to Drive

Supports service account or OAuth2 authentication.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tpllm.config import IntegrationConfig


class ExportFormat(str, Enum):
    """Supported export formats."""
    GOOGLE_DOC = "google_doc"
    GOOGLE_SHEET = "google_sheet"
    PDF = "pdf"
    HTML = "html"


@dataclass
class ExportResult:
    """Result of a Drive export operation."""
    file_id: str = ""
    file_name: str = ""
    file_url: str = ""
    format: str = ""
    folder_id: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.file_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "file_name": self.file_name,
            "file_url": self.file_url,
            "format": self.format,
            "folder_id": self.folder_id,
            "ok": self.ok,
            "error": self.error,
        }


class GoogleDriveIntegration:
    """Google Drive export for TPLLM documents.

    Uses the Google Drive API v3 and Google Docs API v1 to:
    - Create Google Docs from HTML content
    - Create Google Sheets from tabular data
    - Upload files to specific Drive folders
    - Share documents with stakeholders
    """

    def __init__(self, config: IntegrationConfig) -> None:
        self._config = config
        self._service_account_key = config.extra.get("service_account_key", "")
        self._default_folder_id = config.extra.get("folder_id", "")
        self._export_count = 0

    @property
    def name(self) -> str:
        return "gdrive"

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def export_document(
        self,
        title: str,
        html_content: str,
        folder_id: str = "",
        share_with: list[str] | None = None,
    ) -> ExportResult:
        """Export HTML content as a Google Doc.

        Args:
            title: Document title.
            html_content: HTML content to convert.
            folder_id: Drive folder ID (uses default if empty).
            share_with: List of email addresses to share with.
        """
        target_folder = folder_id or self._default_folder_id

        try:
            drive_service = self._get_drive_service()
            docs_service = self._get_docs_service()

            # Create a blank Google Doc
            file_metadata: dict[str, Any] = {
                "name": title,
                "mimeType": "application/vnd.google-apps.document",
            }
            if target_folder:
                file_metadata["parents"] = [target_folder]

            created = drive_service.files().create(
                body=file_metadata, fields="id,webViewLink",
            ).execute()

            file_id = created["id"]
            file_url = created.get("webViewLink", "")

            # Insert HTML content into the doc
            requests = [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": self._html_to_plaintext(html_content),
                    }
                }
            ]
            docs_service.documents().batchUpdate(
                documentId=file_id, body={"requests": requests},
            ).execute()

            # Share with specified users
            if share_with:
                for email in share_with:
                    self._share_file(drive_service, file_id, email)

            self._export_count += 1
            return ExportResult(
                file_id=file_id,
                file_name=title,
                file_url=file_url,
                format=ExportFormat.GOOGLE_DOC.value,
                folder_id=target_folder,
            )
        except ImportError:
            return ExportResult(
                error="google-api-python-client not installed. "
                      "Install: pip install google-api-python-client google-auth",
            )
        except Exception as e:
            return ExportResult(error=str(e))

    def export_spreadsheet(
        self,
        title: str,
        headers: list[str],
        rows: list[list[str]],
        folder_id: str = "",
        share_with: list[str] | None = None,
    ) -> ExportResult:
        """Export tabular data as a Google Sheet.

        Args:
            title: Spreadsheet title.
            headers: Column headers.
            rows: Data rows.
            folder_id: Drive folder ID.
            share_with: Emails to share with.
        """
        target_folder = folder_id or self._default_folder_id

        try:
            drive_service = self._get_drive_service()
            sheets_service = self._get_sheets_service()

            # Create spreadsheet
            file_metadata: dict[str, Any] = {
                "name": title,
                "mimeType": "application/vnd.google-apps.spreadsheet",
            }
            if target_folder:
                file_metadata["parents"] = [target_folder]

            created = drive_service.files().create(
                body=file_metadata, fields="id,webViewLink",
            ).execute()

            file_id = created["id"]
            file_url = created.get("webViewLink", "")

            # Write data
            values = [headers] + rows
            sheets_service.spreadsheets().values().update(
                spreadsheetId=file_id,
                range="A1",
                valueInputOption="RAW",
                body={"values": values},
            ).execute()

            if share_with:
                for email in share_with:
                    self._share_file(drive_service, file_id, email)

            self._export_count += 1
            return ExportResult(
                file_id=file_id,
                file_name=title,
                file_url=file_url,
                format=ExportFormat.GOOGLE_SHEET.value,
                folder_id=target_folder,
            )
        except ImportError:
            return ExportResult(
                error="google-api-python-client not installed",
            )
        except Exception as e:
            return ExportResult(error=str(e))

    def upload_file(
        self,
        file_path: str,
        title: str = "",
        mime_type: str = "",
        folder_id: str = "",
    ) -> ExportResult:
        """Upload a local file to Google Drive.

        Args:
            file_path: Local file path.
            title: File name in Drive (defaults to local filename).
            mime_type: MIME type (auto-detected if empty).
            folder_id: Target Drive folder.
        """
        from pathlib import Path
        local_path = Path(file_path)

        if not local_path.exists():
            return ExportResult(error=f"File not found: {file_path}")

        name = title or local_path.name
        target_folder = folder_id or self._default_folder_id

        # Auto-detect MIME type
        if not mime_type:
            ext_map = {
                ".pdf": "application/pdf",
                ".html": "text/html",
                ".md": "text/markdown",
                ".json": "application/json",
                ".csv": "text/csv",
                ".png": "image/png",
                ".jpg": "image/jpeg",
            }
            mime_type = ext_map.get(local_path.suffix.lower(), "application/octet-stream")

        try:
            drive_service = self._get_drive_service()
            from googleapiclient.http import MediaFileUpload

            file_metadata: dict[str, Any] = {"name": name}
            if target_folder:
                file_metadata["parents"] = [target_folder]

            media = MediaFileUpload(str(local_path), mimetype=mime_type)
            created = drive_service.files().create(
                body=file_metadata, media_body=media, fields="id,webViewLink",
            ).execute()

            self._export_count += 1
            return ExportResult(
                file_id=created["id"],
                file_name=name,
                file_url=created.get("webViewLink", ""),
                format=mime_type,
                folder_id=target_folder,
            )
        except ImportError:
            return ExportResult(error="google-api-python-client not installed")
        except Exception as e:
            return ExportResult(error=str(e))

    def list_exports(self, folder_id: str = "") -> list[dict[str, Any]]:
        """List files in the TPLLM Drive folder."""
        target_folder = folder_id or self._default_folder_id

        try:
            drive_service = self._get_drive_service()
            query = f"'{target_folder}' in parents" if target_folder else "name contains 'TPLLM'"

            results = drive_service.files().list(
                q=query,
                fields="files(id,name,mimeType,modifiedTime,webViewLink)",
                orderBy="modifiedTime desc",
                pageSize=50,
            ).execute()

            return results.get("files", [])
        except ImportError:
            return []
        except Exception:
            return []

    def get_summary(self) -> str:
        """Get integration status summary."""
        lines = [
            "## Google Drive Integration",
            f"- Status: {'enabled' if self.enabled else 'disabled'}",
            f"- Default folder: {self._default_folder_id or 'not set'}",
            f"- Exports this session: {self._export_count}",
            f"- Auth: {'service account' if self._service_account_key else 'not configured'}",
        ]
        return "\n".join(lines)

    # ── Internal ──────────────────────────────────────────

    def _get_drive_service(self):
        """Build the Google Drive API service."""
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            self._service_account_key,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        return build("drive", "v3", credentials=creds)

    def _get_docs_service(self):
        """Build the Google Docs API service."""
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            self._service_account_key,
            scopes=["https://www.googleapis.com/auth/documents"],
        )
        return build("docs", "v1", credentials=creds)

    def _get_sheets_service(self):
        """Build the Google Sheets API service."""
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            self._service_account_key,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return build("sheets", "v4", credentials=creds)

    @staticmethod
    def _share_file(drive_service, file_id: str, email: str) -> None:
        """Share a file with a user."""
        permission = {
            "type": "user",
            "role": "writer",
            "emailAddress": email,
        }
        drive_service.permissions().create(
            fileId=file_id, body=permission, sendNotificationEmail=False,
        ).execute()

    @staticmethod
    def _html_to_plaintext(html: str) -> str:
        """Simple HTML to plaintext conversion for Docs insertion."""
        import re
        # Strip tags
        text = re.sub(r"<br\s*/?>", "\n", html)
        text = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        # Decode common entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&nbsp;", " ").replace("&mdash;", "—").replace("&ndash;", "–")
        return text.strip()
