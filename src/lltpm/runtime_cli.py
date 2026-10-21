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
from datetime import datetime
from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from lltpm.cli_support import (
    DEFAULT_DB,
    DEFAULT_ORGANIZATION,
    console,
    db_option,
    errors,
    horizon as _horizon,
    known_projects as _known_projects,
    logging_options,
    manifest as _manifest,
    now as _now,
    open_persistence as _open_persistence,
    resolve_db as _resolve_db,
    seeded_projection as _seeded_projection,
    start_logging,
)


@click.group(name="runtime")
@logging_options
def runtime_cli(log_level: str, log_format: str) -> None:
    """Live organizational intelligence: ingest, inspect, replay.

    State lives in one local SQLite file. No cloud account, no broker,
    no daemon.

    These commands report what they did on stdout. --log-level adds the
    process's own account of itself — which engine ran, what was
    deduplicated, why a revision was not promoted — on stderr, so
    redirecting stdout to a file keeps both usable.
    """
    start_logging(log_level, log_format)


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
        from lltpm.persistence import build_journal_anchor

        anchor = build_journal_anchor(
            journal=persistence.journal, created_at=_now(),
            organization_id=organization,
        )
        console.print(Panel(
            f"Journal head: {report.journal_head_sequence}\n"
            f"Head hash:    "
            f"{(anchor.record_hash if anchor else '-')[:32]}\n"
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


def _head_sequence(persistence) -> int:
    head = persistence.journal.latest_position()
    return head.global_sequence if head is not None else 0


def _follow(
    *,
    persistence,
    after_sequence: int,
    event_types,
    poll_seconds: float,
    idle_timeout: float | None,
) -> None:
    """Print durable records as they are appended.

    Read-only, like the rest of ``journal``: the follower polls a
    strictly increasing sequence and never writes. One line per record
    rather than a table, because a table cannot be streamed.
    """
    from lltpm.persistence.follow import follow_journal

    wanted = set(event_types) if event_types else None
    errors.print(
        f"[dim]following from sequence {after_sequence + 1}; "
        f"Ctrl-C to stop[/dim]"
    )

    def emit(record) -> None:
        if wanted is not None and record.event_type not in wanted:
            return
        console.print(
            f"[dim]#{record.position.global_sequence:>5}[/dim] "
            f"{record.event_type.value} "
            f"[dim]{record.occurred_at.isoformat(timespec='seconds')}[/dim] "
            f"{', '.join(record.project_ids) or '-'}"
        )

    try:
        last = follow_journal(
            journal=persistence.journal,
            on_record=emit,
            after_sequence=after_sequence,
            poll_seconds=poll_seconds,
            idle_timeout_seconds=idle_timeout,
        )
    except KeyboardInterrupt:
        errors.print("\n[dim]stopped[/dim]")
        return
    errors.print(f"[dim]idle; stopped at sequence {last}[/dim]")


@runtime_cli.command()
@click.option(
    "--db", default=DEFAULT_DB,
    help="SQLite path; defaults to $LLTPM_DATA_DIR/lltpm.db",
)
@click.option(
    "--anchor", "anchor_file", type=click.Path(exists=True), default=None,
    help="Anchor file from `lltpm runtime anchor`, retained out of band",
)
@click.option(
    "--expect-head", default="",
    help="Expected head hash, if you kept it rather than a file",
)
def verify(db: str, anchor_file: str | None, expect_head: str) -> None:
    """Verify the journal hash chain. Read-only; never repairs.

    Without an anchor this checks internal consistency: an in-place
    edit, a reorder, a removed record, a truncation. It cannot detect a
    history that was rewritten wholesale, because every link in a
    rewritten chain recomputes correctly.

    With `--anchor` or `--expect-head` it also checks the head against
    a value you kept somewhere the database is not, which is what
    catches that case.
    """
    from lltpm.persistence import JournalAnchor, verify_journal

    anchor = None
    if anchor_file:
        anchor = JournalAnchor.model_validate_json(
            Path(anchor_file).read_text()
        )
    elif expect_head:
        head = _head_sequence_for(db)
        if head == 0:
            errors.print("[red]corrupt[/red] journal is empty")
            raise SystemExit(1)
        anchor = JournalAnchor(
            global_sequence=head, record_hash=expect_head.strip(),
            created_at=_now(),
        )

    persistence = _open_persistence(_resolve_db(db))
    try:
        report = verify_journal(journal=persistence.journal, anchor=anchor)
        if report.valid:
            console.print(f"[green]ok[/green] {report.explanation}")
            if anchor is None:
                errors.print(
                    "[dim]no anchor supplied — run `lltpm runtime anchor` "
                    "and keep the result somewhere this database is "
                    "not[/dim]"
                )
            return
        console.print(f"[red]corrupt[/red] {report.explanation}")
        if report.corrupted_at_sequence is not None:
            console.print(
                f"       first bad sequence: {report.corrupted_at_sequence}"
            )
        if report.anchor_matched is False:
            console.print(
                f"       anchored head: {report.expected_hash}\n"
                f"       actual head:   {report.actual_hash}"
            )
        console.print(
            "       History is reported as found, never silently repaired."
        )
        raise SystemExit(1)
    finally:
        persistence.close()


def _head_sequence_for(db: str) -> int:
    persistence = _open_persistence(_resolve_db(db))
    try:
        return _head_sequence(persistence)
    finally:
        persistence.close()


@runtime_cli.command()
@db_option
@click.option("--organization", default=DEFAULT_ORGANIZATION)
@click.option(
    "--output", "-o", type=click.Path(), default="",
    help="Write the anchor here instead of stdout",
)
def anchor(db: str, organization: str, output: str) -> None:
    """Record the journal head as an anchor to retain out of band.

    Hash chaining makes an in-place edit detectable; it cannot make a
    wholesale rewrite detectable, because whoever rewrites the file can
    recompute every link. Haber and Stornetta's answer in 1990 was to
    publish the head hash where it could not be retracted. This is that
    idea at the scale of one local file: keep the anchor somewhere the
    database is not — another host, a ticket, a printout — and pass it
    back to `lltpm runtime verify --anchor`.

    Read-only. Writes nothing to the journal.
    """
    from lltpm.persistence import LLTPM_VERSION, build_journal_anchor

    persistence = _open_persistence(_resolve_db(db))
    try:
        built = build_journal_anchor(
            journal=persistence.journal, created_at=_now(),
            organization_id=organization, lltpm_version=LLTPM_VERSION,
        )
        if built is None:
            errors.print(
                "[yellow]journal is empty[/yellow] — there is nothing yet "
                "to anchor, and an anchor at sequence zero would be a "
                "statement about history that does not exist"
            )
            raise SystemExit(1)
        text = json.dumps(built.model_dump(mode="json"), indent=2, default=str)
        if output:
            Path(output).write_text(text + "\n")
            console.print(
                f"[green]anchored[/green] sequence "
                f"{built.global_sequence} to {output}"
            )
        else:
            click.echo(text)
        errors.print(
            "[dim]keep this where the database is not; an anchor stored "
            "beside the file it anchors protects against accident, not "
            "against rewriting[/dim]"
        )
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
@click.option(
    "--follow", "-f", is_flag=True,
    help="Stream records as they land, like tail -f; Ctrl-C to stop",
)
@click.option(
    "--poll-seconds", default=1.0, show_default=True,
    help="How often --follow checks for new records",
)
@click.option(
    "--idle-timeout", default=0.0, show_default=True,
    help="With --follow, stop after this many idle seconds (0 = never)",
)
@click.pass_context
def journal(
    ctx: click.Context,
    db: str,
    start: int,
    end: int,
    event_type: str,
    follow: bool,
    poll_seconds: float,
    idle_timeout: float,
) -> None:
    """Inspect a range of journal records. Read-only."""
    from lltpm.persistence import JournalEventType, inspect_journal_range

    persistence = _open_persistence(_resolve_db(db))
    try:
        event_types = None
        if event_type:
            try:
                event_types = [JournalEventType(event_type)]
            except ValueError:
                console.print(
                    f"[red]error[/red] unknown event type '{event_type}'"
                )
                raise SystemExit(2) from None

        if follow:
            _follow(
                persistence=persistence,
                # An explicit --start means "replay from there, then
                # follow"; without one, follow from the head, which is
                # what tail -f does and what someone watching a live
                # ingest actually wants.
                after_sequence=(
                    start - 1
                    if ctx.get_parameter_source("start")
                    is not click.core.ParameterSource.DEFAULT
                    else _head_sequence(persistence)
                ),
                event_types=event_types,
                poll_seconds=poll_seconds,
                idle_timeout=idle_timeout or None,
            )
            return

        head = persistence.journal.latest_position()
        if head is None:
            console.print("Journal is empty.")
            return

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
        if durable.evidence_records_deduplicated:
            console.print(
                f"  {durable.evidence_records_deduplicated} were already "
                f"durable (redelivered, not re-appended)."
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
        if result.complete and durable.revision_promoted:
            console.print(
                f"[green]published[/green] revision {result.revision_id} "
                f"across {len(result.projects_affected)} project(s)"
            )
        elif result.complete:
            # A pass can succeed and still publish nothing: the evidence
            # was processed and judged non-material, or every change was
            # already reflected. "published revision None" was the old
            # output here, which claimed a revision that never existed.
            why = ", ".join(r.value for r in result.no_action_reasons)
            console.print(
                f"[green]accepted[/green] — no revision was needed "
                f"({why or 'nothing changed'})"
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
