"""The ``tpllm integrations`` command group.

The normalizers translate provider payloads into evidence and are
proven against recorded responses. These commands are the surface that
reaches them, and they are deliberately four
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

from tpllm.cli_support import (
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
    """What each source system can do, and how trustworthy it is.

    Two tables, because "is Slack supported?" has no single answer.
    Capabilities say what the integration can do; contract provenance
    says how much the output should be trusted. A bare `supported=True`
    would hide the difference between a provider whose shapes were
    recorded from its live API and one whose shapes were read out of its
    documentation.
    """
    from tpllm.integrations.contracts import load_contract_bundle
    from tpllm.integrations.providers import (
        ProviderCapability, describe_providers,
    )

    providers = describe_providers()
    if not providers:
        console.print("[red]no providers are registered[/red]")
        return

    capability_order = (
        ProviderCapability.NORMALIZATION,
        ProviderCapability.LIVE_READ,
        ProviderCapability.WEBHOOK_NORMALIZATION,
        ProviderCapability.WEBHOOK_SIGNATURE_VERIFICATION,
        ProviderCapability.RATE_LIMIT_AWARE,
        ProviderCapability.CONTRACT_VERIFICATION,
    )
    capabilities = Table(title="Provider capabilities")
    capabilities.add_column("Source")
    capabilities.add_column("Scope")
    for capability in capability_order:
        capabilities.add_column(
            capability.value.replace("_", "\n"), justify="center",
        )
    for descriptor in providers:
        capabilities.add_row(
            descriptor.source_system,
            descriptor.scope_label,
            *[
                "[green]yes[/green]" if descriptor.has(c) else "[dim]—[/dim]"
                for c in capability_order
            ],
        )
    console.print(capabilities)

    contracts = Table(title="Contract provenance")
    contracts.add_column("Source")
    contracts.add_column("Payload kind")
    contracts.add_column("Fields read", justify="right")
    contracts.add_column("Samples", justify="right")
    contracts.add_column("Backing")
    backing_label = {
        "recorded": "[green]recorded (live)[/green]",
        "official_documentation": "[cyan]official docs[/cyan]",
        "derived": "[yellow]derived[/yellow]",
        "hand_written": "[red]hand-written[/red]",
    }
    for descriptor in providers:
        bundle = load_contract_bundle(descriptor.source_system)
        if bundle is None:
            contracts.add_row(
                descriptor.source_system, "[dim]none[/dim]", "-", "-",
                "[red]no contract[/red]",
            )
            continue
        for kind, contract in sorted(bundle.contracts.items()):
            provenance = bundle.provenance.get(kind, "unknown")
            contracts.add_row(
                descriptor.source_system, kind,
                str(len(contract.fields)),
                str(contract.recorded_from_samples),
                backing_label.get(provenance, provenance),
            )
    console.print(contracts)

    console.print(
        "\nOnly a [green]recorded[/green] contract is evidence that the "
        "live API still matches. [cyan]Official docs[/cyan] means the "
        "shape came from the provider's own published example — worth "
        "more than nothing, and less than a recording, because "
        "documentation drifts from implementations. `verify` reports "
        "anything that is not recorded as unverifiable rather than "
        "passing."
    )
    for descriptor in providers:
        if descriptor.notes:
            console.print(f"\n[dim]{descriptor.source_system}: "
                          f"{descriptor.notes}[/dim]")


# ── Offline ──────────────────────────────────────────────────────────


def _bindings(
    *,
    source_system: str,
    scope: str,
    projects: tuple[str, ...],
    file: str | None,
):
    """Declared bindings from flags or a file, never inferred.

    Provider-agnostic: the scope is whatever the provider calls the
    container a payload lives in — a repository, a workspace/channel
    pair, a site and project key. An unbound scope produces a reported
    unbound payload rather than evidence filed under a plausible
    project, for every provider equally.
    """
    from tpllm.integrations.normalizers import ProjectBindings

    if file:
        mapping = json.loads(Path(file).read_text())
        return ProjectBindings.from_mapping(
            source_system=source_system,
            mapping={k: list(v) for k, v in mapping.items()},
        )
    if scope and projects:
        return ProjectBindings.from_mapping(
            source_system=source_system, mapping={scope: list(projects)},
        )
    return ProjectBindings()


def _provider_or_exit(name: str):
    from tpllm.integrations.providers import provider, provider_names

    descriptor = provider(name)
    if descriptor is None:
        errors.print(
            f"[red]unknown provider '{name}'[/red] — registered: "
            f"{', '.join(provider_names())}"
        )
        raise SystemExit(2)
    return descriptor


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
    "--provider", default="github", show_default=True,
    help="Source system the payload came from",
)
@click.option(
    "--kind", default="", show_default=True,
    help=(
        "Which provider shape the file contains; see "
        "`tpllm integrations sources` for each provider's kinds"
    ),
)
@click.option(
    "--scope", default="",
    help=(
        "Provider scope to bind: owner/name, workspace/channel, or "
        "site/project key. Run `tpllm integrations sources` for each "
        "provider's format"
    ),
)
@click.option(
    "--repository", default="",
    help="Deprecated alias for --scope, kept for GitHub callers",
)
@click.option(
    "--project", "projects", multiple=True,
    help="TPLLM project the scope is bound to; repeatable",
)
@click.option(
    # default=None, not "": click validates the default against
    # exists=True, and an empty path does not exist.
    "--bindings", "bindings_file", type=click.Path(exists=True), default=None,
    help="JSON mapping of provider scope to a list of project ids",
)
@click.option(
    "--output", "-o", type=click.Path(), default="",
    help="Write the envelopes here as JSON instead of only reporting",
)
def normalize(
    payload_file: str,
    provider: str,
    kind: str,
    scope: str,
    repository: str,
    projects: tuple[str, ...],
    bindings_file: str,
    output: str,
) -> None:
    """Turn a provider payload file into evidence. Offline.

    Touches no network and no database, so it is the safe way to see
    exactly what a payload would become before journalling anything.
    Works for every registered provider through the one uniform entry
    point, rather than a branch per provider.
    """
    descriptor = _provider_or_exit(provider)
    scope = scope or repository
    payload_kind = kind or descriptor.payload_kinds[0]
    if payload_kind not in descriptor.payload_kinds:
        errors.print(
            f"[red]'{payload_kind}' is not a {provider} payload kind[/red] "
            f"— known: {', '.join(descriptor.payload_kinds)}"
        )
        raise SystemExit(2)

    from tpllm.integrations.normalizers import NormalizationOutcome

    normalizer = descriptor.normalizer()
    payload = json.loads(Path(payload_file).read_text())
    payloads = payload if isinstance(payload, list) else [payload]
    bindings = _bindings(
        source_system=descriptor.source_system, scope=scope,
        projects=projects, file=bindings_file,
    )
    received_at = utc_now()

    outcome = NormalizationOutcome()
    for item in payloads:
        outcome = outcome.merge(normalizer(
            payload_kind=payload_kind, payload=item, bindings=bindings,
            received_at=received_at,
            scope=descriptor.normalizer_scope(scope),
        ))

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
    from tpllm.integrations.github_reader import UrllibGitHubReader

    return UrllibGitHubReader(token=token or None)


@integrations_cli.command()
@click.option("--repository", required=True, help="owner/name to read")
@click.option(
    "--project", "projects", multiple=True, required=True,
    help="TPLLM project the repository is bound to; repeatable",
)
@db_option
@click.option("--organization", default=DEFAULT_ORGANIZATION)
@click.option("--limit", default=30, show_default=True)
@click.option("--horizon-days", default=14, show_default=True)
@click.option(
    "--token", default="", envvar="TPLLM_GITHUB_TOKEN",
    help="GitHub token; also read from $TPLLM_GITHUB_TOKEN or $GITHUB_TOKEN",
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
    and a later `tpllm runtime replay` rebuilds the projection.
    """
    from tpllm.integrations.github_reader import fetch_github_payloads
    from tpllm.integrations.normalizers import normalize_github_payloads

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
        bindings=_bindings(
            source_system="github", scope=repository,
            projects=projects, file=None,
        ),
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

    from tpllm.runtime import (
        process_durable_organizational_events,
        registry_from_journal,
    )
    from tpllm.runtime.baselines import baseline_providers
    from tpllm.runtime.services import LiveIntelligenceServices
    from tpllm.runtime.sources import JournalBackedDataSource

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
    "--token", default="", envvar="TPLLM_GITHUB_TOKEN",
    help="GitHub token; also read from $TPLLM_GITHUB_TOKEN or $GITHUB_TOKEN",
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
    from tpllm.integrations.contracts import check_contract, load_contract_bundle
    from tpllm.integrations.github_reader import fetch_github_payloads

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
    "--token", default="", envvar="TPLLM_GITHUB_TOKEN",
    help="GitHub token; also read from $TPLLM_GITHUB_TOKEN or $GITHUB_TOKEN",
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
    from tpllm.integrations.contracts import (
        GITHUB_CONTRACT_PATHS,
        ContractSource,
        build_contract_bundle,
        collect_github_logins,
        contract_bundle_path,
        scrub_github_payload,
        serialize_contract_bundle,
        unscrubbed_paths,
    )
    from tpllm.integrations.github_reader import fetch_github_payloads

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
