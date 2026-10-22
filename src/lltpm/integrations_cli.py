"""The ``lltpm integrations`` command group (Commit 38).

Commit 37 built the translation from provider payloads to evidence and
proved it against recorded responses. Nothing a user could type invoked
it. These commands are that surface, and they are deliberately four
different things rather than one:

``sources``    what can be read, and how trustworthy each contract is.
``normalize``  a payload file to evidence, offline, no network at all.
``ingest``     read a repository and journal what it says. The live path.
``verify``     check the recordings still match the live API.
``record``     re-record the fixtures and regenerate the contracts.

``verify`` and ``record`` are the two halves of a self-initializing fake
kept as explicit commands rather than hidden in a test run, because they
are the only parts that touch the network and that should be a decision
somebody makes rather than something a suite does.

Every command here is read-only with respect to the provider. The reader
can issue nothing but GET, so an ingest cannot comment on a pull request
or close an issue however it is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.table import Table

from lltpm.cli_support import (
    DEFAULT_ORGANIZATION,
    console,
    db_option,
    errors,
    horizon,
    logging_options,
    manifest,
    now as utc_now,
    open_persistence,
    resolve_db,
    seeded_projection,
    start_logging,
)

#: The payload kinds the GitHub normalizers accept for `normalize`.
PAYLOAD_KINDS = ("pull_request", "issue", "event", "review")


@click.group(name="integrations")
@logging_options
def integrations_cli(log_level: str, log_format: str) -> None:
    """Read source systems into evidence, and keep the reading honest.

    Everything here is read-only against the provider: the reader can
    issue nothing but GET.
    """
    start_logging(log_level, log_format)


# ── What can be read, and how well ───────────────────────────────────


@integrations_cli.command()
def sources() -> None:
    """Which source systems normalize, and how trustworthy each is.

    Reports the provenance of every contract rather than a pass/fail,
    because a contract derived from a hand-written payload is not
    evidence that the live API still agrees — and presenting it as
    though it were is the failure this command exists to prevent.
    """
    from lltpm.integrations.contracts import (
        available_contract_bundles,
        load_contract_bundle,
    )

    bundles = available_contract_bundles()
    if not bundles:
        console.print(
            "[yellow]no recorded contracts[/yellow] — nothing can be "
            "verified against a live API"
        )
        return

    table = Table(title="Source contracts")
    table.add_column("Source")
    table.add_column("Payload kind")
    table.add_column("Fields read", justify="right")
    table.add_column("Samples", justify="right")
    table.add_column("Backing")
    for name in bundles:
        bundle = load_contract_bundle(name)
        if bundle is None:
            continue
        for kind, contract in sorted(bundle.contracts.items()):
            provenance = bundle.provenance.get(kind, "unknown")
            backing = {
                "recorded": "[green]recorded[/green]",
                "derived": "[yellow]derived[/yellow]",
                "hand_written": "[red]hand-written[/red]",
            }.get(provenance, provenance)
            table.add_row(
                bundle.source_system, kind, str(len(contract.fields)),
                str(contract.recorded_from_samples), backing,
            )
    console.print(table)
    console.print(
        "\nOnly a [green]recorded[/green] contract is evidence about the "
        "live API. A [yellow]derived[/yellow] one was built from a "
        "recording of a related shape; a [red]hand-written[/red] one was "
        "never recorded at all, and `verify` reports it as unverifiable "
        "rather than passing."
    )


# ── Offline ──────────────────────────────────────────────────────────


def _bindings(repository: str, projects: tuple[str, ...], file: str | None):
    """Declared bindings from flags or a file, never inferred.

    A repository with no binding produces a reported unbound payload
    rather than evidence filed under a plausible project.
    """
    from lltpm.integrations.normalizers import GITHUB, ProjectBindings

    if file:
        mapping = json.loads(Path(file).read_text())
        return ProjectBindings.from_mapping(
            source_system=GITHUB,
            mapping={k: list(v) for k, v in mapping.items()},
        )
    if repository and projects:
        return ProjectBindings.from_mapping(
            source_system=GITHUB, mapping={repository: list(projects)},
        )
    return ProjectBindings()


def _report(outcome) -> None:
    console.print(
        f"[green]{len(outcome.envelopes)}[/green] envelope(s); "
        f"{outcome.skipped_count} payload(s) produced none"
    )
    if outcome.envelopes:
        table = Table(title="Evidence")
        table.add_column("Event class")
        table.add_column("Occurred (reality time)")
        table.add_column("Source")
        for envelope in outcome.envelopes:
            table.add_row(
                envelope.event_type,
                envelope.observed_at.isoformat(timespec="minutes"),
                envelope.source_id,
            )
        console.print(table)

    for skipped in outcome.unbound:
        errors.print(
            f"[yellow]unbound[/yellow] {skipped.payload_kind}/"
            f"{skipped.payload_id}: {skipped.reason}"
        )
    for skipped in outcome.rejected:
        errors.print(
            f"[red]rejected[/red] {skipped.payload_kind}/"
            f"{skipped.payload_id}: {skipped.reason}"
        )
    for skipped in outcome.uninterpreted:
        errors.print(
            f"[dim]uninterpreted {skipped.payload_kind}/"
            f"{skipped.payload_id}: {skipped.reason}[/dim]"
        )


@integrations_cli.command()
@click.argument("payload_file", type=click.Path(exists=True))
@click.option(
    "--kind", type=click.Choice(PAYLOAD_KINDS), default="pull_request",
    show_default=True, help="Which provider shape the file contains",
)
@click.option("--repository", default="", help="owner/name the payload is from")
@click.option(
    "--project", "projects", multiple=True,
    help="LLTPM project the repository is bound to; repeatable",
)
@click.option(
    # default=None, not "": click validates the default against
    # exists=True, and an empty path does not exist.
    "--bindings", "bindings_file", type=click.Path(exists=True), default=None,
    help="JSON mapping of owner/name to a list of project ids",
)
@click.option(
    "--output", "-o", type=click.Path(), default="",
    help="Write the envelopes here as JSON instead of only reporting",
)
def normalize(
    payload_file: str,
    kind: str,
    repository: str,
    projects: tuple[str, ...],
    bindings_file: str,
    output: str,
) -> None:
    """Turn a provider payload file into evidence. Offline.

    Touches no network and no database, so it is the safe way to see
    exactly what a payload would become before journalling anything.
    """
    from lltpm.integrations.normalizers import (
        NormalizationOutcome,
        normalize_github_event,
        normalize_github_issue,
        normalize_github_pull_request,
        normalize_github_review,
    )

    payload = json.loads(Path(payload_file).read_text())
    payloads = payload if isinstance(payload, list) else [payload]
    bindings = _bindings(repository, projects, bindings_file)
    received_at = utc_now()

    outcome = NormalizationOutcome()
    for item in payloads:
        if kind == "pull_request":
            step = normalize_github_pull_request(
                item, bindings=bindings, received_at=received_at,
                repository=repository,
            )
        elif kind == "issue":
            step = normalize_github_issue(
                item, bindings=bindings, received_at=received_at,
                repository=repository,
            )
        elif kind == "review":
            step = normalize_github_review(
                item, bindings=bindings, received_at=received_at,
                repository=repository,
            )
        else:
            step = normalize_github_event(
                item, bindings=bindings, received_at=received_at,
            )
        outcome = outcome.merge(step)

    _report(outcome)
    if output:
        Path(output).write_text(json.dumps(
            [e.model_dump(mode="json") for e in outcome.envelopes],
            indent=2, sort_keys=True, default=str,
        ))
        console.print(f"[green]wrote[/green] {output}")
    if outcome.needs_attention:
        raise SystemExit(1)


# ── Live ─────────────────────────────────────────────────────────────


def _reader(token: str):
    from lltpm.integrations.github_reader import UrllibGitHubReader

    return UrllibGitHubReader(token=token or None)


@integrations_cli.command()
@click.option("--repository", required=True, help="owner/name to read")
@click.option(
    "--project", "projects", multiple=True, required=True,
    help="LLTPM project the repository is bound to; repeatable",
)
@db_option
@click.option("--organization", default=DEFAULT_ORGANIZATION)
@click.option("--limit", default=30, show_default=True)
@click.option("--horizon-days", default=14, show_default=True)
@click.option(
    "--token", default="", envvar="LLTPM_GITHUB_TOKEN",
    help="GitHub token; also read from $LLTPM_GITHUB_TOKEN or $GITHUB_TOKEN",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Normalize and report, but journal nothing",
)
def ingest(
    repository: str,
    projects: tuple[str, ...],
    db: str,
    organization: str,
    limit: int,
    horizon_days: int,
    token: str,
    dry_run: bool,
) -> None:
    """Read a repository and journal what it says about the work.

    The end-to-end path: fetch, translate, append, recompute. Journal
    first, as everywhere else — the evidence is durable before anything
    derives from it, so a failed recompute leaves the evidence intact
    and a later `lltpm runtime replay` rebuilds the projection.
    """
    from lltpm.integrations.github_reader import fetch_github_payloads
    from lltpm.integrations.normalizers import normalize_github_payloads

    fetched = fetch_github_payloads(
        reader=_reader(token), repository=repository, limit=limit,
    )
    for kind, reason in sorted(fetched.failures.items()):
        errors.print(f"[yellow]could not read {kind}[/yellow]: {reason}")
    if not fetched:
        errors.print(
            "[red]nothing could be read[/red] — this is an outage, not a "
            "repository with no activity"
        )
        raise SystemExit(1)

    received_at = utc_now()
    outcome = normalize_github_payloads(
        events=fetched.get("events"),
        pull_requests=fetched.get("pull_requests"),
        issues=fetched.get("issues"),
        bindings=_bindings(repository, projects, ""),
        received_at=received_at,
        repository=repository,
    )
    _report(outcome)

    if dry_run:
        console.print("[dim]dry run — nothing was journalled[/dim]")
        return
    if not outcome.envelopes:
        console.print("[yellow]no evidence to journal[/yellow]")
        return

    from lltpm.runtime import (
        process_durable_organizational_events,
        registry_from_journal,
    )
    from lltpm.runtime.baselines import baseline_providers
    from lltpm.runtime.services import LiveIntelligenceServices
    from lltpm.runtime.sources import JournalBackedDataSource

    persistence = open_persistence(resolve_db(db))
    try:
        services = LiveIntelligenceServices(
            data_source=JournalBackedDataSource(
                journal=persistence.journal, organization_id=organization,
            ),
            as_of=received_at,
            scenario_horizon=horizon(received_at, horizon_days),
            **baseline_providers(),
        )
        durable, _ = process_durable_organizational_events(
            envelopes=outcome.envelopes,
            organization_id=organization,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=seeded_projection(persistence, organization),
            intelligence_services=services,
            manifest=manifest(),
            now=received_at,
            identity_registry=registry_from_journal(
                journal=persistence.journal, organization_id=organization,
            ),
        )
        console.print(
            f"Journalled {durable.evidence_records_appended} evidence "
            f"record(s)."
        )
        if durable.evidence_records_deduplicated:
            console.print(
                f"  {durable.evidence_records_deduplicated} were already "
                f"durable (same fact, read again)."
            )
        result = durable.result
        if result.complete and durable.revision_promoted:
            console.print(
                f"[green]published[/green] revision {result.revision_id} "
                f"across {len(result.projects_affected)} project(s)"
            )
        elif result.complete:
            why = ", ".join(r.value for r in result.no_action_reasons)
            console.print(
                f"[green]accepted[/green] — no revision was needed "
                f"({why or 'nothing changed'})"
            )
        for failure in result.failures:
            errors.print(
                f"  [red]{failure.artifact_type.value}[/red] "
                f"{failure.explanation}"
            )
    finally:
        persistence.close()


@integrations_cli.command()
@click.option("--repository", required=True, help="owner/name to check")
@click.option("--source", default="github", show_default=True)
@click.option("--limit", default=30, show_default=True)
@click.option(
    "--token", default="", envvar="LLTPM_GITHUB_TOKEN",
    help="GitHub token; also read from $LLTPM_GITHUB_TOKEN or $GITHUB_TOKEN",
)
def verify(repository: str, source: str, limit: int, token: str) -> None:
    """Check the recorded contracts still match the live API.

    The occasional suite in Fowler's self-initializing fake: the offline
    tests replay recordings, and this is what catches the recordings
    having gone stale. It is read-only and changes nothing.

    A contract that was never recorded is reported as unverifiable
    rather than passing, because a hand-written payload agreeing with
    itself says nothing about GitHub.
    """
    from lltpm.integrations.contracts import check_contract, load_contract_bundle
    from lltpm.integrations.github_reader import fetch_github_payloads

    bundle = load_contract_bundle(source)
    if bundle is None:
        errors.print(f"[red]no recorded contracts for '{source}'[/red]")
        raise SystemExit(2)

    fetched = fetch_github_payloads(
        reader=_reader(token), repository=repository, limit=limit,
    )
    for kind, reason in sorted(fetched.failures.items()):
        errors.print(f"[yellow]could not read {kind}[/yellow]: {reason}")

    # The fixture file names map to payload kinds; the fetch is keyed by
    # endpoint, so translate once here rather than guessing per kind.
    kind_for_endpoint = {
        "pull_requests": "pull_request",
        "issues": "issue",
        "events": "event",
    }

    table = Table(title=f"Contract check — {source} / {repository}")
    table.add_column("Payload kind")
    table.add_column("Checked", justify="right")
    table.add_column("Backing")
    table.add_column("Result")

    breached = False
    checked_any = False
    for endpoint, payloads in sorted(fetched.items()):
        kind = kind_for_endpoint.get(endpoint)
        contract = bundle.contracts.get(kind or "")
        if contract is None:
            continue
        if not bundle.is_evidence(kind or ""):
            table.add_row(
                kind or endpoint, str(len(payloads)),
                f"[yellow]{bundle.provenance.get(kind, 'unknown')}[/yellow]",
                "[yellow]not verifiable[/yellow]",
            )
            continue

        checked_any = True
        breaches = [
            breach
            for payload in payloads
            for breach in check_contract(payload=payload, contract=contract)
        ]
        unique = {b.path: b for b in breaches}
        table.add_row(
            kind, str(len(payloads)), "[green]recorded[/green]",
            "[green]matches[/green]" if not unique
            else f"[red]{len(unique)} breach(es)[/red]",
        )
        for breach in unique.values():
            errors.print(f"  [red]breach[/red] {breach.explanation}")
            breached = True

    console.print(table)
    for kind in bundle.not_live_verifiable_kinds:
        errors.print(
            f"[yellow]note[/yellow] the '{kind}' contract is "
            f"{bundle.provenance.get(kind)}, so nothing here verifies it"
        )
    if breached:
        errors.print(
            "\n[red]the live API no longer provides something a "
            "normalizer reads[/red] — the recordings are stale, or the "
            "provider changed"
        )
        raise SystemExit(1)
    if not checked_any:
        errors.print(
            "[yellow]nothing was verified[/yellow] — no endpoint answered "
            "with a kind that has a recorded contract"
        )
        raise SystemExit(1)
    console.print("[green]ok[/green] every recorded contract still holds")


@integrations_cli.command()
@click.option("--repository", required=True, help="owner/name to record from")
@click.option(
    "--out", "out_dir", required=True, type=click.Path(),
    help="Directory to write the scrubbed fixtures into",
)
@click.option(
    "--alias", default="acme/atlas-service", show_default=True,
    help="Repository alias the recordings are scrubbed to",
)
@click.option("--limit", default=30, show_default=True)
@click.option(
    "--token", default="", envvar="LLTPM_GITHUB_TOKEN",
    help="GitHub token; also read from $LLTPM_GITHUB_TOKEN or $GITHUB_TOKEN",
)
def record(
    repository: str, out_dir: str, alias: str, limit: int, token: str,
) -> None:
    """Re-record the fixtures, scrubbed, and regenerate the contracts.

    The recording half of the fake. Values are pseudonymized before
    anything is written: handles, account ids and the repository name are
    replaced, avatars and emails dropped, opaque ids rewritten
    deterministically. The shape — which is what a contract test is
    about — is preserved exactly.

    Writes only the files it recorded. Fixtures that were derived or
    hand-written are left alone, because re-deriving them is a decision
    about what they are for, not a recording.
    """
    from lltpm.integrations.contracts import (
        GITHUB_CONTRACT_PATHS,
        ContractSource,
        build_contract_bundle,
        collect_github_logins,
        contract_bundle_path,
        scrub_github_payload,
        serialize_contract_bundle,
        unscrubbed_paths,
    )
    from lltpm.integrations.github_reader import fetch_github_payloads

    fetched = fetch_github_payloads(
        reader=_reader(token), repository=repository, limit=limit,
    )
    for kind, reason in sorted(fetched.failures.items()):
        errors.print(f"[yellow]could not read {kind}[/yellow]: {reason}")
    if not fetched:
        errors.print("[red]nothing was recorded[/red]")
        raise SystemExit(1)

    forbidden = {repository, *repository.split("/")}
    for payloads in fetched.values():
        forbidden |= collect_github_logins(payloads)

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    kind_for_endpoint = {
        "pull_requests": "pull_request",
        "issues": "issue",
        "events": "event",
    }
    sources = []
    for endpoint, payloads in sorted(fetched.items()):
        scrubbed = scrub_github_payload(
            payloads, repository_alias=alias, real_repository=repository,
        )
        leaks = unscrubbed_paths(scrubbed, forbidden=forbidden)
        if leaks:
            # Refused rather than written: a fixture that still names
            # someone is worse than no fixture, and it would be
            # committed.
            errors.print(
                f"[red]refusing to write {endpoint}[/red] — "
                f"{len(leaks)} value(s) still disclose something, first: "
                f"{leaks[0]}"
            )
            raise SystemExit(1)
        path = directory / f"{endpoint}.json"
        path.write_text(
            json.dumps(scrubbed, indent=1, sort_keys=True) + "\n"
        )
        console.print(
            f"[green]recorded[/green] {path} ({len(scrubbed)} payload(s))"
        )
        sources.append(ContractSource(
            payload_kind=kind_for_endpoint[endpoint],
            payloads=scrubbed, provenance="recorded",
            recorded_at=utc_now().isoformat(timespec="seconds") + "Z",
        ))

    bundle = build_contract_bundle(
        source_system="github", sources=sources,
        paths_by_kind=GITHUB_CONTRACT_PATHS,
    )
    bundle_path = contract_bundle_path("github")
    bundle_path.write_text(serialize_contract_bundle(bundle))
    console.print(
        f"[green]contracts[/green] {bundle_path} — recorded: "
        f"{', '.join(bundle.verifiable_kinds) or 'none'}"
    )
    errors.print(
        "[yellow]note[/yellow] derived and hand-written fixtures were not "
        "touched; re-derive them deliberately and update the index"
    )
