"""Configuration management for the LLTPM agent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Providers ─────────────────────────────────────────────
# "anthropic" uses the native Anthropic SDK (tool_use, thinking).
# "openai" uses the OpenAI-compatible chat/completions API,
#   which most third-party providers expose (DeepSeek, Together,
#   Fireworks, Gemini via OpenAI compat, Ollama, etc.).

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
SUPPORTED_PROVIDERS = [PROVIDER_ANTHROPIC, PROVIDER_OPENAI]

# ── Models ────────────────────────────────────────────────
# Curated list shown in the config wizard.  Any model ID the
# provider accepts will work — these are just the defaults.

SUPPORTED_MODELS: list[dict[str, str]] = [
    # Anthropic
    {"id": "claude-opus-4-6", "provider": "anthropic", "label": "Opus 4.6 — most capable, highest cost"},
    {"id": "claude-opus-4-5-20250520", "provider": "anthropic", "label": "Opus 4.5 — strong reasoning, previous gen"},
    {"id": "claude-sonnet-4-6", "provider": "anthropic", "label": "Sonnet 4.6 — best cost/capability balance"},
    {"id": "claude-sonnet-4-5-20250514", "provider": "anthropic", "label": "Sonnet 4.5 — capable, previous gen"},
    {"id": "claude-haiku-4-5-20251001", "provider": "anthropic", "label": "Haiku 4.5 — fastest Anthropic option"},
    # OpenAI-compatible (cheap / open-source)
    {"id": "deepseek-chat", "provider": "openai", "label": "DeepSeek V3 — very cheap, strong reasoning"},
    {"id": "deepseek-reasoner", "provider": "openai", "label": "DeepSeek R1 — deep reasoning, low cost"},
    {"id": "gemini-2.5-flash", "provider": "openai", "label": "Gemini 2.5 Flash — ultra-cheap, fast (Google direct)"},
    {"id": "google/gemini-2.5-flash", "provider": "openai", "label": "Gemini 2.5 Flash — via OpenRouter/DeepInfra/Together"},
    {"id": "meta-llama/llama-4-maverick", "provider": "openai", "label": "Llama 4 Maverick — open-weight, self-hostable"},
    {"id": "qwen/qwen3-235b", "provider": "openai", "label": "Qwen 3 235B — open-weight, very capable"},
    {"id": "mistralai/mistral-large-latest", "provider": "openai", "label": "Mistral Large — strong European option"},
    {"id": "moonshotai/kimi-k2.5", "provider": "openai", "label": "Kimi K2.5 — strong agentic tool-calling, low cost"},
]

ANTHROPIC_MODEL_IDS: list[str] = [
    m["id"] for m in SUPPORTED_MODELS if m["provider"] == "anthropic"
]
OPENAI_MODEL_IDS: list[str] = [
    m["id"] for m in SUPPORTED_MODELS if m["provider"] == "openai"
]
MODEL_IDS: list[str] = [m["id"] for m in SUPPORTED_MODELS]

DEFAULT_MODEL: str = "claude-sonnet-4-6"
DEFAULT_PROVIDER: str = PROVIDER_ANTHROPIC

# Well-known base URLs for OpenAI-compatible providers.
KNOWN_OPENAI_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "deepseek": "https://api.deepseek.com/v1",
    "together": "https://api.together.xyz/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai/",
    "openrouter": "https://openrouter.ai/api/v1",
    "kimi": "https://api.moonshot.ai/v1",
    "ollama": "http://localhost:11434/v1",
}


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

    # LLM provider ("anthropic" or "openai" for OpenAI-compatible APIs)
    provider: str = DEFAULT_PROVIDER
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""  # required for non-OpenAI providers
    model: str = DEFAULT_MODEL
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
    gchat: IntegrationConfig = field(default_factory=IntegrationConfig)
    zoom: IntegrationConfig = field(default_factory=IntegrationConfig)
    email: IntegrationConfig = field(default_factory=IntegrationConfig)
    voice: IntegrationConfig = field(default_factory=IntegrationConfig)
    gdrive: IntegrationConfig = field(default_factory=IntegrationConfig)

    # ── factories ──────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> LLTPMConfig:
        """Build config from environment variables and an optional JSON file."""
        cfg = cls()
        cfg.provider = os.getenv("LLTPM_PROVIDER", cfg.provider)
        cfg.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        cfg.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        cfg.openai_base_url = os.getenv("OPENAI_BASE_URL", "")
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

        # Google Chat (webhook URL or service account key path)
        gchat_key = os.getenv("GCHAT_WEBHOOK_URL", "") or os.getenv("GCHAT_SERVICE_ACCOUNT_KEY", "")
        if gchat_key:
            cfg.gchat = IntegrationConfig(
                enabled=True,
                api_key=gchat_key,
                extra={"service_account_key": os.getenv("GCHAT_SERVICE_ACCOUNT_KEY", "")},
            )

        # Google Calendar
        gcal_key = os.getenv("GOOGLE_CALENDAR_SERVICE_ACCOUNT", "")
        if gcal_key:
            cfg.calendar = IntegrationConfig(
                enabled=True,
                api_key=gcal_key,
            )

        # Zoom
        zoom_key = os.getenv("ZOOM_API_KEY", "") or os.getenv("ZOOM_ACCOUNT_ID", "")
        if zoom_key:
            cfg.zoom = IntegrationConfig(
                enabled=True,
                api_key=zoom_key,
                extra={
                    "account_id": os.getenv("ZOOM_ACCOUNT_ID", ""),
                    "client_secret": os.getenv("ZOOM_CLIENT_SECRET", ""),
                },
            )

        # Email (SMTP or SendGrid)
        smtp_host = os.getenv("SMTP_HOST", "")
        sendgrid_key = os.getenv("SENDGRID_API_KEY", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")
        if smtp_host or sendgrid_key:
            backend = "sendgrid" if sendgrid_key else "smtp"
            cfg.email = IntegrationConfig(
                enabled=True,
                api_key=sendgrid_key or smtp_password,
                extra={
                    "backend": backend,
                    "smtp_host": smtp_host or "smtp.gmail.com",
                    "smtp_port": os.getenv("SMTP_PORT", "587"),
                    "smtp_user": os.getenv("SMTP_USER", ""),
                    "from_email": os.getenv("EMAIL_FROM", "lltpm@example.com"),
                    "from_name": os.getenv("EMAIL_FROM_NAME", "LLTPM Agent"),
                },
            )

        # Voice / TTS
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")
        voice_backend = os.getenv("VOICE_BACKEND", "")
        if elevenlabs_key or voice_backend:
            cfg.voice = IntegrationConfig(
                enabled=True,
                api_key=elevenlabs_key,
                extra={
                    "backend": voice_backend or ("elevenlabs" if elevenlabs_key else "system"),
                    "voice_id": os.getenv("VOICE_ID", ""),
                    "speed": os.getenv("VOICE_SPEED", "1.0"),
                },
            )

        # Google Drive
        gdrive_sa = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT", "")
        gdrive_folder = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
        if gdrive_sa or gdrive_folder:
            cfg.gdrive = IntegrationConfig(
                enabled=True,
                extra={
                    "service_account_key": gdrive_sa,
                    "folder_id": gdrive_folder,
                },
            )

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
