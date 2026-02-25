"""Tests for Google Drive Integration."""

from __future__ import annotations

from lltpm.config import IntegrationConfig
from lltpm.integrations.gdrive_integration import (
    GoogleDriveIntegration,
    ExportFormat,
    ExportResult,
)


def _make_config(**overrides):
    defaults = {
        "enabled": True,
        "api_key": "",
        "extra": {
            "service_account_key": "/tmp/test-sa.json",
            "folder_id": "test-folder-123",
        },
    }
    defaults.update(overrides)
    return IntegrationConfig(**defaults)


def test_integration_name():
    gdrive = GoogleDriveIntegration(_make_config())
    assert gdrive.name == "gdrive"


def test_integration_enabled():
    gdrive = GoogleDriveIntegration(_make_config())
    assert gdrive.enabled is True

    disabled = GoogleDriveIntegration(IntegrationConfig(enabled=False))
    assert disabled.enabled is False


def test_export_result_ok():
    result = ExportResult(file_id="abc123", file_name="Test Doc", file_url="https://docs.google.com/abc")
    assert result.ok is True


def test_export_result_error():
    result = ExportResult(error="Something went wrong")
    assert result.ok is False


def test_export_result_to_dict():
    result = ExportResult(
        file_id="abc123",
        file_name="Test Doc",
        file_url="https://docs.google.com/abc",
        format="google_doc",
        folder_id="folder-456",
    )
    d = result.to_dict()
    assert d["file_id"] == "abc123"
    assert d["ok"] is True
    assert d["format"] == "google_doc"


def test_export_format_values():
    assert ExportFormat.GOOGLE_DOC == "google_doc"
    assert ExportFormat.GOOGLE_SHEET == "google_sheet"
    assert ExportFormat.PDF == "pdf"
    assert ExportFormat.HTML == "html"


def test_export_document_without_google_libs():
    """Should return error when google libs not installed."""
    gdrive = GoogleDriveIntegration(_make_config())
    result = gdrive.export_document("Test", "<p>Hello</p>")
    # Will fail because service account file doesn't exist or google libs not installed
    assert not result.ok
    assert result.error


def test_export_spreadsheet_without_google_libs():
    gdrive = GoogleDriveIntegration(_make_config())
    result = gdrive.export_spreadsheet(
        "Test Sheet",
        ["Col A", "Col B"],
        [["Row 1A", "Row 1B"]],
    )
    assert not result.ok
    assert result.error


def test_upload_file_not_found():
    gdrive = GoogleDriveIntegration(_make_config())
    result = gdrive.upload_file("/nonexistent/file.pdf")
    assert not result.ok
    assert "not found" in result.error.lower()


def test_list_exports_without_google_libs():
    gdrive = GoogleDriveIntegration(_make_config())
    files = gdrive.list_exports()
    assert files == []


def test_html_to_plaintext():
    html = "<h1>Title</h1><p>Hello <strong>world</strong></p><br/>New line"
    text = GoogleDriveIntegration._html_to_plaintext(html)
    assert "Title" in text
    assert "Hello" in text
    assert "world" in text
    assert "<" not in text


def test_html_entities_decoded():
    html = "A &amp; B &lt; C &gt; D &mdash; E"
    text = GoogleDriveIntegration._html_to_plaintext(html)
    assert "A & B" in text
    assert "< C >" in text
    assert "—" in text


def test_get_summary():
    gdrive = GoogleDriveIntegration(_make_config())
    summary = gdrive.get_summary()
    assert "Google Drive" in summary
    assert "enabled" in summary
    assert "test-folder-123" in summary


def test_default_folder_used():
    cfg = _make_config()
    gdrive = GoogleDriveIntegration(cfg)
    assert gdrive._default_folder_id == "test-folder-123"


def test_no_folder_configured():
    cfg = IntegrationConfig(enabled=True, extra={})
    gdrive = GoogleDriveIntegration(cfg)
    assert gdrive._default_folder_id == ""
