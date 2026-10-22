"""Tests for Commit 32 — the CLI surface and offline baselines.

Before this there was no command a user could type that reached the
orchestrator or the journal. These tests drive the real `lltpm runtime`
commands through click's runner against a real SQLite file.

They also pin the two things that make the surface honest: baseline
providers are labelled as baselines in the manifest so their output is
never mistaken for model-derived output, and the read-only commands
cannot change anything.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from lltpm.runtime.baselines import (
    BASELINE_RECOMMENDATION_METHOD,
    BASELINE_SCENARIO_METHOD,
    KeywordClaimExtractor,
    StructuralScenarioGenerator,
    baseline_providers,
)
from lltpm.runtime_cli import runtime_cli

EVIDENCE = [
    {
        "source_system": "slack", "source_type": "chat",
        "source_id": "C1-100", "event_type": "architecture_evidence",
        "observed_at": "2026-10-19T09:00:00", "project_ids": ["billing"],
        "content": (
            "Billing activation depends on Platform approval of the "
            "contract standard before the integration can launch."
        ),
    },
    {
        "source_system": "jira", "source_type": "ticket",
        "source_id": "PLAT-42", "event_type": "blocker_opened",
        "observed_at": "2026-10-19T09:30:00", "project_ids": ["billing"],
        "content": (
            "Platform Architecture is in incident response; review "
            "capacity is reduced."
        ),
    },
    {
        "source_system": "meeting", "source_type": "meeting",
        "source_id": "M-7", "event_type": "meeting_transcript",
        "observed_at": "2026-10-19T08:58:00", "project_ids": ["identity"],
        "content": (
            "Identity owns the migration. The integration target is 60 "
            "percent activation."
        ),
    },
]


@pytest.fixture
def workspace(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(EVIDENCE))
    return tmp_path, str(tmp_path / "lltpm.db"), str(evidence)


@pytest.fixture
def runner():
    return CliRunner()


def _ingest(runner, db, evidence, *extra):
    return runner.invoke(
        runtime_cli,
        ["ingest", evidence, "--db", db, "--organization", "acme", *extra],
    )


class TestIngest:
    def test_evidence_is_journalled_and_a_revision_publishes(
        self, runner, workspace,
    ):
        _, db, evidence = workspace
        result = _ingest(runner, db, evidence)
        assert result.exit_code == 0, result.output
        assert "Journalled 3 evidence record(s)" in result.output
        assert "published revision" in result.output

    def test_it_works_offline_with_no_api_key(self, runner, workspace):
        """The quickstart case: no model, no account, one file."""
        _, db, evidence = workspace
        result = _ingest(runner, db, evidence)
        assert result.exit_code == 0
        assert Path(db).exists()

    def test_the_database_is_a_single_file(self, runner, workspace):
        tmp_path, db, evidence = workspace
        _ingest(runner, db, evidence)
        assert Path(db).exists()

    def test_an_empty_evidence_file_does_nothing(self, runner, tmp_path):
        evidence = tmp_path / "empty.json"
        evidence.write_text("[]")
        result = runner.invoke(
            runtime_cli,
            ["ingest", str(evidence), "--db", str(tmp_path / "x.db")],
        )
        assert result.exit_code == 0
        assert "nothing to ingest" in result.output

    def test_redelivering_the_same_evidence_adds_no_evidence(
        self, runner, workspace,
    ):
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        before = _count_evidence(db)
        _ingest(runner, db, evidence)
        assert _count_evidence(db) == before, (
            "the same webhook is the same fact, however many times it "
            "arrives"
        )

    def test_a_second_ingest_allocates_a_new_revision(
        self, runner, workspace,
    ):
        """Each CLI invocation builds a fresh projection, so the revision
        counter has to continue from durable state rather than restart."""
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        second = _ingest(runner, db, evidence)
        assert "revision-2" in second.output, (
            "two genuinely different recomputations must not share one "
            "revision id"
        )

    def test_the_horizon_is_an_explicit_parameter(self, runner, workspace):
        _, db, evidence = workspace
        result = _ingest(runner, db, evidence, "--horizon-days", "30")
        assert result.exit_code == 0
        assert "published revision" in result.output


def _count_evidence(db: str) -> int:
    connection = sqlite3.connect(db)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM journal_records WHERE event_type = ?",
            ("evidence_accepted",),
        ).fetchone()[0]
    finally:
        connection.close()


def _head_sequence(db: str) -> int:
    connection = sqlite3.connect(db)
    try:
        return connection.execute(
            "SELECT MAX(global_sequence) FROM journal_records"
        ).fetchone()[0]
    finally:
        connection.close()


class TestReadOnlyCommands:
    @pytest.fixture
    def seeded(self, runner, workspace):
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        return db

    def test_status_reports_the_current_revision(self, runner, seeded):
        result = runner.invoke(
            runtime_cli, ["status", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 0
        assert "revision-1" in result.output
        assert "valid" in result.output

    def test_status_lists_project_fingerprints(self, runner, seeded):
        result = runner.invoke(
            runtime_cli, ["status", "--db", seeded, "--organization", "acme"],
        )
        assert "billing" in result.output
        assert "identity" in result.output

    def test_verify_passes_on_a_clean_journal(self, runner, seeded):
        result = runner.invoke(runtime_cli, ["verify", "--db", seeded])
        assert result.exit_code == 0
        assert "ok" in result.output

    def test_verify_fails_on_a_hand_edited_journal(self, runner, seeded):
        connection = sqlite3.connect(seeded)
        connection.execute(
            "UPDATE journal_records SET payload = ? "
            "WHERE global_sequence = 2",
            ('{"note":"edited by hand"}',),
        )
        connection.commit()
        connection.close()

        result = runner.invoke(runtime_cli, ["verify", "--db", seeded])
        assert result.exit_code == 1
        assert "corrupt" in result.output
        assert "never silently repaired" in result.output

    def test_verify_names_the_bad_sequence(self, runner, seeded):
        connection = sqlite3.connect(seeded)
        connection.execute(
            "UPDATE journal_records SET payload = ? "
            "WHERE global_sequence = 2",
            ('{"note":"tampered"}',),
        )
        connection.commit()
        connection.close()
        result = runner.invoke(runtime_cli, ["verify", "--db", seeded])
        assert "first bad sequence: 2" in result.output

    def test_journal_lists_records(self, runner, seeded):
        result = runner.invoke(runtime_cli, ["journal", "--db", seeded])
        assert result.exit_code == 0
        assert "evidence_accepted" in result.output
        assert "intelligence_revision_published" in result.output

    def test_journal_can_filter_by_event_type(self, runner, seeded):
        result = runner.invoke(
            runtime_cli,
            ["journal", "--db", seeded, "--event-type", "evidence_accepted"],
        )
        assert result.exit_code == 0
        assert "intelligence_revision_published" not in result.output

    def test_journal_rejects_an_unknown_event_type(self, runner, seeded):
        result = runner.invoke(
            runtime_cli,
            ["journal", "--db", seeded, "--event-type", "not_a_real_type"],
        )
        assert result.exit_code == 2
        assert "unknown event type" in result.output

    def test_read_only_commands_do_not_change_the_journal(
        self, runner, seeded,
    ):
        before = _head_sequence(seeded)
        runner.invoke(runtime_cli, ["status", "--db", seeded])
        runner.invoke(runtime_cli, ["verify", "--db", seeded])
        runner.invoke(runtime_cli, ["journal", "--db", seeded])
        assert _head_sequence(seeded) == before

    def test_status_on_an_empty_database_is_honest(self, runner, tmp_path):
        result = runner.invoke(
            runtime_cli, ["status", "--db", str(tmp_path / "fresh.db")],
        )
        assert result.exit_code == 0
        assert "Journal head: 0" in result.output

    def test_journal_on_an_empty_database_says_so(self, runner, tmp_path):
        result = runner.invoke(
            runtime_cli, ["journal", "--db", str(tmp_path / "fresh.db")],
        )
        assert "Journal is empty" in result.output

    def test_coverage_distinguishes_engines_from_projections(self, runner):
        result = runner.invoke(runtime_cli, ["coverage"])
        assert result.exit_code == 0
        assert "engine" in result.output
        assert "derived projection" in result.output
        assert "thinner than a dedicated" in result.output


class TestReplayCommand:
    @pytest.fixture
    def seeded(self, runner, workspace):
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        return db

    def test_replay_is_a_dry_run_by_default(self, runner, seeded):
        result = runner.invoke(
            runtime_cli, ["replay", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 0
        assert "Published:  False" in result.output

    def test_replay_reports_zero_suppressed_effects_with_no_alerts(
        self, runner, seeded,
    ):
        result = runner.invoke(
            runtime_cli, ["replay", "--db", seeded, "--organization", "acme"],
        )
        assert "external effect(s)" in result.output

    def test_replay_can_publish_when_asked(self, runner, seeded):
        result = runner.invoke(
            runtime_cli,
            ["replay", "--db", seeded, "--organization", "acme", "--publish"],
        )
        assert result.exit_code == 0
        assert "Published:  True" in result.output

    def test_replay_rebuilds_after_projections_are_deleted(
        self, runner, seeded,
    ):
        connection = sqlite3.connect(seeded)
        connection.execute("DELETE FROM runtime_revisions")
        connection.execute("DELETE FROM project_runtime_state")
        connection.execute("DELETE FROM portfolio_runtime_state")
        connection.commit()
        connection.close()

        result = runner.invoke(
            runtime_cli,
            ["replay", "--db", seeded, "--organization", "acme", "--publish"],
        )
        assert result.exit_code == 0
        assert "Published:  True" in result.output

        status = runner.invoke(
            runtime_cli, ["status", "--db", seeded, "--organization", "acme"],
        )
        assert "billing" in status.output, (
            "the journal alone rebuilt the project state"
        )

    def test_replay_stops_on_a_corrupt_journal(self, runner, seeded):
        connection = sqlite3.connect(seeded)
        connection.execute(
            "UPDATE journal_records SET payload = ? "
            "WHERE global_sequence = 1",
            ('{"note":"tampered"}',),
        )
        connection.commit()
        connection.close()

        result = runner.invoke(
            runtime_cli, ["replay", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 1
        assert "mismatch" in result.output or "Complete:   False" in (
            result.output
        )


class TestSnapshotCommand:
    @pytest.fixture
    def seeded(self, runner, workspace):
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        return db

    def test_a_snapshot_is_created_at_the_journal_head(self, runner, seeded):
        result = runner.invoke(
            runtime_cli,
            ["snapshot", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 0
        assert "snapshot snap-" in result.output

    def test_a_snapshot_accelerates_the_next_replay(self, runner, seeded):
        runner.invoke(
            runtime_cli,
            ["snapshot", "--db", seeded, "--organization", "acme"],
        )
        result = runner.invoke(
            runtime_cli, ["replay", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 0
        assert "Snapshot:   snap-" in result.output

    def test_a_snapshot_at_head_reconstructs_without_mismatches(
        self, runner, seeded,
    ):
        """The Commit 31 baseline bug: a snapshot covering everything
        left the reconstruction empty and reported false mismatches."""
        runner.invoke(
            runtime_cli,
            ["snapshot", "--db", seeded, "--organization", "acme"],
        )
        result = runner.invoke(
            runtime_cli, ["replay", "--db", seeded, "--organization", "acme"],
        )
        assert "mismatch" not in result.output, (
            "a snapshot is an accelerator; starting from it must give the "
            "same answer as replaying from record one"
        )
        assert "Complete:   True" in result.output

    def test_an_empty_journal_cannot_be_snapshotted(self, runner, workspace):
        _, db, _ = workspace
        result = runner.invoke(
            runtime_cli,
            ["snapshot", "--db", db, "--organization", "acme"],
        )
        assert result.exit_code == 0
        assert "journal is empty" in result.output

    def test_a_snapshot_needs_a_current_revision(self, runner, seeded):
        connection = sqlite3.connect(seeded)
        connection.execute("UPDATE runtime_revisions SET is_current = 0")
        connection.commit()
        connection.close()
        result = runner.invoke(
            runtime_cli,
            ["snapshot", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 1
        assert "no current revision" in result.output


class TestBaselineProvidersAreLabelled:
    def test_the_providers_declare_their_method_versions(self):
        assert KeywordClaimExtractor.method_version == (
            BASELINE_RECOMMENDATION_METHOD
        )
        assert StructuralScenarioGenerator.method_version == (
            BASELINE_SCENARIO_METHOD
        )

    def test_the_method_names_say_baseline(self):
        assert "baseline" in BASELINE_SCENARIO_METHOD
        assert "baseline" in BASELINE_RECOMMENDATION_METHOD

    def test_a_baseline_manifest_is_not_equivalent_to_a_model_one(self):
        """Commit 31's compatibility check must refuse to call baseline
        output and model output the same thing."""
        from lltpm.persistence import build_manifest, manifests_compatible

        baseline = build_manifest(
            policy_fingerprint="p",
            scenario_method_version=BASELINE_SCENARIO_METHOD,
        )
        model = build_manifest(
            policy_fingerprint="p", scenario_method_version="llm-v1",
        )
        assert manifests_compatible(expected=model, actual=baseline) is False

    def test_the_provider_set_supplies_both_protocols(self):
        providers = baseline_providers()
        assert set(providers) == {"claim_extractor", "scenario_generator"}


class TestKeywordClaimExtractor:
    def _evidence(self, content: str, evidence_id: str = "pev-1"):
        from datetime import datetime

        from lltpm.intelligence import (
            EvidenceReference,
            EvidenceSourceType,
            ProjectEvidence,
        )

        observed = datetime(2026, 10, 19, 9, 0)
        return ProjectEvidence(
            id=evidence_id,
            project_id="billing",
            content=content,
            source=EvidenceReference(
                id=f"ref-{evidence_id}",
                project_id="billing",
                source_type=EvidenceSourceType.CHAT,
                source_id="1",
                excerpt=content[:300],
                observed_at=observed,
                ingested_at=observed,
                reliability=0.6,
                relevance=0.6,
            ),
            normalized_at=observed,
        )

    def test_a_dependency_phrase_yields_a_dependency_claim(self):
        claims = KeywordClaimExtractor().extract_claims(
            project_id="billing",
            evidence=[self._evidence("Billing depends on Platform approval.")],
            as_of=None,
        )
        assert any(c.claim_type.value == "dependency" for c in claims)

    def test_claims_carry_low_confidence(self):
        claims = KeywordClaimExtractor().extract_claims(
            project_id="billing",
            evidence=[self._evidence("Billing depends on Platform.")],
            as_of=None,
        )
        assert all(c.confidence <= 0.5 for c in claims), (
            "a keyword match is weak evidence that a claim was made, and "
            "downstream scoring should treat it that way"
        )

    def test_claims_say_they_were_inferred_by_keyword(self):
        claims = KeywordClaimExtractor().extract_claims(
            project_id="billing",
            evidence=[self._evidence("An incident reduced capacity.")],
            as_of=None,
        )
        assert claims
        assert all(c.inferred for c in claims)
        assert all(
            "not a reading of the text" in c.inference_explanation
            for c in claims
        )

    def test_unmatched_evidence_yields_nothing(self):
        assert KeywordClaimExtractor().extract_claims(
            project_id="billing",
            evidence=[self._evidence("Hello, good morning everyone.")],
            as_of=None,
        ) == []

    def test_extraction_is_deterministic(self):
        evidence = [self._evidence("Billing depends on an integration.")]
        extractor = KeywordClaimExtractor()
        first = extractor.extract_claims(
            project_id="billing", evidence=evidence, as_of=None,
        )
        second = extractor.extract_claims(
            project_id="billing", evidence=evidence, as_of=None,
        )
        assert [c.id for c in first] == [c.id for c in second]

    def test_one_claim_per_type_per_evidence(self):
        claims = KeywordClaimExtractor().extract_claims(
            project_id="billing",
            evidence=[
                self._evidence("Blocked by approval; depends on approval.")
            ],
            as_of=None,
        )
        dependency = [
            c for c in claims if c.claim_type.value == "dependency"
        ]
        assert len(dependency) == 1, (
            "three dependency keywords in one sentence describe one claim"
        )


class TestStructuralScenarioGenerator:
    def _state(self, **overrides):
        from datetime import datetime

        from lltpm.temporal import ProjectTemporalState

        defaults = dict(
            project_id="billing",
            as_of=datetime(2026, 10, 19, 9, 0),
            active_blocker_ids=["blk-contract"],
            active_decision_ids=["dec-standard"],
            evidence_cutoff_at=datetime(2026, 10, 19, 9, 0),
            state_fingerprint="fp",
        )
        defaults.update(overrides)
        return ProjectTemporalState(**defaults)

    def test_each_blocker_produces_a_resolve_or_slip_pair(self):
        candidates = StructuralScenarioGenerator().generate_events(
            temporal_state=self._state(active_decision_ids=[]),
            horizon=None, context=None,
        )
        assert len(candidates) == 2
        assert {c.mutually_exclusive_group for c in candidates} == {
            "blocker-blk-contract",
        }

    def test_each_decision_produces_a_pair_too(self):
        candidates = StructuralScenarioGenerator().generate_events(
            temporal_state=self._state(active_blocker_ids=[]),
            horizon=None, context=None,
        )
        assert len(candidates) == 2
        assert all(
            c.mutually_exclusive_group == "decision-dec-standard"
            for c in candidates
        )

    def test_the_pair_probabilities_sum_to_one(self):
        candidates = StructuralScenarioGenerator().generate_events(
            temporal_state=self._state(active_decision_ids=[]),
            horizon=None, context=None,
        )
        assert sum(c.base_probability for c in candidates) == pytest.approx(
            1.0
        )

    def test_the_slip_carries_more_impact_than_the_resolution(self):
        candidates = StructuralScenarioGenerator().generate_events(
            temporal_state=self._state(active_decision_ids=[]),
            horizon=None, context=None,
        )
        resolved = next(c for c in candidates if "resolved" in c.id)
        persisted = next(c for c in candidates if "persists" in c.id)
        assert persisted.impact > resolved.impact, (
            "probability and impact are separate quantities"
        )

    def test_an_empty_present_produces_no_futures(self):
        assert StructuralScenarioGenerator().generate_events(
            temporal_state=self._state(
                active_blocker_ids=[], active_decision_ids=[],
            ),
            horizon=None, context=None,
        ) == [], (
            "candidates come from real dependency state, not invention"
        )

    def test_generation_is_deterministic(self):
        generator = StructuralScenarioGenerator()
        state = self._state()
        assert [
            c.id for c in generator.generate_events(
                temporal_state=state, horizon=None, context=None,
            )
        ] == [
            c.id for c in generator.generate_events(
                temporal_state=state, horizon=None, context=None,
            )
        ]


class TestExportCommand:
    @pytest.fixture
    def seeded(self, runner, workspace):
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        return db

    def test_stdout_is_clean_json(self, runner, seeded):
        """`lltpm runtime export > file.json` must produce valid JSON."""
        result = runner.invoke(
            runtime_cli,
            ["export", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed["record_count"] > 0
        assert parsed["integrity"]["hash_chain_valid"] is True

    def test_a_selection_carries_the_integrity_verdict_too(
        self, runner, seeded,
    ):
        result = runner.invoke(
            runtime_cli,
            [
                "export", "--db", seeded, "--organization", "acme",
                "--what", "evidence",
            ],
        )
        parsed = json.loads(result.stdout)
        assert "evidence_provenance" in parsed
        assert "integrity" in parsed, (
            "a partial export must still say whether its history verified"
        )

    def test_payloads_can_be_omitted(self, runner, seeded):
        result = runner.invoke(
            runtime_cli,
            [
                "export", "--db", seeded, "--organization", "acme",
                "--no-payloads",
            ],
        )
        parsed = json.loads(result.stdout)
        assert parsed["record_count"] > 0
        assert all(row["payload"] == {} for row in parsed["records"])

    def test_it_can_write_to_a_file(self, runner, seeded, tmp_path):
        target = tmp_path / "export.json"
        result = runner.invoke(
            runtime_cli,
            [
                "export", "--db", seeded, "--organization", "acme",
                "--output", str(target),
            ],
        )
        assert result.exit_code == 0
        assert "exported" in result.output
        assert json.loads(target.read_text())["record_count"] > 0

    def test_a_corrupt_journal_exports_and_exits_nonzero(
        self, runner, seeded,
    ):
        connection = sqlite3.connect(seeded)
        connection.execute(
            "UPDATE journal_records SET payload = ? "
            "WHERE global_sequence = 1",
            ('{"note":"hand edited"}',),
        )
        connection.commit()
        connection.close()

        result = runner.invoke(
            runtime_cli,
            ["export", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 1, (
            "an export of damaged history must not look like a clean one"
        )
        parsed = json.loads(result.stdout)
        assert parsed["integrity"]["hash_chain_valid"] is False
        assert parsed["integrity"]["corrupted_at_sequence"] == 1

    def test_exporting_does_not_change_the_journal(self, runner, seeded):
        before = _head_sequence(seeded)
        runner.invoke(
            runtime_cli,
            ["export", "--db", seeded, "--organization", "acme"],
        )
        assert _head_sequence(seeded) == before

    def test_an_unknown_selection_is_refused(self, runner, seeded):
        result = runner.invoke(
            runtime_cli,
            [
                "export", "--db", seeded, "--organization", "acme",
                "--what", "everything-please",
            ],
        )
        assert result.exit_code == 2


# ── Commit 36: logging and following ─────────────────────────────────


def _all_output(result) -> str:
    """Stdout plus stderr, across click versions.

    Older click mixes the two and refuses `result.stderr`; newer click
    separates them. The log stream is on stderr either way.
    """
    try:
        return result.output + (result.stderr or "")
    except (ValueError, AttributeError):
        return result.output


def _report(result) -> list[str]:
    """Only the command's own report, with log lines filtered out.

    Some click versions mix stderr into `output`, so the report has to
    be separated by shape rather than by stream.
    """
    return [
        line for line in result.output.splitlines()
        if not line.startswith("{")
        and not _TIMESTAMPED.match(line)
    ]


_TIMESTAMPED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


class TestLoggingOptions:
    @pytest.fixture
    def seeded(self, runner, workspace):
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        return db

    def test_logging_is_off_by_default_in_the_command_output(
        self, runner, workspace,
    ):
        """The command's own report is the interface; logs are opt-in."""
        _, db, evidence = workspace
        result = _ingest(runner, db, evidence)
        assert "lltpm.runtime" not in result.output

    def test_an_info_level_run_says_what_the_process_did(
        self, runner, workspace,
    ):
        _, db, evidence = workspace
        result = runner.invoke(
            runtime_cli,
            [
                "--log-level", "info", "ingest", evidence,
                "--db", db, "--organization", "acme",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "evidence journalled" in _all_output(result)

    def test_json_logs_are_one_parseable_object_per_line(
        self, runner, workspace,
    ):
        _, db, evidence = workspace
        result = runner.invoke(
            runtime_cli,
            [
                "--log-level", "info", "--log-format", "json",
                "ingest", evidence, "--db", db, "--organization", "acme",
            ],
        )
        lines = [
            line for line in _all_output(result).splitlines()
            if line.startswith("{")
        ]
        assert lines, _all_output(result)
        for line in lines:
            document = json.loads(line)
            assert {"timestamp", "level", "logger", "message"} <= set(document)

    def test_the_logs_carry_the_revision_the_command_reported(
        self, runner, workspace,
    ):
        """The join key. Without it a log line cannot be tied back to the
        durable revision it describes."""
        _, db, evidence = workspace
        result = runner.invoke(
            runtime_cli,
            [
                "--log-level", "info", "--log-format", "json",
                "ingest", evidence, "--db", db, "--organization", "acme",
            ],
        )
        revisions = {
            json.loads(line).get("revision_id")
            for line in _all_output(result).splitlines()
            if line.startswith("{")
        }
        assert "revision-1" in revisions

    def test_the_logs_name_the_organization(self, runner, workspace):
        _, db, evidence = workspace
        result = runner.invoke(
            runtime_cli,
            [
                "--log-level", "info", "--log-format", "json",
                "ingest", evidence, "--db", db, "--organization", "acme",
            ],
        )
        organizations = {
            json.loads(line).get("organization_id")
            for line in _all_output(result).splitlines()
            if line.startswith("{")
        }
        assert organizations - {None} == {"acme"}, (
            "a line that names an organization must name the right one; "
            "lines about a single artifact legitimately name none"
        )

    def test_a_replay_marks_its_lines_as_replay(self, runner, seeded):
        """Reading a log and mistaking reconstruction for live work is
        how a replay gets blamed for something it only rebuilt."""
        result = runner.invoke(
            runtime_cli,
            [
                "--log-level", "info", "--log-format", "json",
                "replay", "--db", seeded, "--organization", "acme",
            ],
        )
        modes = {
            json.loads(line).get("execution_mode")
            for line in _all_output(result).splitlines()
            if line.startswith("{")
        }
        assert "replay" in modes

    def test_logging_changes_no_outcome(self, runner, workspace, tmp_path):
        """Observability must be additive. If turning logs on changed a
        result, the logs would be part of the computation."""
        _, _, evidence = workspace
        quiet_db = str(tmp_path / "quiet.db")
        loud_db = str(tmp_path / "loud.db")
        quiet = runner.invoke(
            runtime_cli,
            [
                "--log-level", "off", "ingest", evidence,
                "--db", quiet_db, "--organization", "acme",
            ],
        )
        loud = runner.invoke(
            runtime_cli,
            [
                "--log-level", "debug", "ingest", evidence,
                "--db", loud_db, "--organization", "acme",
            ],
        )
        assert quiet.exit_code == loud.exit_code == 0
        assert _report(quiet) == _report(loud), (
            "the command's own report must not depend on the log level"
        )
        assert _head_sequence(quiet_db) == _head_sequence(loud_db)

    def test_an_unknown_level_is_refused(self, runner, workspace):
        _, db, evidence = workspace
        result = runner.invoke(
            runtime_cli,
            ["--log-level", "chatty", "ingest", evidence, "--db", db],
        )
        assert result.exit_code == 2


class TestJournalFollow:
    @pytest.fixture
    def seeded(self, runner, workspace):
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        return db

    def _follow(self, runner, db, *extra):
        return runner.invoke(
            runtime_cli,
            [
                "journal", "--db", db, "--follow",
                "--poll-seconds", "0.01", "--idle-timeout", "0.01", *extra,
            ],
        )

    def test_an_explicit_start_replays_the_tail_then_follows(
        self, runner, seeded,
    ):
        result = self._follow(runner, seeded, "--start", "1")
        assert result.exit_code == 0, result.output
        assert result.output.count("evidence_accepted") >= 3

    def test_without_a_start_it_follows_from_the_head(self, runner, seeded):
        """tail -f semantics: a watcher wants what happens next, not a
        reprint of everything that already happened."""
        result = self._follow(runner, seeded)
        assert result.exit_code == 0
        assert "evidence_accepted" not in result.output

    def test_it_can_filter_by_event_type(self, runner, seeded):
        result = self._follow(
            runner, seeded, "--start", "1",
            "--event-type", "intelligence_revision_published",
        )
        assert result.exit_code == 0
        assert "evidence_accepted" not in result.output
        assert "intelligence_revision_published" in result.output

    def test_an_empty_journal_can_be_followed(self, runner, tmp_path):
        result = self._follow(runner, str(tmp_path / "empty.db"))
        assert result.exit_code == 0, result.output
        assert "Journal is empty" not in result.output, (
            "an empty journal is exactly when someone wants to watch it"
        )

    def test_following_changes_nothing(self, runner, seeded):
        before = _head_sequence(seeded)
        self._follow(runner, seeded, "--start", "1")
        assert _head_sequence(seeded) == before


class TestAnchorCommand:
    """Commit 39: the retained anchor.

    Hash chaining catches an in-place edit. It cannot catch a history
    rewritten wholesale, because every link in a rewritten chain
    recomputes. The anchor is the operator's copy of the head, kept
    where the database is not.
    """

    @pytest.fixture
    def seeded(self, runner, workspace):
        _, db, evidence = workspace
        _ingest(runner, db, evidence)
        return db

    def test_an_anchor_records_the_head(self, runner, seeded):
        result = runner.invoke(
            runtime_cli, ["anchor", "--db", seeded, "--organization", "acme"],
        )
        assert result.exit_code == 0, result.output
        anchor = json.loads(result.stdout)
        assert anchor["global_sequence"] == _head_sequence(seeded)
        assert anchor["record_hash"]

    def test_it_can_be_written_to_a_file(self, runner, seeded, tmp_path):
        path = tmp_path / "anchor.json"
        result = runner.invoke(runtime_cli, [
            "anchor", "--db", seeded, "--organization", "acme",
            "-o", str(path),
        ])
        assert result.exit_code == 0
        assert json.loads(path.read_text())["record_hash"]

    def test_an_empty_journal_cannot_be_anchored(self, runner, tmp_path):
        result = runner.invoke(
            runtime_cli, ["anchor", "--db", str(tmp_path / "empty.db")],
        )
        assert result.exit_code == 1
        assert "nothing yet" in _all_output(result)

    def test_verifying_against_the_anchor_passes(
        self, runner, seeded, tmp_path,
    ):
        path = tmp_path / "anchor.json"
        runner.invoke(runtime_cli, [
            "anchor", "--db", seeded, "-o", str(path),
        ])
        result = runner.invoke(runtime_cli, [
            "verify", "--db", seeded, "--anchor", str(path),
        ])
        assert result.exit_code == 0
        assert "matches the retained anchor" in result.output

    def test_verifying_against_a_wrong_head_fails(self, runner, seeded):
        result = runner.invoke(runtime_cli, [
            "verify", "--db", seeded, "--expect-head", "0" * 64,
        ])
        assert result.exit_code == 1
        # rich wraps the explanation, so match on a phrase that cannot
        # straddle a line break rather than on a whole sentence.
        assert "retained anchor" in result.output
        assert "anchored head" in result.output

    def test_an_unanchored_verify_says_what_it_did_not_check(
        self, runner, seeded,
    ):
        result = runner.invoke(runtime_cli, ["verify", "--db", seeded])
        assert result.exit_code == 0
        output = _all_output(result)
        assert "wholesale rewrite would not be detected" in output
        assert "run `lltpm runtime anchor`" in output

    def test_status_shows_the_head_hash(self, runner, seeded):
        result = runner.invoke(
            runtime_cli, ["status", "--db", seeded, "--organization", "acme"],
        )
        assert "Head hash" in result.output

    def test_anchoring_changes_nothing(self, runner, seeded):
        before = _head_sequence(seeded)
        runner.invoke(runtime_cli, ["anchor", "--db", seeded])
        assert _head_sequence(seeded) == before


class TestExplanation:
    """`ingest` must say what the intelligence concluded.

    The orchestrator always computed a per-project account of what moved
    and which evidence moved it; the command printed `published
    revision-1` and discarded it, so the useful half of the work was
    reachable only from Python. These tests pin the surface, and pin that
    it stays honest about confidence.
    """

    def test_it_reports_which_evidence_moved_the_intelligence(
        self, runner, workspace,
    ):
        _, db, evidence = workspace
        result = _ingest(runner, db, evidence)
        output = _all_output(result)
        assert "because" in output
        # Reality time, not the time of the run: the blocker was opened
        # on the 19th whatever day this test executes on.
        assert "2026-10-19" in output

    def test_a_probability_move_never_travels_without_its_confidence(
        self, runner, workspace,
    ):
        """The one line a reader is most likely to quote out of context.

        A displayed probability move with the confidence silently dropped
        would read as a forecast. The baseline generator's confidence is
        0.00, and the output has to say so in the same breath.
        """
        _, db, evidence = workspace
        result = _ingest(runner, db, evidence)
        output = _all_output(result)
        assert "P(blocker_persisted)" in output
        assert "confidence" in output

    def test_no_explain_still_publishes(self, runner, workspace):
        """Scripted use can turn the prose off without changing the work."""
        _, db, evidence = workspace
        result = _ingest(runner, db, evidence, "--no-explain")
        assert result.exit_code == 0
        output = _all_output(result)
        assert "published revision" in output
        assert "because" not in output

    def test_turning_the_explanation_off_changes_no_durable_state(
        self, runner, workspace, tmp_path,
    ):
        """Reporting is not computation."""
        _, db, evidence = workspace
        other = str(tmp_path / "quiet.db")
        _ingest(runner, db, evidence)
        _ingest(runner, other, evidence, "--no-explain")
        assert _count_evidence(db) == _count_evidence(other)
        assert _head_sequence(db) == _head_sequence(other)

    def test_evidence_derived_text_cannot_colour_the_terminal(self):
        """A ticket title is data, not Rich markup.

        Jira summaries and Slack messages reach these strings. A title of
        `[red]URGENT[/red]` must print as itself rather than be consumed
        as a style tag — the same escaping discipline as the dashboard's
        HTML, applied to the terminal.
        """
        from lltpm.runtime.models import (
            LiveOrchestrationResult,
            ProjectIntelligenceUpdate,
        )
        from lltpm.runtime_cli import _explain

        hostile = "[red]URGENT[/red] blocked on /tmp/x"
        result = LiveOrchestrationResult(
            project_updates=[
                ProjectIntelligenceUpdate(
                    project_id="[bold]billing[/bold]",
                    revision_id="revision-1",
                    what_changed=[hostile],
                ),
            ],
        )
        with _captured() as output:
            _explain(result)
        assert "[red]URGENT[/red]" in output(), (
            "the tag was consumed as markup instead of printed as text"
        )
        assert "[bold]billing[/bold]" in output()

    def test_nothing_is_printed_when_there_is_nothing_to_say(self):
        from lltpm.runtime.models import (
            LiveOrchestrationResult,
            ProjectIntelligenceUpdate,
        )
        from lltpm.runtime_cli import _explain

        result = LiveOrchestrationResult(
            project_updates=[
                ProjectIntelligenceUpdate(
                    project_id="billing", revision_id="revision-1",
                ),
            ],
        )
        with _captured() as output:
            _explain(result)
        assert output().strip() == "", (
            "an empty update must not print a heading with nothing under it"
        )


@contextlib.contextmanager
def _captured():
    """Capture what the CLI's shared console writes.

    `console` is module-level, so the test swaps its file rather than
    constructing a second console that would not be the one under test.
    """
    import io

    from lltpm import runtime_cli

    buffer = io.StringIO()
    original = runtime_cli.console
    runtime_cli.console = Console(file=buffer, width=100, no_color=True)
    try:
        yield buffer.getvalue
    finally:
        runtime_cli.console = original
