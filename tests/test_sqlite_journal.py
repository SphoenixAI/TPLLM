"""The SQLite reference implementation.

The open-source default: one local file, standard-library sqlite3, no
cloud account. These tests hold it to exactly the same contract as the
in-memory store, plus the two things only a real database has to
prove — that data survives closing the process, and that a newer schema
is refused rather than misread.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from tpllm.persistence import (
    DATABASE_SCHEMA_VERSION,
    JournalConcurrencyError,
    JournalEventType,
    JournalIdempotencyError,
    PersistenceSchemaError,
    SQLitePersistenceConfig,
    project_stream,
    verify_journal,
)
from tpllm.persistence.sqlite import (
    SQLitePersistence,
    initialize_sqlite_persistence,
)
from tests._persistence_fixtures import ORGANIZATION, at, record


@pytest.fixture
def memory_db():
    db = initialize_sqlite_persistence(
        SQLitePersistenceConfig(path=":memory:")
    )
    yield db
    db.close()


@pytest.fixture
def file_db(tmp_path):
    config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
    db = initialize_sqlite_persistence(config)
    yield db, config
    db.close()


class TestBootstrap:
    def test_an_in_memory_database_initializes(self, memory_db):
        assert memory_db.database_schema_version() == DATABASE_SCHEMA_VERSION

    def test_a_file_database_initializes(self, file_db):
        db, config = file_db
        assert db.database_schema_version() == DATABASE_SCHEMA_VERSION

    def test_initialization_is_idempotent(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        first = initialize_sqlite_persistence(config)
        first.journal.append(record=record(source_key="a"))
        first.close()

        second = initialize_sqlite_persistence(config)
        assert len(second.journal) == 1, (
            "running setup twice must not damage data — the common case is "
            "a process restarting"
        )
        second.close()

    def test_every_table_exists(self, memory_db):
        names = {
            row["name"] for row in memory_db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "journal_records", "journal_stream_heads", "runtime_revisions",
            "project_runtime_state", "portfolio_runtime_state",
            "intelligence_artifacts", "runtime_snapshots",
            "persistence_metadata",
        } <= names

    def test_the_expected_indexes_exist(self, memory_db):
        names = {
            row["name"] for row in memory_db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert {
            "ix_journal_stream", "ix_journal_idempotency",
            "ix_journal_event_type", "ix_journal_organization",
            "ix_journal_recorded_at",
        } <= names

    def test_a_newer_schema_is_refused(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        db = initialize_sqlite_persistence(config)
        db.connection.execute(
            "UPDATE persistence_metadata SET value = ? WHERE key = ?",
            ("99", "database_schema_version"),
        )
        db.close()
        with pytest.raises(PersistenceSchemaError, match="newer than this"):
            initialize_sqlite_persistence(config)

    def test_the_config_recognizes_memory(self):
        assert SQLitePersistenceConfig(path=":memory:").is_memory is True
        assert SQLitePersistenceConfig(path="./tpllm.db").is_memory is False


class TestDurability:
    def test_records_survive_closing_and_reopening(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        first = initialize_sqlite_persistence(config)
        for i in range(3):
            first.journal.append(record=record(source_key=f"k{i}"))
        head = first.journal.latest_position().global_sequence
        first.close()

        reopened = initialize_sqlite_persistence(config)
        assert reopened.journal.latest_position().global_sequence == head
        assert len(reopened.journal) == 3
        reopened.close()

    def test_the_hash_chain_survives_a_reopen(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        first = initialize_sqlite_persistence(config)
        for i in range(4):
            first.journal.append(record=record(source_key=f"k{i}"))
        first.close()

        reopened = initialize_sqlite_persistence(config)
        assert verify_journal(journal=reopened.journal).valid is True
        reopened.close()

    def test_payloads_round_trip_exactly(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        payload = {"decision_id": "dec-1", "options": ["a", "b"], "n": 3}
        first = initialize_sqlite_persistence(config)
        first.journal.append(record=record(source_key="a", payload=payload))
        first.close()

        reopened = initialize_sqlite_persistence(config)
        assert reopened.journal.read_from(
            global_sequence=1,
        )[0].payload == payload
        reopened.close()

    def test_persisted_payloads_are_readable_json_not_pickle(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        db = initialize_sqlite_persistence(config)
        db.journal.append(
            record=record(source_key="a", payload={"decision_id": "dec-1"})
        )
        db.close()

        raw = sqlite3.connect(config.path)
        stored = raw.execute("SELECT payload FROM journal_records").fetchone()[0]
        raw.close()
        assert isinstance(stored, str)
        assert "dec-1" in stored, (
            "durable history a person cannot read with ordinary tools is not "
            "durable in any useful sense"
        )

    def test_one_file_holds_everything(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        db = initialize_sqlite_persistence(config)
        db.journal.append(record=record(source_key="a"))
        db.artifacts.put(
            artifact_type="scenario", artifact_id="s1",
            revision_id="revision-1", payload={"p": 1}, fingerprint="fp",
        )
        db.close()
        assert (tmp_path / "tpllm.db").exists()


class TestJournalContract:
    def test_sequences_allocate_correctly(self, memory_db):
        first = memory_db.journal.append(record=record(source_key="a"))
        second = memory_db.journal.append(record=record(source_key="b"))
        assert first.position.global_sequence == 1
        assert second.position.global_sequence == 2

    def test_stream_sequences_are_independent(self, memory_db):
        memory_db.journal.append(record=record(
            source_key="a", stream_id=project_stream("project-a"),
        ))
        other = memory_db.journal.append(record=record(
            source_key="b", stream_id=project_stream("project-b"),
        ))
        assert other.position.stream_sequence == 1

    def test_the_chain_links_forward(self, memory_db):
        first = memory_db.journal.append(record=record(source_key="a"))
        second = memory_db.journal.append(record=record(source_key="b"))
        assert second.previous_record_hash == first.record_hash

    def test_a_duplicate_key_yields_one_record(self, memory_db):
        for _ in range(4):
            memory_db.journal.append(record=record(source_key="slack:1"))
        assert len(memory_db.journal) == 1

    def test_a_reused_key_with_new_content_is_refused(self, memory_db):
        memory_db.journal.append(record=record(source_key="a", payload={"n": 1}))
        with pytest.raises(JournalIdempotencyError):
            memory_db.journal.append(
                record=record(source_key="a", payload={"n": 2})
            )

    def test_optimistic_concurrency_conflicts(self, memory_db):
        memory_db.journal.append(
            record=record(source_key="a"), expected_stream_sequence=0,
        )
        with pytest.raises(JournalConcurrencyError, match="committed first"):
            memory_db.journal.append(
                record=record(source_key="b"), expected_stream_sequence=0,
            )

    def test_a_refused_append_leaves_no_partial_row(self, memory_db):
        memory_db.journal.append(
            record=record(source_key="a"), expected_stream_sequence=0,
        )
        with pytest.raises(JournalConcurrencyError):
            memory_db.journal.append(
                record=record(source_key="b"), expected_stream_sequence=0,
            )
        assert len(memory_db.journal) == 1
        assert memory_db.journal.stream_head(
            "organization:acme"
        ) == 1

    def test_concurrent_writers_produce_one_winner(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        db = initialize_sqlite_persistence(config)
        db.journal.append(
            record=record(source_key="seed"), expected_stream_sequence=0,
        )
        results: list[str] = []

        def write(key: str):
            try:
                db.journal.append(
                    record=record(source_key=key),
                    expected_stream_sequence=1,
                )
                results.append("ok")
            except JournalConcurrencyError:
                results.append("conflict")

        threads = [
            threading.Thread(target=write, args=(f"w{i}",)) for i in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == ["conflict", "ok"]
        db.close()

    def test_concurrent_duplicates_produce_one_record(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        db = initialize_sqlite_persistence(config)
        errors: list[Exception] = []

        def append():
            try:
                db.journal.append(record=record(source_key="shared"))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=append) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert len(db.journal) == 1
        db.close()


class TestIndexedLookups:
    def _seed(self, db):
        db.journal.append(record=record(source_key="a"))
        db.journal.append(record=record(
            source_key="b", event_type=JournalEventType.OUTCOME_RECORDED,
            payload={"outcome_id": "o1"},
        ))
        db.journal.append(record=record(
            source_key="c", stream_id=project_stream("project-a"),
        ))

    def test_lookup_by_idempotency_key(self, memory_db):
        committed = memory_db.journal.append(record=record(source_key="a"))
        found = memory_db.journal.find_by_idempotency_key(
            committed.idempotency_key
        )
        assert found.id == committed.id

    def test_lookup_by_record_id(self, memory_db):
        committed = memory_db.journal.append(record=record(source_key="a"))
        assert memory_db.journal.get(committed.id).id == committed.id

    def test_an_unknown_id_returns_none(self, memory_db):
        assert memory_db.journal.get("nope") is None

    def test_stream_reads_are_isolated(self, memory_db):
        self._seed(memory_db)
        found = memory_db.journal.read_stream(
            stream_id=project_stream("project-a"),
        )
        assert len(found) == 1

    def test_range_reads_are_ordered(self, memory_db):
        self._seed(memory_db)
        assert [
            r.position.global_sequence
            for r in memory_db.journal.read_range(
                start_sequence=1, end_sequence=3,
            )
        ] == [1, 2, 3]

    def test_reads_preserve_event_types(self, memory_db):
        self._seed(memory_db)
        types = {r.event_type for r in memory_db.journal.read_from(
            global_sequence=1,
        )}
        assert JournalEventType.OUTCOME_RECORDED in types


class TestCorruptionDetection:
    def test_a_manual_update_breaks_verification(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        db = initialize_sqlite_persistence(config)
        for i in range(3):
            db.journal.append(record=record(source_key=f"k{i}"))
        db.close()

        # Exactly what a well-meaning person with sqlite3 would do.
        raw = sqlite3.connect(config.path)
        raw.execute(
            "UPDATE journal_records SET payload = ? WHERE global_sequence = 2",
            ('{"note":"edited by hand"}',),
        )
        raw.commit()
        raw.close()

        reopened = initialize_sqlite_persistence(config)
        report = verify_journal(journal=reopened.journal)
        assert report.valid is False
        assert report.corrupted_at_sequence == 2
        reopened.close()

    def test_truncation_is_detected(self, memory_db):
        for i in range(5):
            memory_db.journal.append(record=record(source_key=f"k{i}"))
        memory_db.journal._truncate_for_testing(keep_through_sequence=3)
        report = verify_journal(
            journal=memory_db.journal, expected_head_sequence=5,
        )
        assert report.valid is False
        assert report.truncated_after_sequence == 3


class TestContextManager:
    def test_the_database_can_be_used_as_a_context_manager(self, tmp_path):
        config = SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        with initialize_sqlite_persistence(config) as db:
            db.journal.append(record=record(source_key="a"))
            assert len(db.journal) == 1
        reopened = initialize_sqlite_persistence(config)
        assert len(reopened.journal) == 1
        reopened.close()

    def test_a_bare_construction_also_works(self, tmp_path):
        db = SQLitePersistence(
            SQLitePersistenceConfig(path=str(tmp_path / "tpllm.db"))
        )
        assert db.journal is not None
        db.close()
