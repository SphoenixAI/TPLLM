"""Tests for Asana Integration."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

from lltpm.config import IntegrationConfig
from lltpm.integrations.asana_integration import (
    AsanaIntegration,
    AsanaTask,
    AsanaProject,
)
from lltpm.integrations.base import IntegrationError

import pytest


def _make_config(**overrides):
    defaults = {
        "enabled": True,
        "api_key": "test-asana-token-1234567890",
        "extra": {"workspace_gid": "ws-123"},
    }
    defaults.update(overrides)
    return IntegrationConfig(**defaults)


def test_integration_name():
    asana = AsanaIntegration(_make_config())
    assert asana.name() == "Asana"


def test_integration_enabled():
    asana = AsanaIntegration(_make_config())
    assert asana.enabled is True

    disabled = AsanaIntegration(IntegrationConfig(enabled=False))
    assert disabled.enabled is False


def test_require_enabled_raises():
    asana = AsanaIntegration(IntegrationConfig(enabled=False))
    with pytest.raises(IntegrationError, match="not configured"):
        asana.list_projects()


def test_list_projects_requires_workspace():
    asana = AsanaIntegration(_make_config(extra={}))
    with pytest.raises(IntegrationError, match="workspace_gid is required"):
        asana.list_projects()


def test_list_projects_parses_response():
    asana = AsanaIntegration(_make_config())
    mock_response = {
        "data": [
            {
                "gid": "proj-1",
                "name": "Backend Sprint",
                "team": {"name": "Engineering"},
                "permalink_url": "https://app.asana.com/proj-1",
            },
            {
                "gid": "proj-2",
                "name": "Frontend Sprint",
                "team": None,
                "permalink_url": "https://app.asana.com/proj-2",
            },
        ]
    }
    with patch.object(asana, "_api_get", return_value=mock_response):
        projects = asana.list_projects()

    assert len(projects) == 2
    assert projects[0].gid == "proj-1"
    assert projects[0].name == "Backend Sprint"
    assert projects[0].team == "Engineering"
    assert projects[1].team == ""


def test_search_tasks_by_project():
    asana = AsanaIntegration(_make_config())
    mock_response = {
        "data": [
            {
                "gid": "task-1",
                "name": "Fix login bug",
                "assignee": {"name": "Alice"},
                "memberships": [{"section": {"name": "In Progress"}}],
                "due_on": "2026-03-01",
                "completed": False,
                "tags": [{"name": "bug"}],
                "permalink_url": "https://app.asana.com/task-1",
                "notes": "High priority",
            },
        ]
    }
    with patch.object(asana, "_api_get", return_value=mock_response):
        tasks = asana.search_tasks(project_gid="proj-1")

    assert len(tasks) == 1
    assert tasks[0].name == "Fix login bug"
    assert tasks[0].assignee == "Alice"
    assert tasks[0].section == "In Progress"
    assert tasks[0].completed is False
    assert "bug" in tasks[0].tags


def test_search_tasks_no_assignee():
    asana = AsanaIntegration(_make_config())
    mock_response = {
        "data": [
            {
                "gid": "task-2",
                "name": "Unassigned task",
                "assignee": None,
                "memberships": [],
                "due_on": "",
                "completed": False,
                "tags": [],
                "permalink_url": "",
                "notes": "",
            },
        ]
    }
    with patch.object(asana, "_api_get", return_value=mock_response):
        tasks = asana.search_tasks(project_gid="proj-1")

    assert tasks[0].assignee == ""
    assert tasks[0].section == ""


def test_create_task():
    asana = AsanaIntegration(_make_config())
    mock_response = {
        "data": {
            "gid": "task-new",
            "name": "New feature",
            "permalink_url": "https://app.asana.com/task-new",
        }
    }
    with patch.object(asana, "_api_post", return_value=mock_response):
        task = asana.create_task(
            name="New feature",
            project_gid="proj-1",
            assignee="alice@example.com",
            due_date="2026-04-01",
            notes="Implement new feature",
        )

    assert task.gid == "task-new"
    assert task.name == "New feature"


def test_update_task():
    asana = AsanaIntegration(_make_config())
    mock_response = {"data": {"gid": "task-1", "name": "Updated"}}
    with patch.object(asana, "_api_put", return_value=mock_response):
        result = asana.update_task("task-1", name="Updated")

    assert result.get("gid") == "task-1"


def test_update_task_no_fields():
    asana = AsanaIntegration(_make_config())
    result = asana.update_task("task-1")
    assert result["message"] == "No fields to update."


def test_get_task_count_by_section():
    asana = AsanaIntegration(_make_config())
    sections_response = {
        "data": [
            {"gid": "sec-1", "name": "To Do"},
            {"gid": "sec-2", "name": "Done"},
        ]
    }
    tasks_todo = {"data": [{"completed": False}, {"completed": False}, {"completed": True}]}
    tasks_done = {"data": [{"completed": True}]}

    def mock_get(path, params=None):
        if "sections" in path and "tasks" not in path:
            return sections_response
        if "sec-1" in path:
            return tasks_todo
        return tasks_done

    with patch.object(asana, "_api_get", side_effect=mock_get):
        counts = asana.get_task_count_by_section("proj-1")

    assert counts["To Do"] == 2
    assert counts["Done"] == 0


def test_test_connection_disabled():
    asana = AsanaIntegration(IntegrationConfig(enabled=False))
    assert asana.test_connection() is False


def test_test_connection_success():
    asana = AsanaIntegration(_make_config())
    with patch.object(asana, "_api_get", return_value={"data": {"gid": "123"}}):
        assert asana.test_connection() is True


def test_test_connection_failure():
    asana = AsanaIntegration(_make_config())
    with patch.object(asana, "_api_get", side_effect=Exception("Network error")):
        assert asana.test_connection() is False


def test_search_tasks_filter_completed():
    asana = AsanaIntegration(_make_config())
    mock_response = {
        "data": [
            {
                "gid": "t1", "name": "Done task",
                "assignee": None, "memberships": [],
                "due_on": "", "completed": True,
                "tags": [], "permalink_url": "", "notes": "",
            },
            {
                "gid": "t2", "name": "Open task",
                "assignee": None, "memberships": [],
                "due_on": "", "completed": False,
                "tags": [], "permalink_url": "", "notes": "",
            },
        ]
    }
    with patch.object(asana, "_api_get", return_value=mock_response):
        tasks = asana.search_tasks(project_gid="proj-1", completed=False)

    assert len(tasks) == 1
    assert tasks[0].name == "Open task"
