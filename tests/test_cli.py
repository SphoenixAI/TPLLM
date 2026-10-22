"""Tests for CLI utilities and commands."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from tpllm.cli import (
    run_config_wizard,
    save_env_file,
    save_json_config,
    display_integrations_table,
    display_program_table,
    display_tool_table,
    INTEGRATION_PROMPTS,
)
from tpllm.config import TPLLMConfig, IntegrationConfig
from tpllm.main import cli


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
        "TPLLM_MODEL": "claude-opus-4-6",
        "TPLLM_DATA_DIR": "/tmp/tpllm",
    }
    save_env_file(env_vars, path)

    content = path.read_text()
    assert "ANTHROPIC_API_KEY=sk-test" in content
    assert "TPLLM_MODEL=claude-opus-4-6" in content
    path.unlink()


def test_save_json_config():
    """Test JSON config file generation."""
    data_dir = Path(tempfile.mkdtemp())
    env_vars = {"TPLLM_MODEL": "claude-sonnet-4-6"}

    save_json_config(env_vars, data_dir)

    config_path = data_dir / "config.json"
    assert config_path.exists()

    with open(config_path) as f:
        data = json.load(f)
    assert data["model"] == "claude-sonnet-4-6"


def test_display_integrations_table():
    """Test that integrations table renders without error."""
    config = TPLLMConfig()
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
    assert "TPLLM Agent Status" in result.output


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
    assert "Welcome to TPLLM" in result.output


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
    assert "TPLLM" in result.output
    assert "chat" in result.output
    assert "status" in result.output
    assert "init" in result.output


def test_cli_dashboard_reports_missing_extras():
    """Without the dashboard extras, say so rather than traceback."""
    runner = CliRunner()
    with patch.dict("sys.modules", {"tpllm.dashboard": None}):
        result = runner.invoke(cli, ["dashboard"])
    assert result.exit_code == 0
    assert "requires extra dependencies" in result.output
    assert "tpllm[dashboard]" in result.output, (
        "rich reads [dashboard] as a style tag unless escaped, which "
        "turns the hint into 'pip install tpllm'"
    )


def test_cli_dashboard_starts_the_server_when_available():
    """Assert what the command decided, without binding a socket.

    The serve call is patched out on purpose. The previous version of
    this test invoked the real one, so it only passed when uvicorn was
    absent — and when uvicorn was installed it started a web server on
    0.0.0.0:8000 and hung the suite forever.
    """
    runner = CliRunner()
    with patch("tpllm.main._serve_dashboard") as serve:
        result = runner.invoke(cli, ["dashboard"])

    assert result.exit_code == 0
    serve.assert_called_once()
    assert serve.call_args.kwargs == {"host": "127.0.0.1", "port": 8000}


def test_cli_dashboard_binds_loopback_by_default():
    """Exposing a dashboard beyond the machine is opt-in."""
    runner = CliRunner()
    with patch("tpllm.main._serve_dashboard") as serve:
        runner.invoke(cli, ["dashboard"])
    assert serve.call_args.kwargs["host"] == "127.0.0.1"


def test_cli_dashboard_host_and_port_are_overridable():
    runner = CliRunner()
    with patch("tpllm.main._serve_dashboard") as serve:
        result = runner.invoke(
            cli, ["dashboard", "--host", "0.0.0.0", "--port", "9999"],
        )
    assert result.exit_code == 0
    assert serve.call_args.kwargs == {"host": "0.0.0.0", "port": 9999}


def test_cli_dashboard_reports_the_url_it_will_serve():
    runner = CliRunner()
    with patch("tpllm.main._serve_dashboard"):
        result = runner.invoke(cli, ["dashboard", "--port", "9100"])
    assert "9100" in result.output


def test_the_cli_test_suite_never_binds_a_real_socket():
    """A regression guard for the class of bug, not just the instance.

    A test suite that starts a listening server depends on which
    packages happen to be installed and can hang indefinitely. The serve
    call must be reached only through the patchable seam.
    """
    import ast
    import inspect

    import tpllm.main as main_module

    tree = ast.parse(inspect.getsource(main_module))
    uvicorn_run_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and getattr(node.func.value, "id", "") == "uvicorn"
    ]
    assert len(uvicorn_run_calls) == 1, (
        "uvicorn.run must live in exactly one extracted seam so tests can "
        "patch it; inlining it again would reintroduce the hang"
    )
