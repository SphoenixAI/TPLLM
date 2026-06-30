"""Append-only application ledger — the audit trail of control actions.

Every application attempt (applied or blocked) and every reversal is
recorded as an immutable entry. The ledger is the input the next human
re-review reads to decide whether the rule keeps its control authority. It
is append-only by construction: the append helpers return a new ledger and
never edit an existing entry.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.application.control import ApplicationResult, ApplicationDecision
from lltpm.application.monitor import RevertRecord


# ── Enums ──────────────────────────────────────────────────────────────

class LedgerEntryType(str, Enum):
    APPLIED = "applied"
    BLOCKED = "blocked"
    REVERTED = "reverted"


# ── Models ─────────────────────────────────────────────────────────────

class ApplicationLedgerEntry(BaseModel):
    """One immutable audit record."""
    entry_type: LedgerEntryType = LedgerEntryType.BLOCKED
    proposal_id: str = ""
    active_calibration_id: str | None = None
    at: datetime = Field(default_factory=datetime.utcnow)
    detail: str = ""


class ApplicationLedger(BaseModel):
    """Append-only collection of ledger entries."""
    entries: list[ApplicationLedgerEntry] = Field(default_factory=list)


# ── Append helpers (immutable) ────────────────────────────────────────

def append_application(
    *,
    ledger: ApplicationLedger,
    result: ApplicationResult,
    now: datetime | None = None,
) -> ApplicationLedger:
    """Append an application attempt. Returns a new ledger."""
    stamp = now
    if stamp is None and result.journal_entry is not None:
        stamp = result.journal_entry.at
    if stamp is None:
        stamp = datetime.utcnow()
    if result.decision == ApplicationDecision.APPLIED:
        entry = ApplicationLedgerEntry(
            entry_type=LedgerEntryType.APPLIED,
            proposal_id=result.proposal_id,
            active_calibration_id=(
                result.active_record.id if result.active_record else None
            ),
            at=stamp,
            detail=result.reason,
        )
    else:
        entry = ApplicationLedgerEntry(
            entry_type=LedgerEntryType.BLOCKED,
            proposal_id=result.proposal_id,
            at=stamp,
            detail=(
                f"{result.block_reason.value if result.block_reason else 'blocked'}"
                f": {result.reason}"
            ),
        )
    return ledger.model_copy(update={"entries": [*ledger.entries, entry]})


def append_revert(
    *,
    ledger: ApplicationLedger,
    revert_record: RevertRecord,
) -> ApplicationLedger:
    """Append a reversal. Returns a new ledger."""
    entry = ApplicationLedgerEntry(
        entry_type=LedgerEntryType.REVERTED,
        proposal_id=revert_record.proposal_id,
        active_calibration_id=revert_record.active_calibration_id,
        at=revert_record.reverted_at,
        detail=f"{revert_record.trigger.value}: {revert_record.note}",
    )
    return ledger.model_copy(update={"entries": [*ledger.entries, entry]})


# ── Audit assembly ────────────────────────────────────────────────────

def build_application_audit(
    *,
    proposal_id: str,
    ledger: ApplicationLedger,
) -> dict:
    """Summarize the control history of one proposal for re-review."""
    relevant = [e for e in ledger.entries if e.proposal_id == proposal_id]
    applied = [e for e in relevant if e.entry_type == LedgerEntryType.APPLIED]
    blocked = [e for e in relevant if e.entry_type == LedgerEntryType.BLOCKED]
    reverted = [e for e in relevant if e.entry_type == LedgerEntryType.REVERTED]

    return {
        "proposal_id": proposal_id,
        "applied_count": len(applied),
        "blocked_count": len(blocked),
        "reverted_count": len(reverted),
        "total_entries": len(relevant),
        "applied_entries": [e.model_dump() for e in applied],
        "blocked_entries": [e.model_dump() for e in blocked],
        "reverted_entries": [e.model_dump() for e in reverted],
    }
