"""External service integrations — connectors to the tools TPMs live in."""

from lltpm.integrations.base import Integration, IntegrationError
from lltpm.integrations.github_integration import GitHubIntegration
from lltpm.integrations.jira_integration import JiraIntegration
from lltpm.integrations.slack_integration import SlackIntegration
from lltpm.integrations.linear_integration import LinearIntegration
from lltpm.integrations.calendar_integration import CalendarIntegration
from lltpm.integrations.gchat_integration import GoogleChatIntegration
from lltpm.integrations.zoom_integration import ZoomIntegration
from lltpm.integrations.email_integration import EmailIntegration
from lltpm.integrations.slack_bot import SlackBot
from lltpm.integrations.voice_integration import VoiceIntegration
from lltpm.integrations.gdrive_integration import GoogleDriveIntegration
from lltpm.integrations.notion_integration import NotionIntegration

# Commit 18 — tool-native integration runtime (vendor-neutral layer)
from lltpm.integrations.envelopes import (
    SECRET_TEXT_PLACEHOLDER,
    EvidenceEnvelope,
    build_idempotency_key,
    envelope_to_project_evidence,
    is_secret_metadata_key,
    make_envelope,
    redact_secret_text,
    sanitize_metadata,
)
# Commit 37 — the anti-corruption layer: provider payloads -> envelopes
from lltpm.integrations.normalizers import (
    CONTENT_EXCERPT_LIMIT,
    DEFAULT_BLOCKING_LABELS,
    RECOGNIZED_EVENT_CLASSES,
    NormalizationOutcome,
    ProjectBindings,
    SkippedPayload,
    SourceBinding,
    TimestampMissingError,
    github_pull_request_key,
    normalize_github_event,
    normalize_github_issue,
    normalize_github_payloads,
    normalize_github_pull_request,
    normalize_github_review,
    normalize_github_webhook,
    to_naive_utc,
)
from lltpm.integrations.protocols import (
    EvidenceReferenceStore,
    ProjectStateStore,
    ProjectionStore,
    QueryAdapterEvidenceSearchProvider,
    ToolEvidenceAdapter,
    ToolQueryAdapter,
)
from lltpm.integrations.registry import (
    IntegrationRegistry,
    WrappedToolAdapter,
    get_adapter,
    list_available_sources,
    register_adapter,
)
from lltpm.integrations.retention import (
    ArtifactPointer,
    RetentionClass,
    build_artifact_pointer,
    content_hash,
    minimize_envelope,
)
from lltpm.integrations.runtime import (
    InMemoryStateStore,
    IntelligenceUpdate,
    IntelligenceUpdateType,
    RefreshScope,
    determine_refresh_scope,
    process_evidence_envelopes,
)
from lltpm.integrations.surfaces import (
    SurfacePayloadType,
    ToolSurfacePayload,
    ToolSurfaceRenderer,
    build_surface_payload_for_update,
    should_surface_update,
)

__all__ = [
    "Integration", "IntegrationError",
    # Commit 18 — runtime layer
    "ArtifactPointer",
    "EvidenceEnvelope",
    "EvidenceReferenceStore",
    "InMemoryStateStore",
    "CONTENT_EXCERPT_LIMIT",
    "DEFAULT_BLOCKING_LABELS",
    "RECOGNIZED_EVENT_CLASSES",
    "NormalizationOutcome",
    "ProjectBindings",
    "SkippedPayload",
    "SourceBinding",
    "TimestampMissingError",
    "github_pull_request_key",
    "normalize_github_event",
    "normalize_github_issue",
    "normalize_github_payloads",
    "normalize_github_pull_request",
    "normalize_github_review",
    "normalize_github_webhook",
    "to_naive_utc",
    "IntegrationRegistry",
    "IntelligenceUpdate",
    "IntelligenceUpdateType",
    "ProjectStateStore",
    "ProjectionStore",
    "QueryAdapterEvidenceSearchProvider",
    "RefreshScope",
    "RetentionClass",
    "SurfacePayloadType",
    "ToolEvidenceAdapter",
    "ToolQueryAdapter",
    "ToolSurfacePayload",
    "ToolSurfaceRenderer",
    "WrappedToolAdapter",
    "build_artifact_pointer",
    "build_idempotency_key",
    "build_surface_payload_for_update",
    "content_hash",
    "determine_refresh_scope",
    "envelope_to_project_evidence",
    "get_adapter",
    "is_secret_metadata_key",
    "list_available_sources",
    "make_envelope",
    "redact_secret_text",
    "SECRET_TEXT_PLACEHOLDER",
    "minimize_envelope",
    "process_evidence_envelopes",
    "register_adapter",
    "sanitize_metadata",
    "should_surface_update",
    "GitHubIntegration",
    "JiraIntegration",
    "SlackIntegration",
    "LinearIntegration",
    "CalendarIntegration",
    "GoogleChatIntegration",
    "ZoomIntegration",
    "EmailIntegration",
    "SlackBot",
    "VoiceIntegration",
    "GoogleDriveIntegration",
    "NotionIntegration",
]
