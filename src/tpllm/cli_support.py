"""Shared plumbing for the command groups.

Two command groups now open the same database, stamp the same
reproducibility manifest and configure the same logging. Duplicating any
of that would mean the container-durability fix below holds for one
command group and not the other — which is exactly the class of bug that
gets found in production rather than in review.

Deliberately not a package: it imports click and the logging layer, and
nothing in the layered stack imports it. It is application wiring, and
the architecture tests see it as such.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

import click
from rich.console import Console

from tpllm.observability import (
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_LEVEL,
    LOG_FORMAT_CHOICES,
    LOG_LEVEL_CHOICES,
    configure_logging,
)

#: Reports go to stdout so they can be redirected; notes and warnings go
#: to stderr so `... export > file.json` still produces clean JSON while
#: the operator keeps seeing the caveats.
console = Console()
errors = Console(stderr=True)

DEFAULT_DB = ""  # resolved against TPLLM_DATA_DIR; see resolve_db
DEFAULT_ORGANIZATION = "default"
DB_FILENAME = "tpllm.db"

F = TypeVar("F", bound=Callable[..., Any])


def logging_options(group: F) -> F:
    """Add ``--log-level`` and ``--log-format`` to a command group.

    The group callback receives them and configures logging once. Every
    module below emits through the standard library and configures
    nothing, which is what lets TPLLM be embedded without hijacking its
    host's logging.
    """
    group = click.option(
        "--log-format",
        type=click.Choice(LOG_FORMAT_CHOICES),
        default=DEFAULT_LOG_FORMAT,
        show_default=True,
        help="'json' emits one JSON object per line for a log pipeline",
    )(group)
    return click.option(
        "--log-level",
        type=click.Choice(LOG_LEVEL_CHOICES),
        default=DEFAULT_LOG_LEVEL,
        show_default=True,
        help="Operational logging verbosity; logs go to stderr",
    )(group)


def start_logging(log_level: str, log_format: str) -> None:
    configure_logging(level=log_level, log_format=log_format)


def resolve_db(path: str) -> str:
    """Where the journal actually lives.

    An explicit --db wins. Otherwise the database goes in the configured
    data directory, which is what makes the container work: the image
    sets TPLLM_DATA_DIR=/data and mounts a volume there, while the
    working directory is /app. A bare relative default would put the
    journal at /app/tpllm.db — off the volume, and destroyed on restart.
    Durability that a default deployment throws away is not durability.
    """
    if path:
        return path

    data_dir = os.getenv("TPLLM_DATA_DIR")
    if data_dir:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory / DB_FILENAME)
    return DB_FILENAME


def db_option(command: F) -> F:
    return click.option(
        "--db", default=DEFAULT_DB,
        help="SQLite path; defaults to $TPLLM_DATA_DIR/tpllm.db",
    )(command)


def open_persistence(path: str):
    from tpllm.persistence import SQLitePersistenceConfig
    from tpllm.persistence.sqlite import initialize_sqlite_persistence

    return initialize_sqlite_persistence(
        SQLitePersistenceConfig(path=path)
    )


def manifest(policy_fingerprint: str = "cli-default"):
    from tpllm.persistence import build_manifest
    from tpllm.runtime.baselines import (
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


def now() -> datetime:
    """Naive UTC, matching the clock the whole stack uses."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def horizon(moment: datetime, days: int):
    """A forecast needs a stated window; this is the CLI's default one.

    Explicit rather than implicit: the horizon bounds what the lattice
    is allowed to claim, so it is a parameter rather than a constant
    buried in the engine.
    """
    from tpllm.scenarios.models import ScenarioHorizon

    return ScenarioHorizon(
        start_at=moment,
        end_at=moment + timedelta(days=days),
        maximum_depth=4,
        minimum_branch_probability=0.02,
    )


def seeded_projection(persistence, organization: str):
    """A projection whose revision counter continues from durable state.

    Each CLI invocation builds a fresh in-memory projection, so without
    this every ingest would allocate "revision-1" again and the second
    one would dedupe against the first's audit marker — making two
    genuinely different recomputations look like one.
    """
    from tpllm.runtime import RuntimeStateStore as RuntimeProjection

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


def known_projects(persistence) -> list[str]:
    from tpllm.runtime.sources import JournalBackedDataSource

    return JournalBackedDataSource(
        journal=persistence.journal,
    ).project_ids()
