"""Durable persistence models (Commit 31).

Commit 30 made TPLLM live. This layer makes it durable, and the
distinction that organizes everything here is:

    the journal is the durable historical record of organizational state
    transitions; everything derived from it is a rebuildable projection.

That single line decides most of the design. Journal records are
immutable and hash-chained. Runtime projections, artifacts, and
snapshots are caches — deleting all of them must be survivable, because
the acceptance test for this commit is exactly that: delete every
derived artifact, replay the journal, and get semantically equivalent
intelligence back.

Records are JSON-compatible throughout. No pickle: durable history a
person cannot read with ordinary tools is not durable in any useful
sense.

What is journalled is organizational transitions — evidence, identity
decisions, decisions, policy, outcomes. Never anybody's activity.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

# Deliberately duplicated from Commit 18 rather than imported: this
# package depends on no other first-party package, so a storage backend
# can never drag a domain along with it. The rule itself is the same —
# credentials do not belong in evidence, and they certainly do not
# belong in permanent history.
_SECRET_KEY_MARKERS = (
    "token", "secret", "password", "api_key", "apikey", "authorization",
    "signing_key", "credential",
)


def is_secret_metadata_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)

# ── Categories and event types ───────────────────────────────────────


class JournalRecordCategory(str, Enum):
    INGEST = "ingest"
    HUMAN_ACTION = "human_action"
    AGENT_OUTPUT = "agent_output"
    ORGANIZATIONAL_STATE = "organizational_state"
    POLICY = "policy"
    OUTCOME = "outcome"
    RUNTIME_AUDIT = "runtime_audit"
    SOURCE_STATE = "source_state"


class JournalEventType(str, Enum):
    """Durable organizational transitions.

    Deliberately not a log of every internal call. If replaying it would
    not change what the organization is or believes, it does not belong
    here.
    """
    EVIDENCE_ACCEPTED = "evidence_accepted"
    IDENTITY_LINKED = "identity_linked"
    IDENTITY_UNLINKED = "identity_unlinked"
    ENTITY_MERGE_APPLIED = "entity_merge_applied"
    ENTITY_MERGE_REVERSED = "entity_merge_reversed"
    AGENT_RECOMMENDATION_PUBLISHED = "agent_recommendation_published"
    AGENT_RECOMMENDATION_INVALIDATED = "agent_recommendation_invalidated"
    HUMAN_RECOMMENDATION_VALIDATED = "human_recommendation_validated"
    HUMAN_RECOMMENDATION_MODIFIED = "human_recommendation_modified"
    HUMAN_RECOMMENDATION_REJECTED = "human_recommendation_rejected"
    HUMAN_RECOMMENDATION_DEFERRED = "human_recommendation_deferred"
    ORGANIZATIONAL_DECISION_RECORDED = "organizational_decision_recorded"
    ORGANIZATIONAL_DECISION_REOPENED = "organizational_decision_reopened"
    ORGANIZATIONAL_DECISION_SUPERSEDED = "organizational_decision_superseded"
    AUTONOMOUS_DECISION_FINALIZED = "autonomous_decision_finalized"
    AUTONOMOUS_DECISION_BLOCKED = "autonomous_decision_blocked"
    AUTHORITY_POLICY_CHANGED = "authority_policy_changed"
    AUTONOMY_KILL_SWITCH_CHANGED = "autonomy_kill_switch_changed"
    OUTCOME_RECORDED = "outcome_recorded"
    SOURCE_AVAILABILITY_CHANGED = "source_availability_changed"
    INTELLIGENCE_REVISION_PUBLISHED = "intelligence_revision_published"
    DERIVED_ARTIFACT_PUBLISHED = "derived_artifact_published"


EVENT_TYPE_CATEGORY: dict[JournalEventType, JournalRecordCategory] = {
    JournalEventType.EVIDENCE_ACCEPTED: JournalRecordCategory.INGEST,
    JournalEventType.IDENTITY_LINKED: JournalRecordCategory.ORGANIZATIONAL_STATE,
    JournalEventType.IDENTITY_UNLINKED: (
        JournalRecordCategory.ORGANIZATIONAL_STATE
    ),
    JournalEventType.ENTITY_MERGE_APPLIED: (
        JournalRecordCategory.ORGANIZATIONAL_STATE
    ),
    JournalEventType.ENTITY_MERGE_REVERSED: (
        JournalRecordCategory.ORGANIZATIONAL_STATE
    ),
    JournalEventType.AGENT_RECOMMENDATION_PUBLISHED: (
        JournalRecordCategory.AGENT_OUTPUT
    ),
    JournalEventType.AGENT_RECOMMENDATION_INVALIDATED: (
        JournalRecordCategory.AGENT_OUTPUT
    ),
    JournalEventType.HUMAN_RECOMMENDATION_VALIDATED: (
        JournalRecordCategory.HUMAN_ACTION
    ),
    JournalEventType.HUMAN_RECOMMENDATION_MODIFIED: (
        JournalRecordCategory.HUMAN_ACTION
    ),
    JournalEventType.HUMAN_RECOMMENDATION_REJECTED: (
        JournalRecordCategory.HUMAN_ACTION
    ),
    JournalEventType.HUMAN_RECOMMENDATION_DEFERRED: (
        JournalRecordCategory.HUMAN_ACTION
    ),
    JournalEventType.ORGANIZATIONAL_DECISION_RECORDED: (
        JournalRecordCategory.ORGANIZATIONAL_STATE
    ),
    JournalEventType.ORGANIZATIONAL_DECISION_REOPENED: (
        JournalRecordCategory.ORGANIZATIONAL_STATE
    ),
    JournalEventType.ORGANIZATIONAL_DECISION_SUPERSEDED: (
        JournalRecordCategory.ORGANIZATIONAL_STATE
    ),
    JournalEventType.AUTONOMOUS_DECISION_FINALIZED: (
        JournalRecordCategory.ORGANIZATIONAL_STATE
    ),
    JournalEventType.AUTONOMOUS_DECISION_BLOCKED: (
        JournalRecordCategory.RUNTIME_AUDIT
    ),
    JournalEventType.AUTHORITY_POLICY_CHANGED: JournalRecordCategory.POLICY,
    JournalEventType.AUTONOMY_KILL_SWITCH_CHANGED: (
        JournalRecordCategory.POLICY
    ),
    JournalEventType.OUTCOME_RECORDED: JournalRecordCategory.OUTCOME,
    JournalEventType.SOURCE_AVAILABILITY_CHANGED: (
        JournalRecordCategory.SOURCE_STATE
    ),
    JournalEventType.INTELLIGENCE_REVISION_PUBLISHED: (
        JournalRecordCategory.RUNTIME_AUDIT
    ),
    JournalEventType.DERIVED_ARTIFACT_PUBLISHED: (
        JournalRecordCategory.RUNTIME_AUDIT
    ),
}

# Records that describe something the organization *did* or *learned*.
# A replay that skipped one of these would reconstruct a different
# organization, so unknown members of this class must fail loudly rather
# than be ignored.
AUTHORITATIVE_EVENT_TYPES = frozenset(
    event_type for event_type, category in EVENT_TYPE_CATEGORY.items()
    if category != JournalRecordCategory.RUNTIME_AUDIT
)


# ── Position and record ──────────────────────────────────────────────


class JournalPosition(BaseModel):
    """Where a record sits, by sequence rather than by clock.

    Wall-clock time is not an ordering: two events can share a
    millisecond, clocks drift, and a late webhook carries an old
    timestamp. ``global_sequence`` is what replay follows.
    """
    global_sequence: int
    stream_id: str
    stream_sequence: int

    @model_validator(mode="after")
    def _check(self) -> "JournalPosition":
        if self.global_sequence < 1:
            raise ValueError("global_sequence starts at 1.")
        if self.stream_sequence < 1:
            raise ValueError("stream_sequence starts at 1.")
        if not self.stream_id:
            raise ValueError("A journal record must belong to a stream.")
        return self


def organization_stream(organization_id: str) -> str:
    return f"organization:{organization_id}"


def project_stream(project_id: str) -> str:
    return f"project:{project_id}"


def policy_stream(organization_id: str) -> str:
    return f"policy:{organization_id}"


class SecretMaterialError(ValueError):
    """Raised when a payload carries something that must never be
    persisted as history."""


_SECRET_VALUE_MARKERS = (
    "-----begin ", "bearer ", "xoxb-", "xoxp-", "ghp_", "sk-",
)


def assert_no_secret_material(payload: dict[str, Any]) -> None:
    """Refuse obvious credential material anywhere in a payload.

    Commit 18 keeps tokens out of evidence; the journal is the place
    where a leak would be permanent, so the same rule is enforced here
    at the model level rather than by convention.
    """

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if is_secret_metadata_key(str(key)):
                    raise SecretMaterialError(
                        f"Payload key '{path}{key}' looks like a secret — "
                        f"credentials must never enter the durable journal."
                    )
                walk(value, f"{path}{key}.")
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}{index}.")
        elif isinstance(node, str):
            lowered = node.strip().lower()
            for marker in _SECRET_VALUE_MARKERS:
                if lowered.startswith(marker):
                    raise SecretMaterialError(
                        f"Payload value at '{path.rstrip('.')}' looks like "
                        f"credential material and must not be journalled."
                    )

    walk(payload, "")


class JournalRecord(BaseModel):
    """One immutable durable fact.

    A committed record is never edited. A wrong decision is corrected by
    appending a reopen or supersession, not by rewriting what was
    recorded — the point of history is that it says what happened, not
    what we later wished had happened.
    """
    id: str
    position: JournalPosition
    category: JournalRecordCategory
    event_type: JournalEventType
    organization_id: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    occurred_at: datetime
    recorded_at: datetime
    causal_event_id: str | None = None
    idempotency_key: str
    payload_schema_version: int
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    previous_record_hash: str | None = None
    record_hash: str = ""
    runtime_revision_id: str | None = None

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _check(self) -> "JournalRecord":
        if not self.idempotency_key:
            raise ValueError(
                "Every journal record needs an idempotency key; without one "
                "a redelivered webhook becomes a second piece of history."
            )
        if self.payload_schema_version < 1:
            raise ValueError("payload_schema_version starts at 1.")
        if self.recorded_at < self.occurred_at:
            raise ValueError(
                "recorded_at precedes occurred_at — a fact cannot be "
                "recorded before it happened."
            )
        assert_no_secret_material(self.payload)
        return self


def build_journal_idempotency_key(
    *,
    event_type: JournalEventType,
    source_key: str,
) -> str:
    """Deterministic: the same transition always yields the same key.

    Four deliveries of one Slack event produce one EVIDENCE_ACCEPTED
    record, because all four compute this same string.
    """
    return f"{event_type.value}:{source_key}"


# ── Durable evidence ─────────────────────────────────────────────────


class JournalRetentionClass(str, Enum):
    """How much of a source artifact the journal keeps.

    ``NORMALIZED`` is the reference default: enough to reconstruct what
    the organization concluded, without becoming a permanent mirror of
    everybody's Slack.
    """
    MINIMAL = "minimal"
    NORMALIZED = "normalized"
    FULL_INTERNAL = "full_internal"


class SourceAvailability(str, Enum):
    AVAILABLE = "available"
    DELETED = "deleted"
    UNREACHABLE = "unreachable"
    ACCESS_REVOKED = "access_revoked"


class DurableEvidenceRecord(BaseModel):
    """Normalized evidence, durable independently of its source system.

    Replay must not require Slack, Jira, or a meeting recorder to still
    be reachable. Everything needed to reconstruct what the organization
    concluded lives here; the original artifact stays in its system of
    record behind ``source_pointer``.
    """
    evidence_id: str
    source_system: str
    source_type: str
    source_id: str
    event_type: str
    observed_at: datetime
    received_at: datetime
    project_ids: list[str] = Field(default_factory=list)
    canonical_entity_ids: list[str] = Field(default_factory=list)
    source_pointer: str | None = None
    content_hash: str | None = None
    normalized_content: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    retention_class: str = JournalRetentionClass.NORMALIZED.value
    idempotency_key: str = ""

    @model_validator(mode="after")
    def _check(self) -> "DurableEvidenceRecord":
        assert_no_secret_material(self.metadata)
        return self

    @property
    def content_omitted_by_retention(self) -> bool:
        """True when policy deliberately dropped the content.

        Replay reports this as an evidence limitation rather than
        pretending the content was never there.
        """
        return (
            self.normalized_content is None
            and self.retention_class == JournalRetentionClass.MINIMAL.value
        )


# ── Reproducibility ──────────────────────────────────────────────────


class ReproducibilityManifest(BaseModel):
    """What produced a piece of intelligence.

    Reproducibility is a claim about code as much as about data. If the
    scenario method changed between then and now, replaying the same
    evidence may legitimately produce a different answer — and saying
    "reproduced" would be false.
    """
    lltpm_version: str
    journal_schema_version: int
    snapshot_schema_version: int
    runtime_derivation_version: str
    policy_fingerprint: str
    scenario_method_version: str | None = None
    recommendation_method_version: str | None = None
    created_at: datetime


# ── Runtime projections (storage DTOs) ───────────────────────────────


class ProjectRuntimeStateRecord(BaseModel):
    """A project's runtime state as stored.

    Held as an opaque JSON ``state`` blob plus indexed columns, so the
    persistence layer never has to import the runtime's models — which
    would reverse the dependency and create a cycle.
    """
    project_id: str
    organization_id: str | None = None
    revision_id: str | None = None
    state_fingerprint: str = ""
    state: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class PortfolioRuntimeStateRecord(BaseModel):
    organization_id: str
    revision_id: str | None = None
    state_fingerprint: str = ""
    state: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class RuntimeRevisionRecord(BaseModel):
    revision_id: str
    organization_id: str
    complete: bool = False
    is_current: bool = False
    parent_revision_id: str | None = None
    evidence_cutoff_at: datetime | None = None
    created_at: datetime | None = None
    state_fingerprint: str = ""
    state_fingerprints: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class IntelligenceArtifactRecord(BaseModel):
    """A cached derived artifact.

    Historically published agent output is kept because "what did TPLLM
    recommend on August 18?" and "what would today's engine recommend
    from August 18's evidence?" are different questions, and only the
    first one has a fixed answer.
    """
    artifact_type: str
    artifact_id: str
    revision_id: str
    fingerprint: str
    payload: dict[str, Any] = Field(default_factory=dict)
    derivation_version: str | None = None
    policy_fingerprint: str | None = None
    created_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.artifact_type}:{self.artifact_id}"


# ── Snapshots ────────────────────────────────────────────────────────


class RuntimeSnapshot(BaseModel):
    """An accelerator, never a source of truth.

    A snapshot that disagrees with the journal is wrong, and the journal
    wins. Snapshots exist only so replay does not have to start at
    record one forever.
    """
    id: str
    organization_id: str
    journal_global_sequence: int
    active_revision_id: str
    project_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    portfolio_state: dict[str, Any] | None = None
    state_fingerprints: dict[str, str] = Field(default_factory=dict)
    manifest: ReproducibilityManifest
    created_at: datetime
    snapshot_hash: str = ""

    @model_validator(mode="after")
    def _check(self) -> "RuntimeSnapshot":
        if self.journal_global_sequence < 0:
            raise ValueError(
                "A snapshot cannot sit before the start of the journal."
            )
        return self


# ── Replay ───────────────────────────────────────────────────────────


class ReplayMode(str, Enum):
    RECONSTRUCT_CURRENT = "reconstruct_current"
    VERIFY_EQUIVALENCE = "verify_equivalence"
    REEVALUATE_WITH_CURRENT = "reevaluate_with_current"


class ExecutionMode(str, Enum):
    """Whether this run may touch the outside world.

    ``REPLAY`` is the fence. Rebuilding history must never re-send a
    Slack message, re-file a ticket, or re-finalize a decision that was
    already made once.
    """
    LIVE = "live"
    REPLAY = "replay"


class ReplayClock(BaseModel):
    """Deterministic time for reconstruction.

    Historical reconstruction uses the events' own timestamps. A replay
    that consulted the wall clock would produce a different answer every
    time it ran, which is the opposite of the point.
    """
    replay_as_of: datetime

    def now(self) -> datetime:
        return self.replay_as_of


class ExecutionContext(BaseModel):
    """Deterministic controls handed to everything a replay drives."""
    mode: ExecutionMode = ExecutionMode.LIVE
    clock: ReplayClock
    random_seed: int = 0
    manifest: ReproducibilityManifest | None = None
    suppress_side_effects: bool = False

    @model_validator(mode="after")
    def _check(self) -> "ExecutionContext":
        if self.mode == ExecutionMode.REPLAY and not self.suppress_side_effects:
            raise ValueError(
                "A replay context must suppress side effects — replaying "
                "history must never repeat its external consequences."
            )
        return self

    @property
    def is_replay(self) -> bool:
        return self.mode == ExecutionMode.REPLAY


def derive_simulation_seed(
    *,
    revision_id: str,
    project_id: str,
    scenario_input_fingerprint: str,
) -> int:
    """A stable seed from semantic state.

    Deriving the seed rather than storing a random one means a
    reconstructed scenario draws exactly the samples the original did.
    """
    import hashlib

    digest = hashlib.sha256(
        f"{revision_id}|{project_id}|{scenario_input_fingerprint}".encode()
    ).hexdigest()
    return int(digest[:16], 16)


class ReplayRequest(BaseModel):
    """Safe by default: replay reports, and only publishes if asked."""
    organization_id: str
    mode: ReplayMode = ReplayMode.RECONSTRUCT_CURRENT
    from_global_sequence: int | None = None
    to_global_sequence: int | None = None
    use_snapshot: bool = True
    expected_manifest: ReproducibilityManifest | None = None
    publish_result: bool = False

    @model_validator(mode="after")
    def _check(self) -> "ReplayRequest":
        if (
            self.from_global_sequence is not None
            and self.to_global_sequence is not None
            and self.to_global_sequence < self.from_global_sequence
        ):
            raise ValueError("to_global_sequence precedes from_global_sequence.")
        if (
            self.mode == ReplayMode.REEVALUATE_WITH_CURRENT
            and self.publish_result
        ):
            raise ValueError(
                "Re-evaluation under current code must not overwrite "
                "historical agent output; it belongs in its own analysis "
                "namespace."
            )
        return self


class ReplayResult(BaseModel):
    organization_id: str
    mode: ReplayMode
    records_replayed: int = 0
    snapshot_id: str | None = None
    start_sequence: int = 0
    end_sequence: int = 0
    reconstructed_revision_id: str | None = None
    expected_fingerprints: dict[str, str] = Field(default_factory=dict)
    reconstructed_fingerprints: dict[str, str] = Field(default_factory=dict)
    mismatches: list[str] = Field(default_factory=list)
    side_effects_suppressed: int = 0
    manifest_compatible: bool = True
    complete: bool = False
    corruption_detected_at: int | None = None
    evidence_limitations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    published: bool = False

    @property
    def equivalent(self) -> bool:
        return self.complete and not self.mismatches


class ReplayEquivalenceReport(BaseModel):
    equivalent: bool
    expected_state_fingerprints: dict[str, str] = Field(default_factory=dict)
    actual_state_fingerprints: dict[str, str] = Field(default_factory=dict)
    missing_artifacts: list[str] = Field(default_factory=list)
    extra_artifacts: list[str] = Field(default_factory=list)
    changed_artifacts: list[str] = Field(default_factory=list)
    manifest_compatible: bool = True


# ── Health and outbox ────────────────────────────────────────────────


class PersistenceHealthReport(BaseModel):
    journal_head_sequence: int = 0
    journal_hash_valid: bool = True
    latest_snapshot_id: str | None = None
    latest_snapshot_valid: bool | None = None
    current_revision_id: str | None = None
    last_successful_replay_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.journal_hash_valid and not self.warnings


class OutboundIntentStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class DurableOutboundIntent(BaseModel):
    """A recorded intent to touch an external system.

    The seam for exactly-once-ish external mutation later. Recording the
    intent durably, keyed by idempotency, is what will eventually let a
    retry after a crash avoid sending the same message twice. Delivery
    itself is deliberately not implemented here.
    """
    id: str
    originating_journal_record_id: str
    target_system: str
    action_type: str
    payload_hash: str
    status: OutboundIntentStatus = OutboundIntentStatus.PENDING
    idempotency_key: str
    created_at: datetime | None = None
    suppressed_reason: str | None = None


# ── Configuration ────────────────────────────────────────────────────


class SQLitePersistenceConfig(BaseModel):
    """The open-source default: one local file, no cloud account."""
    path: str = "./lltpm.db"
    enable_wal: bool = True
    synchronous: str = "FULL"
    timeout_seconds: float = 5.0

    @property
    def is_memory(self) -> bool:
        return self.path == ":memory:" or self.path.startswith("file::memory:")
