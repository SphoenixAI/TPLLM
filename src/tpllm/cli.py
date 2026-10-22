"""CLI utilities — config wizard, onboarding, and interactive helpers.

Separated from main.py to keep the entry point clean.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.markdown import Markdown

from tpllm.config import (
    ANTHROPIC_MODEL_IDS,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    KNOWN_OPENAI_BASE_URLS,
    MODEL_IDS,
    OPENAI_MODEL_IDS,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    SUPPORTED_PROVIDERS,
    IntegrationConfig,
    TPLLMConfig,
)

console = Console()


# ── Config Wizard ─────────────────────────────────────────


INTEGRATION_PROMPTS: list[dict[str, Any]] = [
    {
        "name": "github",
        "display": "GitHub",
        "env_var": "GITHUB_TOKEN",
        "required": False,
        "fields": [
            {"key": "GITHUB_TOKEN", "prompt": "GitHub personal access token", "secret": True},
        ],
    },
    {
        "name": "slack",
        "display": "Slack",
        "env_var": "SLACK_BOT_TOKEN",
        "required": False,
        "fields": [
            {"key": "SLACK_BOT_TOKEN", "prompt": "Slack Bot Token (xoxb-...)", "secret": True},
            {"key": "SLACK_APP_TOKEN", "prompt": "Slack App Token (xapp-...)", "secret": True},
        ],
    },
    {
        "name": "jira",
        "display": "Jira",
        "env_var": "JIRA_API_TOKEN",
        "required": False,
        "fields": [
            {"key": "JIRA_BASE_URL", "prompt": "Jira base URL", "secret": False},
            {"key": "JIRA_EMAIL", "prompt": "Jira email", "secret": False},
            {"key": "JIRA_API_TOKEN", "prompt": "Jira API token", "secret": True},
        ],
    },
    {
        "name": "email_smtp",
        "display": "Email (SMTP)",
        "env_var": "SMTP_HOST",
        "required": False,
        "fields": [
            {"key": "SMTP_HOST", "prompt": "SMTP host", "secret": False},
            {"key": "SMTP_PORT", "prompt": "SMTP port", "secret": False, "default": "587"},
            {"key": "SMTP_USER", "prompt": "SMTP username", "secret": False},
            {"key": "SMTP_PASSWORD", "prompt": "SMTP password", "secret": True},
            {"key": "EMAIL_FROM", "prompt": "From email address", "secret": False},
        ],
    },
    {
        "name": "email_sendgrid",
        "display": "Email (SendGrid)",
        "env_var": "SENDGRID_API_KEY",
        "required": False,
        "fields": [
            {"key": "SENDGRID_API_KEY", "prompt": "SendGrid API key", "secret": True},
            {"key": "EMAIL_FROM", "prompt": "From email address", "secret": False},
        ],
    },
    {
        "name": "zoom",
        "display": "Zoom",
        "env_var": "ZOOM_ACCOUNT_ID",
        "required": False,
        "fields": [
            {"key": "ZOOM_ACCOUNT_ID", "prompt": "Zoom Account ID", "secret": False},
            {"key": "ZOOM_API_KEY", "prompt": "Zoom API Key / Client ID", "secret": True},
            {"key": "ZOOM_CLIENT_SECRET", "prompt": "Zoom Client Secret", "secret": True},
        ],
    },
]


def run_config_wizard(data_dir: Path | None = None) -> dict[str, str]:
    """Interactive configuration wizard. Returns dict of env vars to set."""
    console.print(Panel(
        "[bold]TPLLM Configuration Wizard[/bold]\n\n"
        "This wizard will help you configure integrations.\n"
        "Press Enter to skip optional integrations.",
        title="Setup",
        border_style="blue",
    ))

    env_vars: dict[str, str] = {}

    # Data directory
    default_dir = str(data_dir or Path.cwd() / ".tpllm")
    dir_choice = Prompt.ask("Data directory", default=default_dir)
    env_vars["TPLLM_DATA_DIR"] = dir_choice

    # Provider selection
    provider = Prompt.ask(
        "LLM provider",
        choices=SUPPORTED_PROVIDERS,
        default=DEFAULT_PROVIDER,
    )
    env_vars["TPLLM_PROVIDER"] = provider

    if provider == PROVIDER_ANTHROPIC:
        # Anthropic API key
        console.print(f"\n[bold yellow]Anthropic API[/bold yellow]")
        api_key = Prompt.ask("  Anthropic API key", password=True)
        if api_key:
            env_vars["ANTHROPIC_API_KEY"] = api_key

        model = Prompt.ask(
            "Claude model",
            choices=ANTHROPIC_MODEL_IDS,
            default=DEFAULT_MODEL,
        )
        env_vars["TPLLM_MODEL"] = model
    else:
        # OpenAI-compatible provider
        console.print(f"\n[bold yellow]OpenAI-compatible API[/bold yellow]")
        console.print("  [dim]Works with DeepSeek, Together, Fireworks, OpenRouter, Ollama, etc.[/dim]")

        api_key = Prompt.ask("  API key", password=True, default="")
        if api_key:
            env_vars["OPENAI_API_KEY"] = api_key

        console.print("\n  [dim]Known providers: " + ", ".join(KNOWN_OPENAI_BASE_URLS) + "[/dim]")
        base_url = Prompt.ask(
            "  Base URL (e.g. https://api.deepseek.com/v1)",
            default="",
        )
        if base_url:
            env_vars["OPENAI_BASE_URL"] = base_url

        model = Prompt.ask(
            "Model ID",
            choices=OPENAI_MODEL_IDS,
            default=OPENAI_MODEL_IDS[0],
        )
        env_vars["TPLLM_MODEL"] = model

    console.print()

    for integration in INTEGRATION_PROMPTS:
        if integration["required"]:
            console.print(f"\n[bold yellow]{integration['display']}[/bold yellow]")
        else:
            enable = Confirm.ask(f"\nEnable {integration['display']}?", default=False)
            if not enable:
                continue
            console.print(f"  [bold]{integration['display']}[/bold]")

        for field_info in integration["fields"]:
            default = field_info.get("default", "")
            value = Prompt.ask(
                f"  {field_info['prompt']}",
                default=default,
                password=field_info.get("secret", False),
            )
            if value:
                env_vars[field_info["key"]] = value

    return env_vars


def save_env_file(env_vars: dict[str, str], path: Path) -> None:
    """Save environment variables to a .env file."""
    lines = ["# TPLLM Configuration", f"# Generated by tpllm init", ""]
    for key, value in sorted(env_vars.items()):
        lines.append(f"{key}={value}")
    lines.append("")

    path.write_text("\n".join(lines))
    console.print(f"\n[green]Configuration saved to {path}[/green]")
    console.print("[dim]Load with: source .env or use direnv/dotenv[/dim]")


def save_json_config(env_vars: dict[str, str], data_dir: Path) -> None:
    """Save a JSON config file to the data directory."""
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "config.json"

    config_data = {
        "model": env_vars.get("TPLLM_MODEL", DEFAULT_MODEL),
    }

    with open(config_path, "w") as f:
        json.dump(config_data, f, indent=2)


# ── Onboarding ────────────────────────────────────────────


def run_onboarding(config: TPLLMConfig) -> None:
    """Run first-time onboarding flow."""
    console.print(Panel(
        "[bold]Welcome to TPLLM[/bold]\n"
        "[italic]AI-powered Technical Program Manager[/italic]\n\n"
        "TPLLM replaces manual program tracking with intelligent,\n"
        "automated program management using the configured model.\n",
        title="TPLLM",
        border_style="cyan",
    ))

    console.print("[bold]What TPLLM does:[/bold]")
    features = [
        "Tracks programs, milestones, risks, and decisions",
        "Sends intelligent follow-ups and nudges",
        "Generates status reports and risk predictions",
        "Routes approvals through multi-step chains",
        "Integrates with Slack, GitHub, Jira, Email, and more",
        "Provides cross-program portfolio intelligence",
        "Detects scope creep and engagement drops",
    ]
    for f in features:
        console.print(f"  [green]>[/green] {f}")

    console.print()

    # Quick setup check
    console.print("[bold]Quick Setup Check:[/bold]")
    checks = [
        ("Anthropic API key", bool(config.anthropic_api_key)),
        ("Data directory", config.data_dir.exists()),
        ("GitHub integration", config.github.enabled),
        ("Slack integration", config.slack.enabled),
        ("Email integration", config.email.enabled),
    ]

    table = Table(show_header=False, box=None, padding=(0, 2))
    for name, ok in checks:
        status = "[green]ready[/green]" if ok else "[dim]not configured[/dim]"
        table.add_row(name, status)
    console.print(table)

    if not config.anthropic_api_key:
        console.print(
            "\n[yellow]To get started, run:[/yellow]\n"
            "  [bold]tpllm init[/bold]  — Configure integrations\n"
            "  [bold]export ANTHROPIC_API_KEY=your-key[/bold]  — Or set directly"
        )
    else:
        console.print(
            "\n[green]You're ready to go![/green] Try:\n"
            "  [bold]tpllm chat[/bold]  — Start a conversation\n"
            "  [bold]tpllm ask \"What programs are active?\"[/bold]  — Quick question\n"
            "  [bold]tpllm daily <program-id>[/bold]  — Daily briefing"
        )


# ── Status Display Helpers ────────────────────────────────


def display_integrations_table(config: TPLLMConfig) -> None:
    """Display a rich table of integration status."""
    table = Table(title="Integration Status", show_header=True)
    table.add_column("Integration", style="bold")
    table.add_column("Status")
    table.add_column("Details", style="dim")

    integrations = [
        ("GitHub", config.github, "Repository tracking"),
        ("Jira", config.jira, "Ticket management"),
        ("Slack", config.slack, "Team communication"),
        ("Linear", config.linear, "Issue tracking"),
        ("Calendar", config.calendar, "Meeting scheduling"),
        ("Google Chat", config.gchat, "Chat integration"),
        ("Zoom", config.zoom, "Meeting intelligence"),
        ("Email", config.email, "Notifications & digests"),
    ]

    for name, cfg, desc in integrations:
        status = "[green]enabled[/green]" if cfg.enabled else "[dim]disabled[/dim]"
        table.add_row(name, status, desc)

    console.print(table)


def display_program_table(programs: list[dict[str, Any]]) -> None:
    """Display programs in a rich table."""
    table = Table(title="Programs", show_header=True)
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Phase")
    table.add_column("Health")

    for p in programs:
        health = p.get("health", "unknown")
        health_style = {
            "green": "[green]Green[/green]",
            "yellow": "[yellow]Yellow[/yellow]",
            "red": "[red]Red[/red]",
        }.get(health.lower(), health)

        table.add_row(
            p.get("id", ""),
            p.get("name", ""),
            p.get("phase", ""),
            health_style,
        )

    console.print(table)


def display_tool_table(tool_definitions: list[dict[str, Any]]) -> None:
    """Display available tools in a categorized table."""
    table = Table(title="Available Tools", show_header=True)
    table.add_column("#", style="dim")
    table.add_column("Tool", style="bold")
    table.add_column("Description")

    for i, tool in enumerate(tool_definitions, 1):
        table.add_row(
            str(i),
            tool.get("name", ""),
            tool.get("description", "")[:80],
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(tool_definitions)} tools[/dim]")
