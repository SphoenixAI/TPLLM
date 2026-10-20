"""Deterministic replay and recovery (Commit 31).

The acceptance test for this whole commit is one sentence: delete every
derived artifact, replay the journal, and get semantically equivalent
organizational intelligence back. This module is what makes that true.

Three properties matter more than speed.

**Replay cannot touch the world.** Rebuilding history must never re-send
a message, re-file a ticket, or re-finalize a decision that was already
made once. ``ExecutionMode.REPLAY`` is a fence enforced at the boundary,
and suppressed effects are counted rather than silently dropped —
"nothing happened" and "eleven things were prevented" are different
reports.

**Replay cannot invent.** A broken hash chain stops reconstruction and
names the position. A truncated journal is reported as incomplete
history, not padded. A snapshot that fails verification is discarded in
favour of an older valid one, or the journal from the beginning.

**Replay cannot pretend.** Reproducing what TPLLM concluded in August
and re-evaluating August's evidence under today's engine are different
questions with different answers. The manifest decides which one is
being asked, and ``REEVALUATE_WITH_CURRENT`` may never overwrite
historical agent output.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from lltpm.persistence.integrity import verify_journal_hash_chain
from lltpm.persistence.journal import inspect_journal_range
from lltpm.persistence.models import (
    DurableOutboundIntent,
    ExecutionContext,
    ExecutionMode,
    JournalEventType,
    JournalRecord,
    OutboundIntentStatus,
    PersistenceHealthReport,
    PortfolioRuntimeStateRecord,
    ProjectRuntimeStateRecord,
    ReplayClock,
    ReplayEquivalenceReport,
    ReplayMode,
    ReplayRequest,
    ReplayResult,
    ReproducibilityManifest,
    RuntimeRevisionRecord,
)
from lltpm.persistence.protocols import (
    RevisionPromotionError,
    UnsupportedJournalEventError,
)
from lltpm.persistence.snapshots import snapshot_is_usable
from lltpm.persistence.versioning import (
    assert_supported_event_type,
    assert_supported_journal_schema,
    manifest_incompatibilities,
)

logger = logging.getLogger(__name__)


class SideEffectFence:
    """Refuses outbound calls during replay, and counts what it stopped.

    Wraps whatever outbound port a caller supplies, so the guarantee
    lives at the boundary rather than depending on every call site
    remembering to check the mode.
    """

    def __init__(self, *, inner: Any = None, context: ExecutionContext):
        self._inner = inner
        self._context = context
        self.suppressed = 0
        self.suppressed_actions: list[str] = []
        self.delivered = 0

    def send(
        self,
        *,
        target_system: str,
        action_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        context: ExecutionContext | None = None,
    ) -> DurableOutboundIntent:
        active = context or self._context
        if active.is_replay or active.suppress_side_effects:
            self.suppressed += 1
            self.suppressed_actions.append(f"{target_system}:{action_type}")
            return DurableOutboundIntent(
                id=f"intent-suppressed-{self.suppressed}",
                originating_journal_record_id="",
                target_system=target_system,
                action_type=action_type,
                payload_hash="",
                status=OutboundIntentStatus.SUPPRESSED,
                idempotency_key=idempotency_key,
                suppressed_reason=(
                    "Replay reconstructs history; it does not repeat its "
                    "external consequences."
                ),
            )
        if self._inner is None:
            raise RuntimeError(
                "No outbound port configured for live execution."
            )
        self.delivered += 1
        return self._inner.send(
            target_system=target_system,
            action_type=action_type,
            payload=payload,
            idempotency_key=idempotency_key,
            context=active,
        )


# Records that describe something the agent already did to the world.
# Replay restores them as historical facts and never re-executes them.
_HISTORICAL_ONLY_EVENTS = frozenset({
    JournalEventType.AUTONOMOUS_DECISION_FINALIZED,
    JournalEventType.AGENT_RECOMMENDATION_PUBLISHED,
    JournalEventType.HUMAN_RECOMMENDATION_VALIDATED,
    JournalEventType.HUMAN_RECOMMENDATION_MODIFIED,
    JournalEventType.HUMAN_RECOMMENDATION_REJECTED,
})


def replay_journal(
    *,
    request: ReplayRequest,
    journal: Any,
    snapshots: Any = None,
    state_store: Any = None,
    artifact_store: Any = None,
    intelligence_services: Any = None,
    reconstructor: Any = None,
    manifest: ReproducibilityManifest | None = None,
    outbound: Any = None,
) -> ReplayResult:
    """Rebuild organizational intelligence from durable history.

    Validates the request, finds a usable checkpoint, verifies the hash
    chain, replays the remaining records behind a side-effect fence,
    compares fingerprints, and reports. Publishing is opt-in: by default
    a replay observes and says what it found.
    """
    expected_manifest = request.expected_manifest or manifest
    result = ReplayResult(
        organization_id=request.organization_id,
        mode=request.mode,
        start_sequence=request.from_global_sequence or 1,
    )

    # ── Checkpoint selection ─────────────────────────────────────────
    snapshot = None
    baseline: dict[str, Any] | None = None
    start_sequence = request.from_global_sequence or 1

    if request.use_snapshot and snapshots is not None:
        for candidate in snapshots.list_snapshots(request.organization_id):
            if (
                request.from_global_sequence is not None
                and candidate.journal_global_sequence
                >= request.from_global_sequence
            ):
                continue
            usable, reason = snapshot_is_usable(
                snapshot=candidate, manifest=expected_manifest,
            )
            if usable:
                snapshot = candidate
                break
            result.warnings.append(reason)

    if snapshot is not None:
        start_sequence = snapshot.journal_global_sequence + 1
        baseline = {
            "active_revision_id": snapshot.active_revision_id,
            "project_states": dict(snapshot.project_states),
            "portfolio_state": snapshot.portfolio_state,
            "state_fingerprints": dict(snapshot.state_fingerprints),
        }
        result.snapshot_id = snapshot.id

    result.start_sequence = start_sequence

    # ── Read and verify ──────────────────────────────────────────────
    head = journal.latest_position()
    head_sequence = head.global_sequence if head else 0
    end_sequence = request.to_global_sequence or head_sequence

    if head_sequence == 0:
        result.complete = True
        result.end_sequence = 0
        result.manifest_compatible = True
        return result

    all_records = journal.read_from(global_sequence=1)
    chain = verify_journal_hash_chain(
        all_records,
        expected_head_sequence=(
            request.to_global_sequence
            if request.to_global_sequence is not None
            and request.to_global_sequence > head_sequence
            else None
        ),
    )
    if not chain.valid:
        result.complete = False
        result.corruption_detected_at = (
            chain.corrupted_at_sequence
            or chain.broken_link_at_sequence
            or chain.truncated_after_sequence
        )
        result.mismatches.append(chain.explanation)
        result.warnings.append(
            "Replay stopped: durable history failed verification. It is not "
            "silently repaired and the missing records are not invented."
        )
        result.end_sequence = head_sequence
        logger.error(
            "replay stopped: durable history failed verification",
            extra={
                "organization_id": request.organization_id,
                "execution_mode": ExecutionMode.REPLAY.value,
                "journal_sequence": result.corruption_detected_at,
                "reason": chain.explanation,
            },
        )
        return result

    records = [
        r for r in all_records
        if start_sequence <= r.position.global_sequence <= end_sequence
        and (
            r.organization_id is None
            or r.organization_id == request.organization_id
        )
    ]

    for record in records:
        assert_supported_journal_schema(
            record.payload_schema_version
            if record.payload_schema_version > 1 else 1
        )
        assert_supported_event_type(record.event_type.value)

    result.records_replayed = len(records)
    result.end_sequence = end_sequence
    logger.info(
        "replay starting",
        extra={
            "organization_id": request.organization_id,
            "mode": request.mode.value,
            "execution_mode": ExecutionMode.REPLAY.value,
            "snapshot_id": result.snapshot_id,
            "start_sequence": start_sequence,
            "end_sequence": end_sequence,
            "records_replayed": len(records),
        },
    )

    # ── Manifest compatibility ───────────────────────────────────────
    incompatibilities = _manifest_report(
        records=records, expected=expected_manifest,
    )
    result.manifest_compatible = not incompatibilities
    result.warnings.extend(incompatibilities)

    # ── Reconstruct ──────────────────────────────────────────────────
    context = ExecutionContext(
        mode=ExecutionMode.REPLAY,
        clock=ReplayClock(
            replay_as_of=_replay_as_of(records, snapshot_created_at=(
                snapshot.created_at if snapshot else None
            )),
        ),
        random_seed=0,
        manifest=expected_manifest,
        suppress_side_effects=True,
    )
    fence = SideEffectFence(inner=outbound, context=context)

    result.expected_fingerprints = _expected_fingerprints(
        records=records, baseline=baseline,
    )
    result.evidence_limitations = _evidence_limitations(records)

    if reconstructor is None:
        # No reconstructor: the caller wants verification of history
        # itself, not a rebuilt projection.
        result.complete = True
        result.side_effects_suppressed = _historical_effect_count(records)
        return result

    outcome = reconstructor.apply_records(
        records=records,
        organization_id=request.organization_id,
        context=context,
        baseline=baseline,
    )

    result.reconstructed_revision_id = getattr(
        outcome, "revision_id", None,
    )
    result.reconstructed_fingerprints = dict(
        getattr(outcome, "state_fingerprints", {}) or {}
    )
    result.side_effects_suppressed = (
        fence.suppressed
        + int(getattr(outcome, "suppressed_side_effects", 0))
        + _historical_effect_count(records)
    )
    result.warnings.extend(list(getattr(outcome, "warnings", []) or []))

    # ── Compare ──────────────────────────────────────────────────────
    if request.mode == ReplayMode.VERIFY_EQUIVALENCE:
        result.mismatches.extend(
            _compare_fingerprints(
                expected=result.expected_fingerprints,
                actual=result.reconstructed_fingerprints,
            )
        )

    result.complete = True

    # ── Publish, only if asked ───────────────────────────────────────
    if request.publish_result and state_store is not None:
        _publish(
            outcome=outcome,
            organization_id=request.organization_id,
            state_store=state_store,
            artifact_store=artifact_store,
            result=result,
        )

    logger.info(
        "replay finished",
        extra={
            "organization_id": request.organization_id,
            "mode": request.mode.value,
            "execution_mode": ExecutionMode.REPLAY.value,
            "revision_id": result.reconstructed_revision_id,
            "records_replayed": result.records_replayed,
            "side_effects_suppressed": result.side_effects_suppressed,
            "mismatch_count": len(result.mismatches),
            "complete": result.complete,
            "published": result.published,
        },
    )
    return result


def _replay_as_of(
    records: list[JournalRecord], *, snapshot_created_at: datetime | None,
) -> datetime:
    """Time comes from the events, never from the wall clock."""
    if records:
        return max(r.recorded_at for r in records)
    if snapshot_created_at is not None:
        return snapshot_created_at
    return datetime(1970, 1, 1)


def _manifest_report(
    *,
    records: list[JournalRecord],
    expected: ReproducibilityManifest | None,
) -> list[str]:
    """Compare the manifests history was written under to the expected one."""
    if expected is None:
        return []
    warnings: list[str] = []
    seen: set[str] = set()
    for record in records:
        if record.event_type != JournalEventType.DERIVED_ARTIFACT_PUBLISHED:
            continue
        stored = record.payload.get("manifest")
        if not isinstance(stored, dict):
            continue
        try:
            actual = ReproducibilityManifest.model_validate(stored)
        except Exception:  # noqa: BLE001 — a malformed manifest is a warning
            continue
        for line in manifest_incompatibilities(
            expected=expected, actual=actual,
        ):
            if line not in seen:
                seen.add(line)
                warnings.append(
                    f"Artifact {record.payload.get('artifact_id')} was "
                    f"derived under different code — {line}. Replaying it "
                    f"reproduces the inputs, not the conclusion."
                )
    return warnings


def _expected_fingerprints(
    *,
    records: list[JournalRecord],
    baseline: dict[str, Any] | None,
) -> dict[str, str]:
    """What the published revisions said the state fingerprints were.

    Taken from the journal rather than from the projections, so
    equivalence can be checked after the projections are deleted.
    """
    fingerprints: dict[str, str] = dict(
        (baseline or {}).get("state_fingerprints", {}) or {}
    )
    for record in records:
        if record.event_type != (
            JournalEventType.INTELLIGENCE_REVISION_PUBLISHED
        ):
            continue
        published = record.payload.get("state_fingerprints")
        if isinstance(published, dict):
            fingerprints.update({str(k): str(v) for k, v in published.items()})
    return fingerprints


def _evidence_limitations(records: list[JournalRecord]) -> list[str]:
    """Say honestly where history is thinner than it was.

    Retention policy dropping content, or a source being deleted, are
    both real limits on reconstruction. Reporting them beats quietly
    reaching for a source that no longer exists.
    """
    limitations: list[str] = []
    for record in records:
        if record.event_type == JournalEventType.SOURCE_AVAILABILITY_CHANGED:
            availability = record.payload.get("availability")
            if availability and availability != "available":
                limitations.append(
                    f"Evidence {record.payload.get('evidence_id')} was used "
                    f"at the time, but its source is now '{availability}' "
                    f"and cannot be retrieved."
                )
        elif record.event_type == JournalEventType.EVIDENCE_ACCEPTED:
            evidence = record.payload.get("evidence") or {}
            if (
                evidence.get("normalized_content") is None
                and evidence.get("retention_class") == "minimal"
            ):
                limitations.append(
                    f"Evidence {evidence.get('evidence_id')} was retained "
                    f"under a minimal policy; its content was intentionally "
                    f"not persisted."
                )
    return limitations


def _historical_effect_count(records: list[JournalRecord]) -> int:
    """External consequences that already happened and are not repeated."""
    return sum(1 for r in records if r.event_type in _HISTORICAL_ONLY_EVENTS)


def _compare_fingerprints(
    *, expected: dict[str, str], actual: dict[str, str],
) -> list[str]:
    mismatches: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        before = expected.get(key)
        after = actual.get(key)
        if before is None:
            mismatches.append(f"{key}: reconstructed but never published")
        elif after is None:
            mismatches.append(f"{key}: published but not reconstructed")
        elif before != after:
            mismatches.append(
                f"{key}: expected {before}, reconstructed {after}"
            )
    return mismatches


def _publish(
    *,
    outcome: Any,
    organization_id: str,
    state_store: Any,
    artifact_store: Any,
    result: ReplayResult,
) -> None:
    """Write a reconstructed projection back, promoting atomically."""
    revision_id = getattr(outcome, "revision_id", None)
    if revision_id is None:
        return

    for project_id, state in (
        getattr(outcome, "project_states", {}) or {}
    ).items():
        state_store.put_project_state(
            ProjectRuntimeStateRecord(
                project_id=project_id,
                organization_id=organization_id,
                revision_id=revision_id,
                state_fingerprint=state.get("state_fingerprint", ""),
                state=state,
            )
        )
    portfolio_state = getattr(outcome, "portfolio_state", None)
    if portfolio_state:
        state_store.put_portfolio_state(
            PortfolioRuntimeStateRecord(
                organization_id=organization_id,
                revision_id=revision_id,
                state_fingerprint=portfolio_state.get("state_fingerprint", ""),
                state=portfolio_state,
            )
        )

    state_store.put_revision(
        RuntimeRevisionRecord(
            revision_id=revision_id,
            organization_id=organization_id,
            complete=True,
            state_fingerprints=dict(result.reconstructed_fingerprints),
        )
    )
    try:
        state_store.promote_revision(
            organization_id=organization_id,
            revision_id=revision_id,
            expected_current_revision_id=state_store.get_current_revision(
                organization_id
            ),
        )
        result.published = True
    except RevisionPromotionError as error:
        result.warnings.append(
            f"Reconstructed revision was not promoted: {error}"
        )


# ── Recovery entry points ────────────────────────────────────────────


def recover_runtime_state(
    *,
    organization_id: str,
    journal: Any,
    snapshots: Any = None,
    state_store: Any = None,
    artifact_store: Any = None,
    reconstructor: Any = None,
    manifest: ReproducibilityManifest | None = None,
) -> ReplayResult:
    """Restart recovery: latest usable checkpoint plus the rest of the
    journal, published as current state.

    This is what runs after a crash. Nobody has to rebuild context by
    hand.
    """
    return replay_journal(
        request=ReplayRequest(
            organization_id=organization_id,
            mode=ReplayMode.RECONSTRUCT_CURRENT,
            use_snapshot=True,
            expected_manifest=manifest,
            publish_result=True,
        ),
        journal=journal,
        snapshots=snapshots,
        state_store=state_store,
        artifact_store=artifact_store,
        reconstructor=reconstructor,
        manifest=manifest,
    )


def rebuild_runtime_projections(
    *,
    organization_id: str,
    journal: Any,
    state_store: Any,
    artifact_store: Any = None,
    snapshots: Any = None,
    reconstructor: Any = None,
    manifest: ReproducibilityManifest | None = None,
    discard_snapshots: bool = True,
) -> ReplayResult:
    """Deliberately throw away every projection and regenerate it.

    The administrative escape hatch, and the acceptance test's main
    move: derived state is disposable because the journal is not.
    """
    if hasattr(state_store, "clear_projections"):
        state_store.clear_projections(organization_id)
    if artifact_store is not None and hasattr(artifact_store, "clear"):
        artifact_store.clear()
    if discard_snapshots and snapshots is not None and hasattr(
        snapshots, "clear",
    ):
        snapshots.clear()

    return replay_journal(
        request=ReplayRequest(
            organization_id=organization_id,
            mode=ReplayMode.RECONSTRUCT_CURRENT,
            use_snapshot=not discard_snapshots,
            expected_manifest=manifest,
            publish_result=True,
        ),
        journal=journal,
        snapshots=snapshots,
        state_store=state_store,
        artifact_store=artifact_store,
        reconstructor=reconstructor,
        manifest=manifest,
    )


def verify_replay_equivalence(
    *,
    result: ReplayResult,
    expected_artifact_ids: list[str] | None = None,
    reconstructed_artifact_ids: list[str] | None = None,
) -> ReplayEquivalenceReport:
    """Compare a reconstruction against what was originally published."""
    expected_ids = set(expected_artifact_ids or [])
    actual_ids = set(reconstructed_artifact_ids or [])
    changed = [
        key for key in sorted(
            set(result.expected_fingerprints) & set(
                result.reconstructed_fingerprints
            )
        )
        if result.expected_fingerprints[key]
        != result.reconstructed_fingerprints[key]
    ]
    return ReplayEquivalenceReport(
        equivalent=(
            result.complete
            and not changed
            and not (expected_ids - actual_ids)
            and result.manifest_compatible
        ),
        expected_state_fingerprints=dict(result.expected_fingerprints),
        actual_state_fingerprints=dict(result.reconstructed_fingerprints),
        missing_artifacts=sorted(expected_ids - actual_ids),
        extra_artifacts=sorted(actual_ids - expected_ids),
        changed_artifacts=changed,
        manifest_compatible=result.manifest_compatible,
    )


# ── Health ───────────────────────────────────────────────────────────


def inspect_persistence_health(
    *,
    organization_id: str,
    journal: Any,
    snapshots: Any = None,
    state_store: Any = None,
    manifest: ReproducibilityManifest | None = None,
    last_successful_replay_at: datetime | None = None,
) -> PersistenceHealthReport:
    """A read-only look at whether durable storage is sound."""
    head = journal.latest_position()
    head_sequence = head.global_sequence if head else 0
    chain = verify_journal_hash_chain(journal.read_from(global_sequence=1))

    warnings: list[str] = []
    if not chain.valid:
        warnings.append(chain.explanation)

    latest_snapshot_id: str | None = None
    latest_snapshot_valid: bool | None = None
    if snapshots is not None:
        listed = snapshots.list_snapshots(organization_id)
        if listed:
            newest = listed[0]
            latest_snapshot_id = newest.id
            usable, reason = snapshot_is_usable(
                snapshot=newest, manifest=manifest,
            )
            latest_snapshot_valid = usable
            if not usable:
                warnings.append(reason)

    current_revision_id = (
        state_store.get_current_revision(organization_id)
        if state_store is not None else None
    )
    if head_sequence > 0 and current_revision_id is None:
        warnings.append(
            f"Journal holds {head_sequence} record(s) but no revision is "
            f"current; projections need rebuilding."
        )

    return PersistenceHealthReport(
        journal_head_sequence=head_sequence,
        journal_hash_valid=chain.valid,
        latest_snapshot_id=latest_snapshot_id,
        latest_snapshot_valid=latest_snapshot_valid,
        current_revision_id=current_revision_id,
        last_successful_replay_at=last_successful_replay_at,
        warnings=warnings,
    )


def historical_records(
    *,
    journal: Any,
    event_type: JournalEventType,
    organization_id: str | None = None,
) -> list[JournalRecord]:
    """Every record of one kind — the shape of a historical query."""
    head = journal.latest_position()
    if head is None:
        return []
    return inspect_journal_range(
        journal=journal,
        start_sequence=1,
        end_sequence=head.global_sequence,
        event_types=[event_type],
        organization_id=organization_id,
    )
