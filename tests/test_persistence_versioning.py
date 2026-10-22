"""Versioning, migration, and honest reproduction.

Replaying August's evidence under September's scenario scoring may
legitimately produce a different recommendation. Calling that
"reproduced" would be false, so versions are recorded, compared, and
allowed to say no.
"""

from __future__ import annotations

import pytest

from tpllm.persistence import (
    CURRENT_PAYLOAD_SCHEMA_VERSION,
    DATABASE_SCHEMA_VERSION,
    JOURNAL_SCHEMA_VERSION,
    KNOWN_EVENT_TYPE_VALUES,
    SNAPSHOT_SCHEMA_VERSION,
    JournalEventType,
    NoOpJournalPayloadMigrator,
    PersistenceSchemaError,
    UnsupportedJournalEventError,
    assert_supported_database_schema,
    assert_supported_event_type,
    assert_supported_journal_schema,
    build_manifest,
    manifest_incompatibilities,
    manifests_compatible,
    migrate_payload,
)
from tests._persistence_fixtures import manifest


class TestVersionConstants:
    def test_schema_versions_are_positive(self):
        assert JOURNAL_SCHEMA_VERSION >= 1
        assert SNAPSHOT_SCHEMA_VERSION >= 1
        assert DATABASE_SCHEMA_VERSION >= 1
        assert CURRENT_PAYLOAD_SCHEMA_VERSION >= 1

    def test_the_library_version_tracks_the_installed_package(self):
        """pyproject.toml is the single source of truth.

        A manifest records provenance; a hand-copied version string that
        drifts from the package would quietly make that record false.
        """
        from importlib.metadata import PackageNotFoundError, version

        from tpllm.persistence import TPLLM_VERSION

        try:
            installed = version("tpllm")
        except PackageNotFoundError:
            pytest.skip("tpllm is not installed; nothing to compare against")
        assert TPLLM_VERSION == installed

    def test_a_declared_fallback_exists_for_source_trees(self):
        from tpllm.persistence.versioning import DECLARED_TPLLM_VERSION

        assert DECLARED_TPLLM_VERSION

    def test_the_manifest_reports_the_same_version(self):
        from tpllm.persistence import TPLLM_VERSION

        assert build_manifest(
            policy_fingerprint="fp",
        ).tpllm_version == TPLLM_VERSION

    def test_a_manifest_carries_every_derivation_input(self):
        built = manifest()
        assert built.tpllm_version
        assert built.runtime_derivation_version
        assert built.policy_fingerprint
        assert built.scenario_method_version
        assert built.recommendation_method_version

    def test_the_manifest_records_both_schema_versions(self):
        built = manifest()
        assert built.journal_schema_version == JOURNAL_SCHEMA_VERSION
        assert built.snapshot_schema_version == SNAPSHOT_SCHEMA_VERSION


class TestManifestCompatibility:
    def test_identical_manifests_are_compatible(self):
        assert manifests_compatible(
            expected=manifest(), actual=manifest(),
        ) is True

    def test_a_changed_scenario_method_is_incompatible(self):
        differences = manifest_incompatibilities(
            expected=manifest(),
            actual=manifest(scenario_method_version="learned_v2"),
        )
        assert differences
        assert "scenario_method_version" in differences[0]

    def test_a_changed_recommendation_method_is_incompatible(self):
        assert manifests_compatible(
            expected=manifest(),
            actual=manifest(recommendation_method_version="v22"),
        ) is False

    def test_a_changed_policy_fingerprint_is_incompatible(self):
        assert manifests_compatible(
            expected=manifest(),
            actual=manifest(policy_fingerprint="policy-fp-v2"),
        ) is False

    def test_a_library_version_bump_alone_is_compatible(self):
        assert manifests_compatible(
            expected=manifest(), actual=manifest(tpllm_version="0.99.0"),
        ) is True, (
            "the version string is metadata; derivation identity is what "
            "decides whether the same answer would be produced"
        )

    def test_a_missing_manifest_asserts_nothing(self):
        assert manifests_compatible(expected=None, actual=manifest()) is True
        assert manifests_compatible(expected=manifest(), actual=None) is True

    def test_incompatibilities_name_both_sides(self):
        differences = manifest_incompatibilities(
            expected=manifest(runtime_derivation_version="runtime-v30"),
            actual=manifest(runtime_derivation_version="runtime-v31"),
        )
        assert "runtime-v30" in differences[0]
        assert "runtime-v31" in differences[0]

    def test_several_differences_are_all_reported(self):
        differences = manifest_incompatibilities(
            expected=manifest(),
            actual=manifest(
                runtime_derivation_version="runtime-v31",
                scenario_method_version="learned_v2",
            ),
        )
        assert len(differences) == 2


class TestSchemaGuards:
    def test_a_supported_journal_schema_passes(self):
        assert_supported_journal_schema(JOURNAL_SCHEMA_VERSION)

    def test_a_newer_journal_schema_is_refused(self):
        with pytest.raises(PersistenceSchemaError, match="not supported"):
            assert_supported_journal_schema(99)

    def test_a_supported_database_schema_passes(self):
        assert_supported_database_schema(DATABASE_SCHEMA_VERSION)

    def test_a_newer_database_schema_is_refused(self):
        with pytest.raises(PersistenceSchemaError, match="newer than this"):
            assert_supported_database_schema(DATABASE_SCHEMA_VERSION + 1)

    def test_an_invalid_database_schema_is_refused(self):
        with pytest.raises(PersistenceSchemaError, match="not a valid"):
            assert_supported_database_schema(0)

    def test_the_refusal_explains_the_risk(self):
        with pytest.raises(PersistenceSchemaError, match="misread"):
            assert_supported_database_schema(DATABASE_SCHEMA_VERSION + 1)


class TestUnknownEventTypes:
    def test_a_known_event_type_resolves(self):
        assert assert_supported_event_type("evidence_accepted") == (
            JournalEventType.EVIDENCE_ACCEPTED
        )

    def test_an_unknown_future_event_fails_loudly(self):
        with pytest.raises(
            UnsupportedJournalEventError, match="unknown to this build",
        ):
            assert_supported_event_type("organizational_merger_completed")

    def test_the_refusal_explains_why_skipping_is_unsafe(self):
        with pytest.raises(
            UnsupportedJournalEventError,
            match="reconstruct a different organization",
        ):
            assert_supported_event_type("some_future_event")

    def test_every_declared_event_type_is_known(self):
        for event_type in JournalEventType:
            assert event_type.value in KNOWN_EVENT_TYPE_VALUES
            assert assert_supported_event_type(event_type.value)


class TestMigration:
    def test_a_current_payload_passes_through_unchanged(self):
        migrator = NoOpJournalPayloadMigrator()
        version, payload = migrator.migrate(
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            from_version=1,
            payload={"evidence": {"evidence_id": "ev-1"}},
        )
        assert version == 1
        assert payload == {"evidence": {"evidence_id": "ev-1"}}

    def test_migration_returns_a_copy_not_the_original(self):
        migrator = NoOpJournalPayloadMigrator()
        original = {"a": 1}
        _, migrated = migrator.migrate(
            event_type=JournalEventType.EVIDENCE_ACCEPTED,
            from_version=1, payload=original,
        )
        migrated["a"] = 2
        assert original["a"] == 1, (
            "migration happens on read; it must not rewrite what is stored"
        )

    def test_a_newer_payload_version_is_refused(self):
        with pytest.raises(PersistenceSchemaError, match="newer than this"):
            NoOpJournalPayloadMigrator().migrate(
                event_type=JournalEventType.EVIDENCE_ACCEPTED,
                from_version=99, payload={},
            )

    def test_the_helper_delegates_to_the_migrator(self):
        version, payload = migrate_payload(
            migrator=NoOpJournalPayloadMigrator(),
            event_type=JournalEventType.OUTCOME_RECORDED,
            from_version=1,
            payload={"outcome_id": "o1"},
        )
        assert version == 1
        assert payload == {"outcome_id": "o1"}

    def test_a_custom_migrator_can_upgrade_a_payload(self):
        class RenameMigrator:
            def migrate(self, *, event_type, from_version, payload):
                if from_version == 1:
                    upgraded = dict(payload)
                    upgraded["outcome_value"] = upgraded.pop(
                        "observed_value", None,
                    )
                    return 2, upgraded
                return from_version, dict(payload)

        version, payload = migrate_payload(
            migrator=RenameMigrator(),
            event_type=JournalEventType.OUTCOME_RECORDED,
            from_version=1,
            payload={"observed_value": "late"},
        )
        assert version == 2
        assert payload == {"outcome_value": "late"}


class TestManifestBuilder:
    def test_the_builder_accepts_overrides(self):
        built = build_manifest(
            policy_fingerprint="fp",
            runtime_derivation_version="runtime-v99",
        )
        assert built.runtime_derivation_version == "runtime-v99"
        assert built.policy_fingerprint == "fp"

    def test_the_builder_is_deterministic(self):
        assert build_manifest(policy_fingerprint="fp") == build_manifest(
            policy_fingerprint="fp"
        )
