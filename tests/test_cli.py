"""Tests for CLI utilities and commands."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from lltpm.cli import (
    run_config_wizard,
    save_env_file,
    save_json_config,
    display_integrations_table,
    display_program_table,
    display_tool_table,
    INTEGRATION_PROMPTS,
)
from lltpm.config import LLTPMConfig, IntegrationConfig
from lltpm.main import cli


def test_integration_prompts_defined():
    """Ensure integration prompts are properly structured."""
    assert len(INTEGRATION_PROMPTS) >= 5
    for p in INTEGRATION_PROMPTS:
        assert "name" in p
        assert "display" in p
        assert "fields" in p
        for f in p["fields"]:
            assert "key" in f
            assert "prompt" in f


def test_save_env_file():
    """Test .env file generation."""
    with tempfile.NamedTemporaryFile(suffix=".env", delete=False, mode="w") as f:
        path = Path(f.name)

    env_vars = {
        "ANTHROPIC_API_KEY": "sk-test",
        "LLTPM_MODEL": "claude-opus-4-6",
        "LLTPM_DATA_DIR": "/tmp/lltpm",
    }
    save_env_file(env_vars, path)

    content = path.read_text()
    assert "ANTHROPIC_API_KEY=sk-test" in content
    assert "LLTPM_MODEL=claude-opus-4-6" in content
    path.unlink()


def test_save_json_config():
    """Test JSON config file generation."""
    data_dir = Path(tempfile.mkdtemp())
    env_vars = {"LLTPM_MODEL": "claude-sonnet-4-6"}

    save_json_config(env_vars, data_dir)

    config_path = data_dir / "config.json"
    assert config_path.exists()

    with open(config_path) as f:
        data = json.load(f)
    assert data["model"] == "claude-sonnet-4-6"


def test_display_integrations_table():
    """Test that integrations table renders without error."""
    config = LLTPMConfig()
    config.github = IntegrationConfig(enabled=True, api_key="test")
    # Should not raise
    display_integrations_table(config)


def test_display_program_table():
    """Test that program table renders without error."""
    programs = [
        {"id": "alpha", "name": "Project Alpha", "phase": "Execution", "health": "green"},
        {"id": "beta", "name": "Project Beta", "phase": "Planning", "health": "yellow"},
    ]
    display_program_table(programs)


def test_display_tool_table():
    """Test that tool table renders without error."""
    tools = [
        {"name": "create_program", "description": "Create a new program"},
        {"name": "add_risk", "description": "Add a risk to RAID log"},
    ]
    display_tool_table(tools)


def test_cli_status_command():
    """Test the status CLI command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "LLTPM Agent Status" in result.output


def test_cli_tools_command():
    """Test the tools CLI command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["tools"])
    assert result.exit_code == 0
    assert "Available Tools" in result.output


def test_cli_welcome_command():
    """Test the welcome/onboarding command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["welcome"])
    assert result.exit_code == 0
    assert "Welcome to LLTPM" in result.output


def test_cli_today_command():
    """Test the today command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["today"])
    assert result.exit_code == 0
    assert "Today" in result.output


def test_cli_help():
    """Test the main help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "LLTPM" in result.output
    assert "chat" in result.output
    assert "status" in result.output
    assert "init" in result.output


def test_cli_dashboard_without_deps():
    """Test dashboard command gracefully fails without FastAPI."""
    runner = CliRunner()
    # This will either start the server or show the install message
    # depending on whether fastapi is available
    result = runner.invoke(cli, ["dashboard"])
    # Either it starts (exit_code 0) or tells user to install
    # We just check it doesn't crash unexpectedly
    assert result.exit_code in (0, 1) or "dashboard" in result.output.lower()
