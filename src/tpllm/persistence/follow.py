"""Tailing the journal as records land.

A durable history that can only be read after the fact is hard to trust
during an incident — the question "is anything reaching the journal
right now?" has no answer. This gives it one, and gives it cheaply:
polling ``read_from`` on a strictly increasing global sequence.

Polling rather than notifications, deliberately. The reference backend
is one SQLite file with no broker and no daemon, and a reader on its own
connection sees committed appends on its next query. A notification
mechanism would need a process to run, which is the thing this project
has repeatedly chosen not to require.

Read-only by construction: the only journal method called here is
``read_from``. Following history must never be able to alter it.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from tpllm.persistence.models import JournalRecord

#: How many records one poll may pull back. A follower starting from
#: sequence zero on a long history should stream it rather than
#: materialize all of it to print the tail.
DEFAULT_BATCH_SIZE = 500

DEFAULT_POLL_SECONDS = 1.0


def follow_journal(
    *,
    journal: Any,
    on_record: Callable[[JournalRecord], None],
    after_sequence: int = 0,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    idle_timeout_seconds: float | None = None,
    max_records: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Deliver records to ``on_record`` as they appear; return the last
    global sequence delivered.

    Stops when ``max_records`` have been delivered, or when
    ``idle_timeout_seconds`` passes with nothing new. With neither set it
    follows indefinitely, which is what a terminal wants — the caller
    ends it with Ctrl-C.

    ``sleep`` and ``monotonic`` are injectable so the loop can be tested
    for real instead of only in a shape that happens to terminate.
    """
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive.")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive.")

    cursor = after_sequence
    delivered = 0
    last_change = monotonic()

    while True:
        batch = [
            record for record in journal.read_from(
                global_sequence=cursor + 1, limit=batch_size,
            )
            if record.position.global_sequence > cursor
        ]
        batch.sort(key=lambda record: record.position.global_sequence)

        for record in batch:
            on_record(record)
            cursor = record.position.global_sequence
            delivered += 1
            if max_records is not None and delivered >= max_records:
                return cursor

        if batch:
            last_change = monotonic()
            # A full batch probably means more is already waiting, so
            # read again before sleeping. Catching up on a long history
            # should not take one poll interval per batch.
            if len(batch) >= batch_size:
                continue

        # Checked before sleeping, not after: with a zero timeout the
        # caller means "drain what is there and stop", and a sleep would
        # make that cost a poll interval it did not ask for.
        if (
            idle_timeout_seconds is not None
            and monotonic() - last_change >= idle_timeout_seconds
        ):
            return cursor

        sleep(poll_seconds)
