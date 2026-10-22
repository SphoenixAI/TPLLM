"""The `tpllm integrations` surface.

Driven through click's runner with a recorded reader, so the whole path
— fetch, translate, append, recompute — is exercised with no network and
no credentials. That is the point of putting the reader behind a port:
the offline suite and the live check run the same code.

The behaviours that matter most here are the honest ones. A contract
that was never recorded must be reported as unverifiable rather than
passing; a repository that could not be read must be distinguishable
from one with no activity; and a recording that still names somebody
must be refused rather than written.
"""

from __future__ import annotations

import copy
import json

import pytest
from click.testing import CliRunner

from tpllm.integrations.contracts import (
    GITHUB_CONTRACT_PATHS,
    build_contract_bundle,
    contract_bundle_path,
    load_contract_bundle,
    serialize_contract_bundle,
)
from tpllm.integrations.github_reader import RecordedGitHubReader
from tpllm.integrations_cli import integrations_cli
from tests import _github_fixtures as fx


@pytest.fixture
def runner():
    return CliRunner()


def _all_output(result) -> str:
    try:
        return result.output + (result.stderr or "")
    except (ValueError, AttributeError):
        return result.output


@pytest.fixture
def responses():
    """The recorded payloads, served at the paths the reader asks for."""
    return {
        f"repos/{fx.REPOSITORY}/pulls": fx.load("pull_requests"),
        f"repos/{fx.REPOSITORY}/issues": fx.load("issues"),
        f"repos/{fx.REPOSITORY}/events": fx.load("events"),
    }


@pytest.fixture
def recorded(monkeypatch, responses):
    """Point the CLI's reader factory at the recordings."""
    reader = RecordedGitHubReader(responses)
    monkeypatch.setattr(
        "tpllm.integrations_cli._reader", lambda token: reader,
    )
    return reader


# ── sources ──────────────────────────────────────────────────────────


class TestSources:
    def test_it_lists_every_payload_kind(self, runner):
        result = runner.invoke(integrations_cli, ["sources"])
        assert result.exit_code == 0, result.output
        for kind in ("pull_request", "issue", "event", "review"):
            assert kind in result.output

    def test_it_marks_what_is_recorded_and_what_is_not(self, runner):
        result = runner.invoke(integrations_cli, ["sources"])
        assert "recorded" in result.output
        assert "hand-written" in result.output

    def test_it_says_what_recorded_means(self, runner):
        result = runner.invoke(integrations_cli, ["sources"])
        assert "evidence that the live API still matches" in result.output
        # Slack and Jira added the third tier, so the listing has to
        # distinguish it rather than calling everything unrecorded.
        assert "official docs" in result.output


# ── normalize ────────────────────────────────────────────────────────


class TestNormalize:
    def _write(self, tmp_path, name):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(fx.load(name)))
        return str(path)

    def test_a_payload_file_becomes_evidence(self, runner, tmp_path):
        result = runner.invoke(integrations_cli, [
            "normalize", self._write(tmp_path, "pull_requests"),
            "--kind", "pull_request", "--repository", fx.REPOSITORY,
            "--project", fx.PROJECT,
        ])
        assert result.exit_code == 0, _all_output(result)
        assert "pr_merged" in result.output

    def test_it_reports_reality_time_not_the_run_time(
        self, runner, tmp_path,
    ):
        result = runner.invoke(integrations_cli, [
            "normalize", self._write(tmp_path, "pull_requests"),
            "--repository", fx.REPOSITORY, "--project", fx.PROJECT,
        ])
        assert "2026-02-25" in result.output, (
            "the column is reality time; showing when we ran would hide "
            "the whole point"
        )

    def test_it_touches_no_network(self, runner, tmp_path, monkeypatch):
        def explode(token):
            raise AssertionError("normalize must not reach the network")

        monkeypatch.setattr("tpllm.integrations_cli._reader", explode)
        result = runner.invoke(integrations_cli, [
            "normalize", self._write(tmp_path, "pull_requests"),
            "--repository", fx.REPOSITORY, "--project", fx.PROJECT,
        ])
        assert result.exit_code == 0, _all_output(result)

    def test_an_unbound_repository_exits_nonzero(self, runner, tmp_path):
        result = runner.invoke(integrations_cli, [
            "normalize", self._write(tmp_path, "pull_requests"),
            "--repository", fx.REPOSITORY,
        ])
        assert result.exit_code == 1
        assert "unbound" in _all_output(result)

    def test_bindings_can_come_from_a_file(self, runner, tmp_path):
        bindings = tmp_path / "bindings.json"
        bindings.write_text(json.dumps({fx.REPOSITORY: [fx.PROJECT]}))
        result = runner.invoke(integrations_cli, [
            "normalize", self._write(tmp_path, "pull_requests"),
            "--repository", fx.REPOSITORY, "--bindings", str(bindings),
        ])
        assert result.exit_code == 0, _all_output(result)

    def test_envelopes_can_be_written_out(self, runner, tmp_path):
        out = tmp_path / "envelopes.json"
        runner.invoke(integrations_cli, [
            "normalize", self._write(tmp_path, "pull_requests"),
            "--repository", fx.REPOSITORY, "--project", fx.PROJECT,
            "--output", str(out),
        ])
        written = json.loads(out.read_text())
        assert written and all("idempotency_key" in e for e in written)

    def test_uninterpreted_payloads_are_reported_not_hidden(
        self, runner, tmp_path,
    ):
        result = runner.invoke(integrations_cli, [
            "normalize", self._write(tmp_path, "events"),
            "--kind", "event", "--repository", fx.REPOSITORY,
            "--project", fx.PROJECT,
        ])
        assert result.exit_code == 0
        assert "uninterpreted" in _all_output(result)
        assert "not an organizational fact" in _all_output(result)


# ── ingest ───────────────────────────────────────────────────────────


class TestIngest:
    def _ingest(self, runner, db, *extra):
        return runner.invoke(integrations_cli, [
            "ingest", "--repository", fx.REPOSITORY,
            "--project", fx.PROJECT, "--db", db,
            "--organization", "acme", *extra,
        ])

    def test_real_payloads_become_durable_evidence(
        self, runner, recorded, tmp_path,
    ):
        db = str(tmp_path / "tpllm.db")
        result = self._ingest(runner, db)
        assert result.exit_code == 0, _all_output(result)
        assert "Journalled 8 evidence record(s)" in result.output

    def test_the_journal_is_a_single_file(self, runner, recorded, tmp_path):
        db = tmp_path / "tpllm.db"
        self._ingest(runner, str(db))
        assert db.exists()

    def test_reading_the_same_repository_twice_adds_nothing(
        self, runner, recorded, tmp_path,
    ):
        db = str(tmp_path / "tpllm.db")
        self._ingest(runner, db)
        second = self._ingest(runner, db)
        assert "Journalled 0 evidence record(s)" in second.output
        assert "already durable" in second.output

    def test_a_dry_run_journals_nothing(self, runner, recorded, tmp_path):
        db = tmp_path / "tpllm.db"
        result = self._ingest(runner, str(db), "--dry-run")
        assert result.exit_code == 0
        assert "nothing was journalled" in result.output
        assert not db.exists(), (
            "a dry run that created the database would still have written"
        )

    def test_the_hash_chain_holds_after_a_live_shaped_ingest(
        self, runner, recorded, tmp_path,
    ):
        from tpllm.persistence import SQLitePersistenceConfig, verify_journal
        from tpllm.persistence.sqlite import initialize_sqlite_persistence

        db = str(tmp_path / "tpllm.db")
        self._ingest(runner, db)
        persistence = initialize_sqlite_persistence(
            SQLitePersistenceConfig(path=db)
        )
        try:
            assert verify_journal(journal=persistence.journal).valid
        finally:
            persistence.close()

    def test_an_unreadable_endpoint_is_reported_but_not_fatal(
        self, runner, monkeypatch, responses, tmp_path,
    ):
        responses.pop(f"repos/{fx.REPOSITORY}/events")
        monkeypatch.setattr(
            "tpllm.integrations_cli._reader",
            lambda token: RecordedGitHubReader(responses),
        )
        result = self._ingest(runner, str(tmp_path / "tpllm.db"))
        assert result.exit_code == 0, _all_output(result)
        assert "could not read events" in _all_output(result)
        assert "Journalled 8 evidence record(s)" in result.output

    def test_total_failure_is_an_outage_not_an_empty_repository(
        self, runner, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(
            "tpllm.integrations_cli._reader",
            lambda token: RecordedGitHubReader({}),
        )
        result = self._ingest(runner, str(tmp_path / "tpllm.db"))
        assert result.exit_code == 1
        assert "outage" in _all_output(result)

    def test_an_unbound_project_is_required_by_the_command(
        self, runner, recorded, tmp_path,
    ):
        result = runner.invoke(integrations_cli, [
            "ingest", "--repository", fx.REPOSITORY,
            "--db", str(tmp_path / "x.db"),
        ])
        assert result.exit_code == 2, (
            "the binding is the one thing the command cannot infer, so it "
            "is required rather than defaulted"
        )

    def test_reality_time_reaches_the_journal(
        self, runner, recorded, tmp_path,
    ):
        from tpllm.persistence import SQLitePersistenceConfig
        from tpllm.persistence.sqlite import initialize_sqlite_persistence

        db = str(tmp_path / "tpllm.db")
        self._ingest(runner, db)
        persistence = initialize_sqlite_persistence(
            SQLitePersistenceConfig(path=db)
        )
        try:
            evidence = [
                r for r in persistence.journal.read_from(global_sequence=1)
                if r.event_type.value == "evidence_accepted"
            ]
            assert evidence
            assert all(r.occurred_at.month == 2 for r in evidence), (
                "the journal must record when the merge happened, not "
                "when we polled for it"
            )
            assert all(
                r.recorded_at.month == 10 for r in evidence
            ), "knowledge time is still today"
        finally:
            persistence.close()


# ── verify ───────────────────────────────────────────────────────────


class TestVerify:
    def _verify(self, runner, *extra):
        return runner.invoke(integrations_cli, [
            "verify", "--repository", fx.REPOSITORY, *extra,
        ])

    def test_the_recordings_satisfy_their_own_contracts(
        self, runner, recorded,
    ):
        result = self._verify(runner)
        assert result.exit_code == 0, _all_output(result)
        assert "every recorded contract still holds" in result.output

    def test_a_removed_field_is_a_breach(
        self, runner, monkeypatch, responses,
    ):
        """The scenario the whole mechanism exists for."""
        broken = copy.deepcopy(responses)
        for payload in broken[f"repos/{fx.REPOSITORY}/pulls"]:
            payload.pop("merged_at", None)
        monkeypatch.setattr(
            "tpllm.integrations_cli._reader",
            lambda token: RecordedGitHubReader(broken),
        )
        result = self._verify(runner)
        assert result.exit_code == 1
        assert "merged_at" in _all_output(result)
        assert "no longer provides" in _all_output(result)

    def test_a_retyped_field_is_a_breach(
        self, runner, monkeypatch, responses,
    ):
        broken = copy.deepcopy(responses)
        broken[f"repos/{fx.REPOSITORY}/pulls"][0]["number"] = "8"
        monkeypatch.setattr(
            "tpllm.integrations_cli._reader",
            lambda token: RecordedGitHubReader(broken),
        )
        result = self._verify(runner)
        assert result.exit_code == 1
        assert "is now str" in _all_output(result)

    def test_a_new_provider_field_is_not_a_breach(
        self, runner, monkeypatch, responses,
    ):
        extended = copy.deepcopy(responses)
        for payload in extended[f"repos/{fx.REPOSITORY}/pulls"]:
            payload["some_new_github_field"] = {"nested": True}
        monkeypatch.setattr(
            "tpllm.integrations_cli._reader",
            lambda token: RecordedGitHubReader(extended),
        )
        assert self._verify(runner).exit_code == 0

    def test_an_unrecorded_contract_is_not_reported_as_passing(
        self, runner, recorded,
    ):
        """A hand-written payload agreeing with itself says nothing about
        GitHub, and a report that implied otherwise would be worse than
        no report."""
        result = self._verify(runner)
        output = _all_output(result)
        assert "not verifiable" in output
        assert "the 'review' contract is hand_written" in output

    def test_nothing_verifiable_exits_nonzero(
        self, runner, monkeypatch, responses,
    ):
        """An unverified run must not look like a verified one."""
        only_issues = {
            f"repos/{fx.REPOSITORY}/issues": responses[
                f"repos/{fx.REPOSITORY}/issues"
            ]
        }
        monkeypatch.setattr(
            "tpllm.integrations_cli._reader",
            lambda token: RecordedGitHubReader(only_issues),
        )
        result = self._verify(runner)
        assert result.exit_code == 1
        assert "nothing was verified" in _all_output(result)

    def test_an_unknown_source_is_refused(self, runner, recorded):
        result = self._verify(runner, "--source", "bitbucket")
        assert result.exit_code == 2
        assert "no recorded contracts" in _all_output(result)

    def test_verifying_changes_nothing(self, runner, recorded, tmp_path):
        before = contract_bundle_path("github").read_text()
        self._verify(runner)
        assert contract_bundle_path("github").read_text() == before


# ── record ───────────────────────────────────────────────────────────


class TestRecord:
    def test_it_writes_scrubbed_fixtures(
        self, runner, recorded, tmp_path, monkeypatch,
    ):
        # The bundle is package data; keep the real one untouched.
        bundle = contract_bundle_path("github")
        original = bundle.read_text()
        out = tmp_path / "fixtures"
        try:
            result = runner.invoke(integrations_cli, [
                "record", "--repository", fx.REPOSITORY,
                "--out", str(out), "--alias", "other/service",
            ])
            assert result.exit_code == 0, _all_output(result)
            written = json.loads((out / "pull_requests.json").read_text())
            assert written
            assert "other/service" in json.dumps(written)
            assert fx.REPOSITORY not in json.dumps(written)
        finally:
            bundle.write_text(original)

    def test_it_regenerates_the_contract_bundle(
        self, runner, recorded, tmp_path,
    ):
        bundle = contract_bundle_path("github")
        original = bundle.read_text()
        try:
            runner.invoke(integrations_cli, [
                "record", "--repository", fx.REPOSITORY,
                "--out", str(tmp_path / "f"), "--alias", "other/service",
            ])
            regenerated = load_contract_bundle("github")
            assert regenerated is not None
            assert "pull_request" in regenerated.verifiable_kinds
        finally:
            bundle.write_text(original)

    def test_it_refuses_to_write_a_fixture_that_still_names_somebody(
        self, runner, recorded, tmp_path, monkeypatch,
    ):
        """A fixture that leaks is worse than no fixture, because it gets
        committed."""
        monkeypatch.setattr(
            "tpllm.integrations.contracts.scrub_github_payload",
            lambda payload, **kwargs: payload,
        )
        out = tmp_path / "fixtures"
        result = runner.invoke(integrations_cli, [
            "record", "--repository", fx.REPOSITORY, "--out", str(out),
        ])
        assert result.exit_code == 1
        assert "refusing to write" in _all_output(result)
        assert not list(out.glob("*.json")), (
            "nothing should have been written before the refusal"
        )

    def test_it_says_derived_fixtures_were_not_touched(
        self, runner, recorded, tmp_path,
    ):
        bundle = contract_bundle_path("github")
        original = bundle.read_text()
        try:
            result = runner.invoke(integrations_cli, [
                "record", "--repository", fx.REPOSITORY,
                "--out", str(tmp_path / "f"), "--alias", "other/service",
            ])
            assert "were not touched" in _all_output(result)
        finally:
            bundle.write_text(original)

    def test_nothing_readable_is_not_recorded_as_empty(
        self, runner, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(
            "tpllm.integrations_cli._reader",
            lambda token: RecordedGitHubReader({}),
        )
        result = runner.invoke(integrations_cli, [
            "record", "--repository", fx.REPOSITORY,
            "--out", str(tmp_path / "f"),
        ])
        assert result.exit_code == 1
        assert "nothing was recorded" in _all_output(result)


# ── The shipped bundle cannot drift from the recordings ──────────────


class TestTheShippedBundleMatchesTheFixtures:
    """The bundle ships in the package because `verify` has to work from
    an installed wheel. That makes drift possible, so it is checked."""

    def test_the_bundle_is_exactly_what_the_fixtures_produce(self):
        rebuilt = build_contract_bundle(
            source_system="github", sources=fx.contract_sources(),
            paths_by_kind=GITHUB_CONTRACT_PATHS, note=fx.BUNDLE_NOTE,
        )
        assert serialize_contract_bundle(rebuilt) == (
            contract_bundle_path("github").read_text()
        ), (
            "the shipped contract no longer matches the recordings it "
            "claims to come from; regenerate it alongside the fixtures"
        )

    def test_the_bundle_knows_which_kinds_are_evidence(self):
        bundle = load_contract_bundle("github")
        assert bundle is not None
        assert bundle.verifiable_kinds == ["event", "pull_request"]
        assert bundle.unverifiable_kinds == ["issue", "review"]

    def test_the_bundle_is_shipped_inside_the_package(self):
        path = contract_bundle_path("github")
        assert path.exists()
        assert "src/tpllm/integrations" in str(path).replace("\\", "/"), (
            "a contract that only exists in a git checkout cannot be "
            "checked by the person running the installed tool"
        )
