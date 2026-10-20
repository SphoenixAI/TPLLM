"""The ``lltpm runtime`` command group (Commit 32).

Until now the organizational-reasoning stack had no entry point. The
engines ran, the journal was durable, and nothing a user could type
reached either. These commands are that surface.

They work offline with no API key: ``--local`` opens one SQLite file and
the baseline providers supply the two Protocols that would otherwise
need a model. The commands that only read durable history — ``verify``,
``journal``, ``status`` — need no providers at all.

Every command that can change state says what it changed, and the two
that cannot change anything (``verify``, ``journal``) are read-only by
construction.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
# Notes and warnings go to stderr so `lltpm runtime export > file.json`
# produces clean JSON while the operator still sees the caveats.
errors = Console(stderr=True)

DEFAULT_DB = ""  # resolved against LLTPM_DATA_DIR; see _resolve_db
DEFAULT_ORGANIZATION = "default"
DB_FILENAME = "lltpm.db"


def _resolve_db(path: str) -> str:
    """Where the journal actually lives.

    An explicit --db wins. Otherwise the database goes in the configured
    data directory, which is what makes the container work: the image
    sets LLTPM_DATA_DIR=/data and mounts a volume there, while the
    working directory is /app. A bare relative default would put the
    journal at /app/lltpm.db — off the volume, and destroyed on restart.
    Durability that a default deployment throws away is not durability.
    """
    if path:
        return path

    data_dir = os.getenv("LLTPM_DATA_DIR")
    if data_dir:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory / DB_FILENAME)
    return DB_FILENAME


def _open_persistence(path: str):
    from lltpm.persistence import SQLitePersistenceConfig
    from lltpm.persistence.sqlite import initialize_sqlite_persistence

    return initialize_sqlite_persistence(
        SQLitePersistenceConfig(path=path)
    )


def _manifest(policy_fingerprint: str = "cli-default"):
    from lltpm.persistence import build_manifest
    from lltpm.runtime.baselines import (
        BASELINE_RECOMMENDATION_METHOD,
        BASELINE_SCENARIO_METHOD,
    )

    # The manifest records that these artifacts came from baselines, so
    # nothing later mistakes them for model-derived output.
    return build_manifest(
        policy_fingerprint=policy_fingerprint,
        scenario_method_version=BASELINE_SCENARIO_METHOD,
        recommendation_method_version=BASELINE_RECOMMENDATION_METHOD,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _horizon(now: datetime, days: int):
    """A forecast needs a stated window; this is the CLI's default one.

    Explicit rather than implicit: the horizon bounds what the lattice
    is allowed to claim, so it is a parameter rather than a constant
    buried in the engine.
    """
    from lltpm.scenarios.models import ScenarioHorizon

    return ScenarioHorizon(
        start_at=now,
        end_at=now + timedelta(days=days),
        maximum_depth=4,
        minimum_branch_probability=0.02,
    )


@click.group(name="runtime")
def runtime_cli() -> None:
    """Live organizational intelligence: ingest, inspect, replay.

    State lives in one local SQLite file. No cloud account, no broker,
    no daemon.
    """


# ── Read-only inspection ─────────────────────────────────────────────


@runtime_cli.command()
@click.option(
    "--db", default=DEFAULT_DB,
    help="SQLite path; defaults to $LLTPM_DATA_DIR/lltpm.db",
)
@click.option("--organization", default=DEFAULT_ORGANIZATION)
def status(db: str, organization: str) -> None:
    """Current revision, journal head, and storage health."""
    from lltpm.persistence import inspect_persistence_health

    persistence = _open_persistence(_resolve_db(db))
    try:
        report = inspect_persistence_health(
            organization_id=organization,
            journal=persistence.journal,
            snapshots=persistence.snapshots,
            state_store=persistence.state,
        )
        console.print(Panel(
            f"Journal head: {report.journal_head_sequence}\n"
            f"Hash chain:   "
            f"{'valid' if report.journal_hash_valid else 'BROKEN'}\n"
            f"Revision:     {report.current_revision_id or 'none'}\n"
            f"Snapshot:     {report.latest_snapshot_id or 'none'}",
            title=f"LLTPM runtime — {organization}",
            border_style="blue" if report.journal_hash_valid else "red",
        ))

        states = [
            s for s in persistence.state.all_project_states()
        ] if hasattr(persistence.state, "all_project_states") else []
        if not states:
            states = [
                s for s in (
                    persistence.state.get_project_state(p)
                    for p in _known_projects(persistence)
                ) if s is not None
            ]
        if states:
            table = Table(title="Project state")
            table.add_column("Project")
            table.add_column("Revision")
            table.add_column("Fingerprint")
            for state in sorted(states, key=lambda s: s.project_id):
                table.add_row(
                    state.project_id,
                    state.revision_id or "-",
                    (state.state_fingerprint or "-")[:16],
                )
            console.print(table)

        for warning in report.warnings:
            console.print(f"[yellow]warning[/yellow] {warning}")
    finally:
        persistence.close()


def _seeded_projection(persistence, organization: str):
    """A projection whose revision counter continues from durable state.

    Each CLI invocation builds a fresh in-memory projection, so without
    this every ingest would allocate "revision-1" again and the second
    one would dedupe against the first's audit marker — making two
    genuinely different recomputations look like one.
    """
    from lltpm.runtime import RuntimeStateStore as RuntimeProjection

    projection = RuntimeProjection(organization_id=organization)
    existing = (
        persistence.state.list_revisions(organization)
        if hasattr(persistence.state, "list_revisions") else []
    )
    highest = 0
    for revision in existing:
        suffix = revision.revision_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return projection.with_revision_index(highest)


def _known_projects(persistence) -> list[str]:
    from lltpm.runtime.sources import JournalBackedDataSource

    return JournalBackedDataSource(
        journal=persistence.journal,
    ).project_ids()


@runtime_cli.command()
@click.option(
    "--db", default=DEFAULT_DB,
    help="SQLite path; defaults to $LLTPM_DATA_DIR/lltpm.db",
)
def verify(db: str) -> None:
    """Verify the journal hash chain. Read-only; never repairs."""
    from lltpm.persistence import verify_journal

    persistence = _open_persistence(_resolve_db(db))
    try:
        report = verify_journal(journal=persistence.journal)
        if report.valid:
            console.print(f"[green]ok[/green] {report.explanation}")
            return
        console.print(f"[red]corrupt[/red] {report.explanation}")
        if report.corrupted_at_sequence is not None:
            console.print(
                f"       first bad sequence: {report.corrupted_at_sequence}"
            )
        console.print(
            "       History is reported as found, never silently repaired."
        )
        raise SystemExit(1)
    finally:
        persistence.close()


@runtime_cli.command()
@click.option(
    "--db", default=DEFAULT_DB,
    help="SQLite path; defaults to $LLTPM_DATA_DIR/lltpm.db",
)
@click.option("--start", default=1, help="First global sequence")
@click.option("--end", default=0, help="Last global sequence (0 = head)")
@click.option("--event-type", default="", help="Filter by event type")
def journal(db: str, start: int, end: int, event_type: str) -> None:
    """Inspect a range of journal records. Read-only."""
    from lltpm.persistence import JournalEventType, inspect_journal_range

    persistence = _open_persistence(_resolve_db(db))
    try:
        head = persistence.journal.latest_position()
        if head is None:
            console.print("Journal is empty.")
            return
        event_types = None
        if event_type:
            try:
                event_types = [JournalEventType(event_type)]
            except ValueError:
                console.print(
                    f"[red]error[/red] unknown event type '{event_type}'"
                )
                raise SystemExit(2) from None

        records = inspect_journal_range(
            journal=persistence.journal,
            start_sequence=start,
            end_sequence=end or head.global_sequence,
            event_types=event_types,
        )
        table = Table(title=f"Journal {start}..{end or head.global_sequence}")
        table.add_column("#", justify="right")
        table.add_column("Event")
        table.add_column("Occurred")
        table.add_column("Projects")
        for record in records:
            table.add_row(
                str(record.position.global_sequence),
                record.event_type.value,
                record.occurred_at.isoformat(timespec="minutes"),
                ", ".join(record.project_ids) or "-",
            )
        console.print(table)
    finally:
        persistence.close()


@runtime_cli.command()
@click.option(
    "--db", default=DEFAULT_DB,
    help="SQLite path; defaults to $LLTPM_DATA_DIR/lltpm.db",
)
@click.option("--organization", default=DEFAULT_ORGANIZATION)
@click.option(
    "--output", "-o", type=click.Path(), default="",
    help="Write JSON here instead of stdout",
)
@click.option(
    "--what",
    type=click.Choice(
        ["all", "decisions", "recommendations", "policy", "evidence"],
    ),
    default="all", show_default=True,
)
@click.option(
    "--no-payloads", is_flag=True,
    help="Omit record payloads; keep the index and provenance",
)
def export(
    db: str, organization: str, output: str, what: str, no_payloads: bool,
) -> None:
    """Export durable history as JSON. Read-only.

    The export carries its own integrity verdict, so a copy that was
    never verified cannot be mistaken for one that was.
    """
    from lltpm.persistence import export_journal

    persistence = _open_persistence(_resolve_db(db))
    try:
        bundle = export_journal(
            journal=persistence.journal,
            organization_id=organization,
            exported_at=_now(),
            manifest=_manifest(),
            include_payloads=not no_payloads,
        )

        selection = {
            "all": bundle.model_dump(mode="json"),
            "decisions": {"decisions": bundle.decisions},
            "recommendations": {"recommendations": bundle.recommendations},
            "policy": {"policy_history": bundle.policy_history},
            "evidence": {
                "evidence_provenance": [
                    p.model_dump(mode="json")
                    for p in bundle.evidence_provenance
                ],
            },
        }[what]
        if what != "all":
            selection["integrity"] = bundle.integrity.model_dump(mode="json")

        text = json.dumps(selection, indent=2, sort_keys=True)
        if output:
            Path(output).write_text(text)
            console.print(
                f"[green]exported[/green] {bundle.record_count} record(s) "
                f"to {output}"
            )
        else:
            click.echo(text)

        for note in bundle.notes:
            errors.print(f"[yellow]note[/yellow] {note}")
        if not bundle.trustworthy:
            errors.print(
                "[red]warning[/red] this export describes history that "
                "failed verification"
            )
            raise SystemExit(1)
    finally:
        persistence.close()


@runtime_cli.command()
def coverage() -> None:
    """Which artifact types run a dedicated engine, and which do not."""
    from lltpm.runtime.services import coverage_report

    report = coverage_report()
    table = Table(title="Artifact coverage")
    table.add_column("Artifact type")
    table.add_column("Backing")
    for name in report.engine_backed:
        table.add_row(name, "[green]engine[/green]")
    for name in report.derived:
        table.add_row(name, "[yellow]derived projection[/yellow]")
    for name, reason in sorted(report.unavailable.items()):
        table.add_row(name, f"[red]unavailable[/red] — {reason}")
    console.print(table)
    console.print(
        "\nA derived projection is real and fingerprinted but thinner "
        "than a dedicated engine would produce."
    )
    if report.unavailable:
        console.print(
            "Unavailable types need declared organizational structure "
            "(capacity windows, launch definitions) before they can be "
            "computed from anything real."
        )


# ── State-changing ───────────────────────────────────────────────────


@runtime_cli.command()
@click.argument("evidence_file", type=click.Path(exists=True))
@click.option(
    "--db", default=DEFAULT_DB,
    help="SQLite path; defaults to $LLTPM_DATA_DIR/lltpm.db",
)
@click.option("--organization", default=DEFAULT_ORGANIZATION)
@click.option(
    "--horizon-days", default=14, show_default=True,
    help="Forecast window the scenario lattice may reason over",
)
def ingest(
    evidence_file: str, db: str, organization: str, horizon_days: int,
) -> None:
    """Journal evidence from a JSON file, then recompute intelligence.

    Journal first: the evidence is durable before anything derives from
    it, so a recompute that fails leaves the evidence intact and a later
    ``replay`` rebuilds the projection.
    """
    from lltpm.integrations.envelopes import make_envelope
    from lltpm.runtime import (
        process_durable_organizational_events,
        registry_from_journal,
    )
    from lltpm.runtime.baselines import baseline_providers
    from lltpm.runtime.services import LiveIntelligenceServices
    from lltpm.runtime.sources import JournalBackedDataSource

    payload = json.loads(Path(evidence_file).read_text())
    events = payload if isinstance(payload, list) else payload.get("events", [])
    if not events:
        console.print("[yellow]nothing to ingest[/yellow]")
        return

    now = _now()
    envelopes = [
        make_envelope(
            source_system=item.get("source_system", "manual"),
            source_type=item.get("source_type", "document"),
            source_id=str(item["source_id"]),
            event_type=item.get("event_type", "chat_message"),
            observed_at=datetime.fromisoformat(item["observed_at"]),
            project_ids=list(item.get("project_ids", [])),
            content=item.get("content"),
            title=item.get("title"),
            metadata=item.get("metadata", {}),
            received_at=now,
        )
        for item in events
    ]

    persistence = _open_persistence(_resolve_db(db))
    try:
        data_source = JournalBackedDataSource(
            journal=persistence.journal, organization_id=organization,
        )
        services = LiveIntelligenceServices(
            data_source=data_source,
            as_of=now,
            scenario_horizon=_horizon(now, horizon_days),
            **baseline_providers(),
        )
        # Canonical identity is rebuilt from durable history rather than
        # re-resolved from scratch: the decisions already made stand, and
        # a replay does not need the source systems to still answer.
        registry = registry_from_journal(
            journal=persistence.journal, organization_id=organization,
        )
        durable, projection = process_durable_organizational_events(
            envelopes=envelopes,
            organization_id=organization,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=_seeded_projection(
                persistence, organization,
            ),
            intelligence_services=services,
            runtime_policy=None,
            manifest=_manifest(),
            now=now,
            identity_registry=registry,
        )

        console.print(
            f"Journalled {durable.evidence_records_appended} evidence "
            f"record(s)."
        )
        if durable.identities_linked:
            console.print(
                f"Resolved {len(durable.identities_linked)} canonical "
                f"identity link(s)."
            )
        if durable.identities_ambiguous:
            console.print(
                f"  [yellow]{len(durable.identities_ambiguous)} identity "
                f"(ies) ambiguous[/yellow] — left unlinked for a person to "
                f"settle rather than guessed"
            )
        result = durable.result
        if result.complete:
            console.print(
                f"[green]published[/green] revision {result.revision_id} "
                f"across {len(result.projects_affected)} project(s)"
            )
        else:
            console.print(
                "[yellow]no revision published[/yellow] — the evidence is "
                "durable; run `lltpm runtime replay` once the inputs it "
                "needs are configured."
            )
        for failure in result.failures:
            console.print(
                f"  [red]{failure.artifact_type.value}[/red] "
                f"{failure.explanation}"
            )
        for action in result.attention_actions:
            console.print(f"  attention: {action.value}")
        for reason in result.no_action_reasons:
            console.print(f"  no action: {reason.value}")
    finally:
        persistence.close()


@runtime_cli.command()
@click.option(
    "--db", default=DEFAULT_DB,
    help="SQLite path; defaults to $LLTPM_DATA_DIR/lltpm.db",
)
@click.option("--organization", default=DEFAULT_ORGANIZATION)
@click.option(
    "--publish/--dry-run", default=False,
    help="Write the reconstruction back as current state",
)
@click.option("--horizon-days", default=14, show_default=True)
def replay(
    db: str, organization: str, publish: bool, horizon_days: int,
) -> None:
    """Rebuild projections from the journal.

    Dry run by default. Replay never touches an external system, so this
    is safe to run against a live database.
    """
    from lltpm.persistence import (
        ReplayMode,
        ReplayRequest,
        replay_journal,
    )
    from lltpm.runtime import RuntimeJournalReconstructor
    from lltpm.runtime.baselines import baseline_providers
    from lltpm.runtime.services import LiveIntelligenceServices
    from lltpm.runtime.sources import JournalBackedDataSource

    persistence = _open_persistence(_resolve_db(db))
    try:
        data_source = JournalBackedDataSource(
            journal=persistence.journal, organization_id=organization,
        )
        now = _now()
        services = LiveIntelligenceServices(
            data_source=data_source,
            as_of=now,
            scenario_horizon=_horizon(now, horizon_days),
            **baseline_providers(),
        )
        result = replay_journal(
            request=ReplayRequest(
                organization_id=organization,
                mode=(
                    ReplayMode.RECONSTRUCT_CURRENT if publish
                    else ReplayMode.VERIFY_EQUIVALENCE
                ),
                publish_result=publish,
            ),
            journal=persistence.journal,
            snapshots=persistence.snapshots,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            reconstructor=RuntimeJournalReconstructor(
                intelligence_services=services,
            ),
            manifest=_manifest(),
        )

        console.print(Panel(
            f"Replayed:   {result.records_replayed} record(s) "
            f"({result.start_sequence}..{result.end_sequence})\n"
            f"Snapshot:   {result.snapshot_id or 'none (full rebuild)'}\n"
            f"Complete:   {result.complete}\n"
            f"Suppressed: {result.side_effects_suppressed} external "
            f"effect(s)\n"
            f"Published:  {result.published}",
            title="Replay",
            border_style="green" if result.complete else "red",
        ))
        for mismatch in result.mismatches:
            console.print(f"  [red]mismatch[/red] {mismatch}")
        for limitation in result.evidence_limitations:
            console.print(f"  [yellow]limitation[/yellow] {limitation}")
        for warning in result.warnings:
            console.print(f"  warning {warning}")
        if not result.complete:
            raise SystemExit(1)
    finally:
        persistence.close()


@runtime_cli.command()
@click.option(
    "--db", default=DEFAULT_DB,
    help="SQLite path; defaults to $LLTPM_DATA_DIR/lltpm.db",
)
@click.option("--organization", default=DEFAULT_ORGANIZATION)
def snapshot(db: str, organization: str) -> None:
    """Checkpoint current state so future replays start from here."""
    from lltpm.persistence import build_runtime_snapshot

    persistence = _open_persistence(_resolve_db(db))
    try:
        head = persistence.journal.latest_position()
        if head is None:
            console.print("[yellow]journal is empty[/yellow]")
            return
        revision_id = persistence.state.get_current_revision(organization)
        if revision_id is None:
            console.print(
                "[yellow]no current revision[/yellow] — run "
                "`lltpm runtime replay --publish` first"
            )
            raise SystemExit(1)

        projects = _known_projects(persistence)
        states = {}
        fingerprints = {}
        for project_id in projects:
            stored = persistence.state.get_project_state(project_id)
            if stored is None:
                continue
            states[project_id] = stored.state
            fingerprints[f"project:{project_id}"] = stored.state_fingerprint
        portfolio = persistence.state.get_portfolio_state(organization)
        if portfolio is not None:
            fingerprints["portfolio"] = portfolio.state_fingerprint

        built = build_runtime_snapshot(
            snapshot_id=f"snap-{head.global_sequence}",
            organization_id=organization,
            journal_global_sequence=head.global_sequence,
            active_revision_id=revision_id,
            project_states=states,
            portfolio_state=portfolio.state if portfolio else None,
            state_fingerprints=fingerprints,
            manifest=_manifest(),
            created_at=_now(),
        )
        persistence.snapshots.put_snapshot(built)
        console.print(
            f"[green]snapshot[/green] {built.id} at sequence "
            f"{built.journal_global_sequence}"
        )
    finally:
        persistence.close()
