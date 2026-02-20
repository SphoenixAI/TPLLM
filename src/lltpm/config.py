"""Configuration management for the LLTPM agent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class IntegrationConfig:
    """Connection settings for a single external service."""

    enabled: bool = False
    api_key: str = ""
    base_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLTPMConfig:
    """Top-level configuration for the LLTPM agent."""

    # Claude API
    anthropic_api_key: str = ""
    model: str = "claude-opus-4-6"
    max_tokens: int = 16384

    # Agent behaviour
    async_first: bool = True  # prefer async over meetings
    meeting_gate_enabled: bool = True
    auto_remind: bool = True
    stale_ticket_threshold_hours: int = 48
    risk_scan_interval_hours: int = 4

    # Persistence
    data_dir: Path = field(default_factory=lambda: Path.cwd() / ".lltpm")

    # Integrations
    github: IntegrationConfig = field(default_factory=IntegrationConfig)
    jira: IntegrationConfig = field(default_factory=IntegrationConfig)
    slack: IntegrationConfig = field(default_factory=IntegrationConfig)
    linear: IntegrationConfig = field(default_factory=IntegrationConfig)
    calendar: IntegrationConfig = field(default_factory=IntegrationConfig)

    # ── factories ──────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> LLTPMConfig:
        """Build config from environment variables and an optional JSON file."""
        cfg = cls()
        cfg.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        cfg.model = os.getenv("LLTPM_MODEL", cfg.model)
        cfg.data_dir = Path(os.getenv("LLTPM_DATA_DIR", str(cfg.data_dir)))

        # GitHub
        gh_token = os.getenv("GITHUB_TOKEN", "")
        if gh_token:
            cfg.github = IntegrationConfig(enabled=True, api_key=gh_token)

        # Jira
        jira_url = os.getenv("JIRA_BASE_URL", "")
        jira_token = os.getenv("JIRA_API_TOKEN", "")
        if jira_url and jira_token:
            cfg.jira = IntegrationConfig(
                enabled=True, api_key=jira_token, base_url=jira_url,
                extra={"email": os.getenv("JIRA_EMAIL", "")},
            )

        # Slack
        slack_token = os.getenv("SLACK_BOT_TOKEN", "")
        if slack_token:
            cfg.slack = IntegrationConfig(enabled=True, api_key=slack_token)

        # Linear
        linear_token = os.getenv("LINEAR_API_KEY", "")
        if linear_token:
            cfg.linear = IntegrationConfig(enabled=True, api_key=linear_token)

        # Overlay a JSON config file if present
        config_path = cfg.data_dir / "config.json"
        if config_path.exists():
            cfg._apply_json(config_path)

        return cfg

    def _apply_json(self, path: Path) -> None:
        with open(path) as f:
            data = json.load(f)
        for key, value in data.items():
            if hasattr(self, key) and not isinstance(getattr(self, key), IntegrationConfig):
                setattr(self, key, value)

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir
