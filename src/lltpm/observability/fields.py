"""The structured-field vocabulary, and the names it may not use.

Two problems this module exists to prevent.

First, **attribute collisions**. ``logger.info(msg, extra={"module": x})``
raises ``KeyError`` inside the logging machinery, because ``module`` is
already a ``LogRecord`` attribute. The failure happens at the call site,
at runtime, in whatever code path happened to log — so the reserved set
is published here and asserted by a test rather than discovered in
production.

Second, **vocabulary drift**. Structured logs are only worth having if
the same thing is called the same thing everywhere; ``revision``,
``rev_id`` and ``revision_id`` in three packages make the fields
unqueryable. :data:`STRUCTURED_FIELD_NAMES` is the agreed list, and a
guardrail test walks every log call in ``src/lltpm`` to check it.

Note what the vocabulary excludes: there is no field for evidence
content, message text, or anything describing what a person did. LLTPM
models organizational mechanics — capacity, dependencies, authority,
information flow — and never individual activity. An observability layer
is the easiest place for that line to erode by accident, so here the
line is a list.
"""

from __future__ import annotations

import logging
from typing import Any

#: Every attribute the logging machinery sets on a record itself.
#: Derived from a real record rather than transcribed, so a future
#: interpreter that adds one is accounted for automatically.
RESERVED_RECORD_ATTRIBUTES: frozenset[str] = frozenset(
    set(vars(logging.LogRecord("", 0, "", 0, "", (), None)))
    | {"message", "asctime", "taskName"}
)

#: The agreed field vocabulary. Correlation ids first — these are what
#: join a log line back to durable history — then counts and outcomes.
STRUCTURED_FIELD_NAMES: frozenset[str] = frozenset({
    # Correlation: the join keys between a log line and the journal.
    "organization_id",
    "project_id",
    "revision_id",
    "parent_revision_id",
    "causal_event_id",
    "journal_sequence",
    "journal_stream",
    "journal_event_type",
    "record_id",
    "idempotency_key",
    "evidence_id",
    "snapshot_id",
    "artifact_type",
    "execution_mode",
    "source_system",
    "external_scope",
    "payload_kind",
    # Magnitudes: how much of something, never who did it.
    "artifact_count",
    "evidence_count",
    "events_deduplicated",
    "events_processed",
    "events_received",
    "failure_count",
    "identity_ambiguous_count",
    "identity_linked_count",
    "mismatch_count",
    "project_count",
    "open_read_routes",
    "records_appended",
    "records_replayed",
    "rejected_count",
    "skipped_count",
    "unbound_count",
    "side_effects_suppressed",
    "start_sequence",
    "end_sequence",
    "duration_ms",
    # Outcomes and explanations.
    "complete",
    "promoted",
    "published",
    "no_action_reason",
    "outcome",
    "reason",
    "mode",
})


def extra_fields(**fields: Any) -> dict[str, Any]:
    """Build an ``extra`` mapping, refusing a reserved attribute name.

    Call sites inside ``lltpm`` pass plain dicts — they import nothing
    from this package by design. This helper is for application code and
    for tests, where failing loudly on a bad name beats a ``KeyError``
    raised from inside the formatter.
    """
    collisions = sorted(set(fields) & RESERVED_RECORD_ATTRIBUTES)
    if collisions:
        raise ValueError(
            f"Field name(s) {collisions} are LogRecord attributes; logging "
            f"would raise at the call site. Rename them."
        )
    return dict(fields)


def record_fields(record: logging.LogRecord) -> dict[str, Any]:
    """The structured fields a call site attached to ``record``.

    Anything the logging machinery did not put there itself, which is
    precisely what ``extra={...}`` adds.
    """
    return {
        name: value for name, value in vars(record).items()
        if name not in RESERVED_RECORD_ATTRIBUTES
    }
