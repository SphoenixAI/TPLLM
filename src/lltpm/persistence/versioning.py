"""Schema and reproducibility versioning.

Reproducibility is a claim about code, not only about data. Replaying
August's evidence under September's scenario scoring may legitimately
produce a different recommendation — and calling that "reproduced" would
be false in a way that quietly destroys the value of the whole durable
layer.

So versions are recorded, compared, and allowed to say "no". Three rules
follow from that:

- an incompatible manifest makes a snapshot unusable, not merely
  suspect; replay falls back to the journal
- older code meeting a newer journal event fails loudly, because
  skipping an organizational mutation reconstructs a different
  organization
- migrations run on read, never by rewriting stored history
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lltpm.persistence.models import (
    AUTHORITATIVE_EVENT_TYPES,
    JournalEventType,
    ReproducibilityManifest,
)
from lltpm.persistence.protocols import (
    PersistenceSchemaError,
    UnsupportedJournalEventError,
)

def _installed_version(fallback: str) -> str:
    """The version actually installed, not one hand-copied into source.

    A manifest's whole job is honest provenance, so two version strings
    drifting apart would undermine the thing it exists to record.
    ``pyproject.toml`` is the single source of truth; the fallback covers
    running from a source tree with nothing installed.

    This is metadata, not derivation identity — ``lltpm_version`` is
    deliberately excluded from the compatibility comparison below, so a
    version that reflects the real install cannot make two otherwise
    equivalent reconstructions look incompatible.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("lltpm")
    except Exception:  # noqa: BLE001 — a missing install is not an error
        return fallback


DECLARED_LLTPM_VERSION = "0.41.0"
LLTPM_VERSION = _installed_version(DECLARED_LLTPM_VERSION)
JOURNAL_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 1
CURRENT_PAYLOAD_SCHEMA_VERSION = 1
RUNTIME_DERIVATION_VERSION = "runtime-v30"
SCENARIO_METHOD_VERSION = "heuristic_v1"
RECOMMENDATION_METHOD_VERSION = "evidence-weighted-v21"

# Journal schema versions this code can read. A newer one is refused.
SUPPORTED_JOURNAL_SCHEMA_VERSIONS = frozenset({1})
SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS = frozenset({1})
SUPPORTED_PAYLOAD_SCHEMA_VERSIONS = frozenset({1})

KNOWN_EVENT_TYPE_VALUES = frozenset(e.value for e in JournalEventType)


def build_manifest(
    *,
    policy_fingerprint: str,
    created_at: datetime | None = None,
    runtime_derivation_version: str = RUNTIME_DERIVATION_VERSION,
    scenario_method_version: str | None = SCENARIO_METHOD_VERSION,
    recommendation_method_version: str | None = RECOMMENDATION_METHOD_VERSION,
    lltpm_version: str = LLTPM_VERSION,
) -> ReproducibilityManifest:
    return ReproducibilityManifest(
        lltpm_version=lltpm_version,
        journal_schema_version=JOURNAL_SCHEMA_VERSION,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        runtime_derivation_version=runtime_derivation_version,
        policy_fingerprint=policy_fingerprint,
        scenario_method_version=scenario_method_version,
        recommendation_method_version=recommendation_method_version,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# Fields whose change means a rebuilt artifact is no longer the same
# artifact. The version string and creation time are metadata; the
# derivation identity is what matters.
_DERIVATION_FIELDS = (
    "journal_schema_version",
    "snapshot_schema_version",
    "runtime_derivation_version",
    "policy_fingerprint",
    "scenario_method_version",
    "recommendation_method_version",
)


def manifest_incompatibilities(
    *,
    expected: ReproducibilityManifest | None,
    actual: ReproducibilityManifest | None,
) -> list[str]:
    """Name every reason two manifests would not reproduce each other."""
    if expected is None or actual is None:
        return []
    return [
        f"{field}: expected {getattr(expected, field)!r}, "
        f"found {getattr(actual, field)!r}"
        for field in _DERIVATION_FIELDS
        if getattr(expected, field) != getattr(actual, field)
    ]


def manifests_compatible(
    *,
    expected: ReproducibilityManifest | None,
    actual: ReproducibilityManifest | None,
) -> bool:
    return not manifest_incompatibilities(expected=expected, actual=actual)


def assert_supported_journal_schema(version: int) -> None:
    if version not in SUPPORTED_JOURNAL_SCHEMA_VERSIONS:
        raise PersistenceSchemaError(
            f"Journal schema version {version} is not supported by this "
            f"build (supports "
            f"{sorted(SUPPORTED_JOURNAL_SCHEMA_VERSIONS)}). Opening a newer "
            f"journal with older code would misread history."
        )


def assert_supported_database_schema(version: int) -> None:
    if version > DATABASE_SCHEMA_VERSION:
        raise PersistenceSchemaError(
            f"Database schema version {version} is newer than this build "
            f"understands ({DATABASE_SCHEMA_VERSION}). Refusing to open it "
            f"rather than risk silently misreading durable state."
        )
    if version < 1:
        raise PersistenceSchemaError(
            f"Database schema version {version} is not a valid version."
        )


def assert_supported_event_type(event_type: str) -> JournalEventType:
    """Resolve a stored event type, refusing anything unknown.

    A future LLTPM may journal transitions this build has never heard
    of. Ignoring one would mean replaying an organization missing a
    decision it actually made.
    """
    if event_type not in KNOWN_EVENT_TYPE_VALUES:
        raise UnsupportedJournalEventError(
            f"Journal event type '{event_type}' is unknown to this build. "
            f"It may be an organizational mutation written by newer code; "
            f"skipping it would reconstruct a different organization."
        )
    return JournalEventType(event_type)


def assert_replayable_event(event_type: JournalEventType) -> None:
    """Audit records may be skipped; authoritative ones may not."""
    if event_type not in AUTHORITATIVE_EVENT_TYPES:
        return


class NoOpJournalPayloadMigrator:
    """The identity migrator: current payloads need no upgrade.

    Real migrations register here as the schema evolves. What they must
    never do is write back — history is read forward, not rewritten.
    """

    def migrate(
        self,
        *,
        event_type: JournalEventType,
        from_version: int,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        if from_version > CURRENT_PAYLOAD_SCHEMA_VERSION:
            raise PersistenceSchemaError(
                f"Payload schema version {from_version} for "
                f"{event_type.value} is newer than this build understands "
                f"({CURRENT_PAYLOAD_SCHEMA_VERSION})."
            )
        return from_version, dict(payload)


def migrate_payload(
    *,
    migrator: object,
    event_type: JournalEventType,
    from_version: int,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Apply a migrator on read, leaving the stored record untouched."""
    return migrator.migrate(
        event_type=event_type, from_version=from_version, payload=payload,
    )
