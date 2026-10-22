"""Persistence boundaries and product guardrails.

Two things are being defended.

The dependency arrow: the runtime composes persistence, and no domain
composes either. A domain that imported SQLite would make storage a
concern of the model layer, and a persistence package that imported the
orchestrator would make the dependency circular.

And the product itself: durability must not quietly become surveillance
history. A journal that recorded what each person was doing all day
would be a different product wearing this one's architecture.
"""

from __future__ import annotations

import ast
import importlib
import inspect

import pytest

from tests.test_architecture_boundaries import (
    build_first_party_import_graph,
    find_package_cycles,
)

PERSISTENCE_MODULES = [
    "models", "protocols", "journal", "state_store", "snapshots",
    "replay", "integrity", "versioning", "sqlite",
]

DOMAIN_PACKAGES = [
    "core", "identity", "temporal", "structural", "intelligence",
    "coordination", "scenarios", "portfolio", "evaluation", "memory",
    "integrations", "application",
]


@pytest.fixture(scope="module")
def graph():
    return build_first_party_import_graph(
        DOMAIN_PACKAGES + ["runtime", "persistence"]
    )


def executable_source(module) -> str:
    """Module source with docstrings stripped.

    The guardrails target executable code; prose that explains *why* a
    concept is excluded must not trip the check that excludes it.
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        body = node.body
        if (
            body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
    return ast.unparse(tree).lower()


class TestDependencyDirection:
    def test_persistence_imports_no_first_party_package(self, graph):
        assert graph["persistence"] == set(), (
            f"persistence must stand alone, found "
            f"{sorted(graph['persistence'])} — a storage backend that "
            f"dragged a domain along with it would not be a port."
        )

    def test_the_runtime_composes_persistence(self, graph):
        assert "persistence" in graph["runtime"], (
            "the runtime is the only layer that wires storage in"
        )

    def test_persistence_does_not_import_the_runtime(self, graph):
        assert "runtime" not in graph["persistence"], (
            "persistence knows how to read and verify history; it does not "
            "know how intelligence is derived from it"
        )

    @pytest.mark.parametrize("domain", DOMAIN_PACKAGES)
    def test_no_domain_imports_persistence(self, graph, domain):
        assert "persistence" not in graph[domain], (
            f"{domain} must stay storage-agnostic; persistence is wired in "
            f"at the runtime seam, not inside the models."
        )

    def test_adding_persistence_introduced_no_cycle(self, graph):
        assert find_package_cycles(graph) == []


class TestNoDomainKnowsAboutSqlite:
    @pytest.mark.parametrize(
        "package",
        DOMAIN_PACKAGES + ["runtime"],
    )
    def test_no_domain_imports_sqlite(self, package):
        import pathlib

        root = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src" / "tpllm" / package
        )
        if not root.exists():
            return
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(a.name for a in node.names)
                for module in modules:
                    assert not module.startswith("sqlite3"), (
                        f"{path} imports sqlite3 — only the persistence "
                        f"reference implementation may know the backend."
                    )
                    assert module != "tpllm.persistence.sqlite", (
                        f"{path} imports the SQLite implementation directly; "
                        f"depend on the protocols instead."
                    )

    def test_only_the_sqlite_module_imports_sqlite3(self):
        # An import, not a mention: other modules may discuss the backend
        # in prose without depending on it.
        import pathlib

        root = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src" / "tpllm" / "persistence"
        )
        importers = set()
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(a.name for a in node.names)
                if any(m.startswith("sqlite3") for m in modules):
                    importers.add(path.name)
        assert importers == {"sqlite.py"}


class TestPersistenceInternalLayering:
    def test_models_import_no_other_persistence_module(self):
        import tpllm.persistence.models as models

        source = executable_source(models)
        for module in ("protocols", "journal", "replay", "sqlite"):
            assert f"tpllm.persistence.{module}" not in source

    def test_integrity_depends_only_on_models(self):
        import tpllm.persistence.integrity as integrity

        source = executable_source(integrity)
        for module in ("journal", "replay", "sqlite", "state_store"):
            assert f"tpllm.persistence.{module}" not in source

    def test_the_sqlite_backend_depends_on_models_and_protocols(self):
        import tpllm.persistence.sqlite as sqlite_module

        source = executable_source(sqlite_module)
        assert "tpllm.persistence.models" in source
        assert "tpllm.persistence.protocols" in source

    def test_every_persistence_module_imports(self):
        for name in PERSISTENCE_MODULES:
            assert importlib.import_module(f"tpllm.persistence.{name}")


class TestNoPickle:
    @pytest.mark.parametrize("name", PERSISTENCE_MODULES)
    def test_durable_state_is_never_pickled(self, name):
        module = importlib.import_module(f"tpllm.persistence.{name}")
        source = executable_source(module)
        for forbidden in ("pickle", "marshal", "shelve", "dill"):
            assert forbidden not in source, (
                f"{name} must not use '{forbidden}' — durable history a "
                f"person cannot read with ordinary tools is not durable in "
                f"any useful sense."
            )

    def test_the_runtime_seam_does_not_pickle_either(self):
        import tpllm.runtime.durability as durability

        assert "pickle" not in executable_source(durability)


class TestNoSurveillanceHistory:
    """Durability must not quietly become a record of what people did."""

    FORBIDDEN = (
        "employee_productivity", "productivity_score", "screen_activity",
        "browser_activity", "browsing", "timesheet", "employee_efficiency",
        "efficiency_ranking", "keystrokes", "active_minutes", "idle_time",
        "hours_worked", "message_volume", "attendance", "utilization",
        "performance_score", "employee_score",
    )

    @pytest.mark.parametrize("name", PERSISTENCE_MODULES)
    def test_no_durable_model_records_activity(self, name):
        module = importlib.import_module(f"tpllm.persistence.{name}")
        source = executable_source(module)
        for term in self.FORBIDDEN:
            assert term not in source, (
                f"persistence.{name} must not persist '{term}' — the journal "
                f"records organizational transitions, not what each person "
                f"was doing."
            )

    def test_the_runtime_durability_seam_is_clean(self):
        import tpllm.runtime.durability as durability

        source = executable_source(durability)
        for term in self.FORBIDDEN:
            assert term not in source

    def test_journal_event_types_describe_organizational_transitions(self):
        from tpllm.persistence import JournalEventType

        for event_type in JournalEventType:
            for term in ("employee", "activity", "hours", "timesheet",
                         "attendance", "keystroke"):
                assert term not in event_type.value, (
                    f"journal event '{event_type.value}' must describe what "
                    f"the organization did, not what a person was doing"
                )

    def test_durable_evidence_has_no_activity_fields(self):
        from tpllm.persistence import DurableEvidenceRecord

        for field in DurableEvidenceRecord.model_fields:
            for term in ("employee", "activity", "hours", "duration",
                         "keystroke", "attendance"):
                assert term not in field

    def test_runtime_state_records_have_no_activity_fields(self):
        from tpllm.persistence import (
            PortfolioRuntimeStateRecord,
            ProjectRuntimeStateRecord,
        )

        fields = (
            set(ProjectRuntimeStateRecord.model_fields)
            | set(PortfolioRuntimeStateRecord.model_fields)
        )
        for field in fields:
            for term in ("employee", "activity", "hours", "timesheet"):
                assert term not in field

    def test_the_sqlite_schema_holds_no_activity_tables(self):
        from tpllm.persistence.sqlite import SCHEMA_STATEMENTS

        schema = " ".join(SCHEMA_STATEMENTS).lower()
        for term in (
            "employee", "activity", "timesheet", "attendance", "keystroke",
            "screen_time",
        ):
            assert term not in schema, (
                f"the reference schema must not create a '{term}' table — "
                f"durability is not surveillance"
            )


class TestInspectability:
    def test_persisted_payloads_are_json_compatible(self):
        from tpllm.persistence import canonical_json_bytes

        import json

        payload = {
            "decision_id": "dec-1",
            "options": ["a", "b"],
            "confidence": 0.84,
        }
        assert json.loads(canonical_json_bytes(payload)) == payload

    def test_a_journal_record_round_trips_through_json(self):
        import json

        from tpllm.persistence import JournalRecord, canonical_json_bytes
        from tests._persistence_fixtures import record

        built = record()
        restored = JournalRecord.model_validate(
            json.loads(canonical_json_bytes(built.model_dump(mode="json")))
        )
        assert restored.payload == built.payload
        assert restored.event_type == built.event_type

    def test_a_snapshot_round_trips_through_json(self):
        import json

        from tpllm.persistence import (
            RuntimeSnapshot,
            canonical_json_bytes,
            verify_snapshot_integrity,
        )
        from tests._persistence_fixtures import (
            ORGANIZATION,
            at,
            manifest,
        )
        from tpllm.persistence import build_runtime_snapshot

        snapshot = build_runtime_snapshot(
            snapshot_id="snap-1", organization_id=ORGANIZATION,
            journal_global_sequence=10, active_revision_id="revision-1",
            project_states={}, portfolio_state=None,
            state_fingerprints={"a": "b"}, manifest=manifest(),
            created_at=at(60),
        )
        restored = RuntimeSnapshot.model_validate(
            json.loads(canonical_json_bytes(snapshot.model_dump(mode="json")))
        )
        assert verify_snapshot_integrity(restored) is True


class TestOpenSourceDefault:
    def test_no_cloud_dependency_is_imported(self):
        for name in PERSISTENCE_MODULES:
            module = importlib.import_module(f"tpllm.persistence.{name}")
            source = executable_source(module)
            for cloud in (
                "boto3", "kafka", "redis", "psycopg", "sqlalchemy",
                "google.cloud", "azure",
            ):
                assert cloud not in source, (
                    f"persistence.{name} must not require '{cloud}' — the "
                    f"default path is one local file and no account."
                )

    def test_the_default_config_is_a_local_file(self):
        from tpllm.persistence import SQLitePersistenceConfig

        assert SQLitePersistenceConfig().path == "./tpllm.db"

    def test_no_daemon_or_scheduler_is_required(self):
        for name in PERSISTENCE_MODULES:
            module = importlib.import_module(f"tpllm.persistence.{name}")
            source = executable_source(module)
            for term in ("celery", "apscheduler", "asyncio.create_task"):
                assert term not in source
