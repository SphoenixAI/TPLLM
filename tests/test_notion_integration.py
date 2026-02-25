"""Tests for Notion Integration."""

from __future__ import annotations

import json
from unittest.mock import patch

from lltpm.config import IntegrationConfig
from lltpm.integrations.notion_integration import (
    NotionIntegration,
    NotionPage,
    NotionDatabase,
)
from lltpm.integrations.base import IntegrationError

import pytest


def _make_config(**overrides):
    defaults = {
        "enabled": True,
        "api_key": "secret_test-notion-token-1234567890",
    }
    defaults.update(overrides)
    return IntegrationConfig(**defaults)


def test_integration_name():
    notion = NotionIntegration(_make_config())
    assert notion.name() == "Notion"


def test_integration_enabled():
    notion = NotionIntegration(_make_config())
    assert notion.enabled is True

    disabled = NotionIntegration(IntegrationConfig(enabled=False))
    assert disabled.enabled is False


def test_require_enabled_raises():
    notion = NotionIntegration(IntegrationConfig(enabled=False))
    with pytest.raises(IntegrationError, match="not configured"):
        notion.list_databases()


def test_list_databases_parses_response():
    notion = NotionIntegration(_make_config())
    mock_response = {
        "results": [
            {
                "id": "db-1",
                "title": [{"plain_text": "Sprint Board"}],
                "url": "https://notion.so/db-1",
                "properties": {
                    "Name": {"type": "title"},
                    "Status": {"type": "status"},
                    "Assignee": {"type": "people"},
                },
            },
        ]
    }
    with patch.object(notion, "_api_post", return_value=mock_response):
        databases = notion.list_databases()

    assert len(databases) == 1
    assert databases[0].id == "db-1"
    assert databases[0].title == "Sprint Board"
    assert "Name" in databases[0].property_names
    assert "Status" in databases[0].property_names


def test_query_database_parses_pages():
    notion = NotionIntegration(_make_config())
    mock_response = {
        "results": [
            {
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"plain_text": "Fix auth bug"}],
                    },
                    "Status": {
                        "type": "status",
                        "status": {"name": "In Progress"},
                    },
                    "Assignee": {
                        "type": "people",
                        "people": [{"name": "Bob"}],
                    },
                    "Due Date": {
                        "type": "date",
                        "date": {"start": "2026-03-15"},
                    },
                    "Tags": {
                        "type": "multi_select",
                        "multi_select": [{"name": "bug"}, {"name": "p0"}],
                    },
                },
            },
        ]
    }
    with patch.object(notion, "_api_post", return_value=mock_response):
        pages = notion.query_database("db-1")

    assert len(pages) == 1
    assert pages[0].title == "Fix auth bug"
    assert pages[0].status == "In Progress"
    assert pages[0].assignee == "Bob"
    assert pages[0].due_date == "2026-03-15"
    assert pages[0].tags == ["bug", "p0"]


def test_query_database_with_filters():
    notion = NotionIntegration(_make_config())
    mock_response = {"results": []}

    with patch.object(notion, "_api_post", return_value=mock_response) as mock_post:
        notion.query_database("db-1", status_filter="Done", assignee_filter="Alice")

    # Verify filter was sent
    call_body = mock_post.call_args[1]["body"]
    assert "filter" in call_body
    assert call_body["filter"]["and"][0]["property"] == "Status"
    assert call_body["filter"]["and"][1]["property"] == "Assignee"


def test_create_page():
    notion = NotionIntegration(_make_config())
    mock_response = {
        "id": "page-new",
        "url": "https://notion.so/page-new",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "New Task"}]},
            "Status": {"type": "status", "status": {"name": "To Do"}},
        },
    }
    with patch.object(notion, "_api_post", return_value=mock_response):
        page = notion.create_page(
            database_id="db-1",
            title="New Task",
            status="To Do",
            due_date="2026-04-01",
            content="Task description",
        )

    assert page.id == "page-new"
    assert page.title == "New Task"


def test_update_page():
    notion = NotionIntegration(_make_config())
    mock_response = {"id": "page-1", "url": "https://notion.so/page-1"}
    with patch.object(notion, "_api_patch", return_value=mock_response):
        result = notion.update_page("page-1", status="Done")

    assert result["id"] == "page-1"


def test_update_page_no_fields():
    notion = NotionIntegration(_make_config())
    result = notion.update_page("page-1")
    assert result["message"] == "No fields to update."


def test_search():
    notion = NotionIntegration(_make_config())
    mock_response = {
        "results": [
            {
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "Auth RFC"}]},
                },
            },
        ]
    }
    with patch.object(notion, "_api_post", return_value=mock_response):
        results = notion.search("auth")

    assert len(results) == 1
    assert results[0].title == "Auth RFC"


def test_database_summary():
    notion = NotionIntegration(_make_config())
    mock_pages = [
        NotionPage(id="1", title="A", url="", status="To Do", assignee="Alice", due_date="", tags=[]),
        NotionPage(id="2", title="B", url="", status="To Do", assignee="Bob", due_date="", tags=[]),
        NotionPage(id="3", title="C", url="", status="Done", assignee="Alice", due_date="", tags=[]),
    ]
    with patch.object(notion, "query_database", return_value=mock_pages):
        summary = notion.get_database_summary("db-1")

    assert summary["total_pages"] == 3
    assert summary["by_status"]["To Do"] == 2
    assert summary["by_status"]["Done"] == 1
    assert summary["by_assignee"]["Alice"] == 2
    assert summary["by_assignee"]["Bob"] == 1


def test_test_connection_disabled():
    notion = NotionIntegration(IntegrationConfig(enabled=False))
    assert notion.test_connection() is False


def test_test_connection_success():
    notion = NotionIntegration(_make_config())
    with patch.object(notion, "_api_get", return_value={"id": "user-1"}):
        assert notion.test_connection() is True


def test_test_connection_failure():
    notion = NotionIntegration(_make_config())
    with patch.object(notion, "_api_get", side_effect=Exception("fail")):
        assert notion.test_connection() is False


def test_parse_page_empty_properties():
    notion = NotionIntegration(_make_config())
    item = {
        "id": "empty",
        "url": "https://notion.so/empty",
        "properties": {},
    }
    page = notion._parse_page(item)
    assert page.id == "empty"
    assert page.title == ""
    assert page.status == ""
    assert page.assignee == ""


def test_parse_page_select_status():
    """Test that select-type Status property is parsed correctly."""
    notion = NotionIntegration(_make_config())
    item = {
        "id": "sel",
        "url": "",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Test"}]},
            "Status": {"type": "select", "select": {"name": "Active"}},
        },
    }
    page = notion._parse_page(item)
    assert page.status == "Active"
