"""Tests for Commit 31 — the rebuildable projection store.

Everything here is a cache. The one thing that must survive a crash is
atomic revision promotion: a crash before the pointer moves leaves the
previous revision authoritative, a crash after leaves the new one, and
nothing ever observes half a revision as current.
"""

from __future__ import annotations

import pytest

from lltpm.persistence import (
    InMemoryIntelligenceArtifactStore,
    InMemoryRuntimeStateStore,
    PortfolioRuntimeStateRecord,
    ProjectRuntimeStateRecord,
    RevisionPromotionError,
    RuntimeRevisionRecord,
    SQLitePersistenceConfig,
)
from lltpm.persistence.sqlite import initialize_sqlite_persistence
from tests._persistence_fixtures import (
    ORGANIZATION,
    PROJECT_A,
    PROJECT_C,
    at,
)


def _revision(revision_id: str = "revision-1", **overrides):
    defaults = dict(
        revision_id=revision_id,
        organization_id=ORGANIZATION,
        complete=True,
        state_fingerprints={"project:project-a": "fp-a"},
    )
    defaults.update(overrides)
    return RuntimeRevisionRecord(**defaults)


def _project_state(project_id: str = PROJECT_A, **overrides):
    defaults = dict(
        project_id=project_id,
        organization_id=ORGANIZATION,
        revision_id="revision-1",
        state_fingerprint="fp-a",
        state={"project_id": project_id, "kpi_state_ids": ["kpi-1"]},
        updated_at=at(5),
    )
    defaults.update(overrides)
    return ProjectRuntimeStateRecord(**defaults)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    """Both backends must satisfy the same contract."""
    if request.param == "memory":
        yield InMemoryRuntimeStateStore()
    else:
        db = initialize_sqlite_persistence(
            SQLitePersistenceConfig(path=str(tmp_path / "lltpm.db"))
        )
        yield db.state
        db.close()


class TestProjectState:
    def test_a_project_state_persists(self, store):
        store.put_project_state(_project_state())
        found = store.get_project_state(PROJECT_A)
        assert found.project_id == PROJECT_A
        assert found.state_fingerprint == "fp-a"
        assert found.state["kpi_state_ids"] == ["kpi-1"]

    def test_an_unknown_project_returns_none(self, store):
        assert store.get_project_state("no-such-project") is None

    def test_writing_twice_updates_rather_than_duplicates(self, store):
        store.put_project_state(_project_state())
        store.put_project_state(
            _project_state(state_fingerprint="fp-a2", revision_id="revision-2")
        )
        found = store.get_project_state(PROJECT_A)
        assert found.state_fingerprint == "fp-a2"
        assert found.revision_id == "revision-2"

    def test_projects_are_stored_independently(self, store):
        store.put_project_state(_project_state(PROJECT_A))
        store.put_project_state(
            _project_state(PROJECT_C, state_fingerprint="fp-c")
        )
        assert store.get_project_state(PROJECT_C).state_fingerprint == "fp-c"
        assert store.get_project_state(PROJECT_A).state_fingerprint == "fp-a"


class TestPortfolioState:
    def test_portfolio_state_persists(self, store):
        store.put_portfolio_state(
            PortfolioRuntimeStateRecord(
                organization_id=ORGANIZATION,
                revision_id="revision-1",
                state_fingerprint="fp-portfolio",
                state={"bottleneck_ids": ["b1"]},
            )
        )
        found = store.get_portfolio_state(ORGANIZATION)
        assert found.state_fingerprint == "fp-portfolio"
        assert found.state["bottleneck_ids"] == ["b1"]

    def test_an_unknown_organization_returns_none(self, store):
        assert store.get_portfolio_state("no-such-org") is None


class TestRevisionPromotion:
    def test_a_complete_revision_becomes_current(self, store):
        store.put_revision(_revision())
        store.promote_revision(
            organization_id=ORGANIZATION,
            revision_id="revision-1",
            expected_current_revision_id=None,
        )
        assert store.get_current_revision(ORGANIZATION) == "revision-1"

    def test_an_incomplete_candidate_is_refused(self, store):
        store.put_revision(_revision(complete=False))
        with pytest.raises(RevisionPromotionError, match="not complete"):
            store.promote_revision(
                organization_id=ORGANIZATION,
                revision_id="revision-1",
                expected_current_revision_id=None,
            )
        assert store.get_current_revision(ORGANIZATION) is None

    def test_an_unpersisted_revision_is_refused(self, store):
        with pytest.raises(RevisionPromotionError, match="nothing to promote"):
            store.promote_revision(
                organization_id=ORGANIZATION,
                revision_id="revision-missing",
                expected_current_revision_id=None,
            )

    def test_a_stale_expectation_is_refused(self, store):
        store.put_revision(_revision("revision-1"))
        store.put_revision(_revision("revision-2"))
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-1",
            expected_current_revision_id=None,
        )
        with pytest.raises(RevisionPromotionError, match="promoted first"):
            store.promote_revision(
                organization_id=ORGANIZATION, revision_id="revision-2",
                expected_current_revision_id=None,
            )
        assert store.get_current_revision(ORGANIZATION) == "revision-1"

    def test_a_matching_expectation_moves_the_pointer(self, store):
        store.put_revision(_revision("revision-1"))
        store.put_revision(_revision("revision-2"))
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-1",
            expected_current_revision_id=None,
        )
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-2",
            expected_current_revision_id="revision-1",
        )
        assert store.get_current_revision(ORGANIZATION) == "revision-2"

    def test_only_one_revision_is_current_at_a_time(self, store):
        store.put_revision(_revision("revision-1"))
        store.put_revision(_revision("revision-2"))
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-1",
            expected_current_revision_id=None,
        )
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-2",
            expected_current_revision_id="revision-1",
        )
        current = [
            r for r in store.list_revisions(ORGANIZATION) if r.is_current
        ]
        assert len(current) == 1
        assert current[0].revision_id == "revision-2"

    def test_a_failed_candidate_does_not_replace_the_current(self, store):
        store.put_revision(_revision("revision-1"))
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-1",
            expected_current_revision_id=None,
        )
        store.put_revision(_revision("revision-2", complete=False))
        with pytest.raises(RevisionPromotionError):
            store.promote_revision(
                organization_id=ORGANIZATION, revision_id="revision-2",
                expected_current_revision_id="revision-1",
            )
        assert store.get_current_revision(ORGANIZATION) == "revision-1", (
            "a revision that did not finish must never answer for the "
            "organization"
        )

    def test_the_previous_revision_stays_inspectable(self, store):
        store.put_revision(_revision("revision-1"))
        store.put_revision(_revision("revision-2"))
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-1",
            expected_current_revision_id=None,
        )
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-2",
            expected_current_revision_id="revision-1",
        )
        old = store.get_revision("revision-1")
        assert old is not None
        assert old.is_current is False
        assert old.state_fingerprints == {"project:project-a": "fp-a"}

    def test_a_failed_candidate_stays_inspectable(self, store):
        store.put_revision(_revision("revision-2", complete=False))
        found = store.get_revision("revision-2")
        assert found is not None
        assert found.complete is False

    def test_an_unknown_revision_returns_none(self, store):
        assert store.get_revision("nope") is None

    def test_no_current_revision_on_a_fresh_store(self, store):
        assert store.get_current_revision(ORGANIZATION) is None


class TestProjectionClearing:
    def test_clearing_removes_projections(self, store):
        store.put_project_state(_project_state())
        store.put_revision(_revision())
        store.promote_revision(
            organization_id=ORGANIZATION, revision_id="revision-1",
            expected_current_revision_id=None,
        )
        store.clear_projections(ORGANIZATION)
        assert store.get_current_revision(ORGANIZATION) is None
        assert store.get_project_state(PROJECT_A) is None
        assert store.get_revision("revision-1") is None


class TestArtifactStore:
    @pytest.fixture(params=["memory", "sqlite"])
    def artifacts(self, request, tmp_path):
        if request.param == "memory":
            yield InMemoryIntelligenceArtifactStore()
        else:
            db = initialize_sqlite_persistence(
                SQLitePersistenceConfig(path=str(tmp_path / "artifacts.db"))
            )
            yield db.artifacts
            db.close()

    def test_an_artifact_persists(self, artifacts):
        artifacts.put(
            artifact_type="scenario_lattice", artifact_id="s1",
            revision_id="revision-1", payload={"p": 0.49}, fingerprint="fp1",
        )
        found = artifacts.get("scenario_lattice", "s1")
        assert found.fingerprint == "fp1"
        assert found.payload == {"p": 0.49}

    def test_an_unknown_artifact_returns_none(self, artifacts):
        assert artifacts.get("scenario_lattice", "nope") is None

    def test_history_is_kept_per_revision(self, artifacts):
        artifacts.put(
            artifact_type="recommendation", artifact_id="rec-1",
            revision_id="revision-1", payload={"option": "opt-b"},
            fingerprint="fp-august",
        )
        artifacts.put(
            artifact_type="recommendation", artifact_id="rec-1",
            revision_id="revision-2", payload={"option": "opt-c"},
            fingerprint="fp-september",
        )
        august = artifacts.get_at_revision(
            "recommendation", "rec-1", "revision-1",
        )
        assert august.payload == {"option": "opt-b"}, (
            "what TPLLM recommended in August has one true answer, and a "
            "better engine later does not change it"
        )

    def test_get_returns_the_most_recent_revision(self, artifacts):
        artifacts.put(
            artifact_type="recommendation", artifact_id="rec-1",
            revision_id="revision-1", payload={"option": "opt-b"},
            fingerprint="fp1",
        )
        artifacts.put(
            artifact_type="recommendation", artifact_id="rec-1",
            revision_id="revision-2", payload={"option": "opt-c"},
            fingerprint="fp2",
        )
        assert artifacts.get(
            "recommendation", "rec-1",
        ).payload == {"option": "opt-c"}

    def test_artifacts_can_be_listed_by_revision(self, artifacts):
        artifacts.put(
            artifact_type="a", artifact_id="1", revision_id="revision-1",
            payload={}, fingerprint="f",
        )
        artifacts.put(
            artifact_type="b", artifact_id="2", revision_id="revision-1",
            payload={}, fingerprint="f",
        )
        artifacts.put(
            artifact_type="c", artifact_id="3", revision_id="revision-2",
            payload={}, fingerprint="f",
        )
        assert len(artifacts.get_revision_artifacts("revision-1")) == 2

    def test_the_manifest_fields_round_trip(self, artifacts):
        artifacts.put(
            artifact_type="a", artifact_id="1", revision_id="revision-1",
            payload={}, fingerprint="f",
            derivation_version="runtime-v30",
            policy_fingerprint="policy-fp-v1",
        )
        found = artifacts.get("a", "1")
        assert found.derivation_version == "runtime-v30"
        assert found.policy_fingerprint == "policy-fp-v1"

    def test_clearing_empties_the_cache(self, artifacts):
        artifacts.put(
            artifact_type="a", artifact_id="1", revision_id="revision-1",
            payload={}, fingerprint="f",
        )
        artifacts.clear()
        assert artifacts.get("a", "1") is None


class TestCopyOnRead:
    def test_stored_state_cannot_be_mutated_through_a_reference(self):
        store = InMemoryRuntimeStateStore()
        store.put_project_state(_project_state())
        found = store.get_project_state(PROJECT_A)
        found.state["kpi_state_ids"].append("sneaky")
        assert store.get_project_state(
            PROJECT_A,
        ).state["kpi_state_ids"] == ["kpi-1"]
