"""Active calibration registry — the runtime consult layer.

Commit 9 created the control plane: governed eligibility → bounded
application → ``ActiveCalibrationRecord`` → reversibility snapshot →
append-only ledger → auto-revert. It deliberately stopped before wiring
application into the ranker.

This module closes that gap. The ranker stays pure — it only asks: given
this option, base score, and decision context, is there an active governed
calibration that applies? The registry answers either "no adjustment" or
"apply this bounded adjustment, with explanation and trace".

The registry never grants, renews, or escalates governance authority, never
creates or clears calibration proposals, and never mutates governance
records. It reads active records that Commit 9 already applied, checks
scope conservatively (no scope means no runtime authority), bounds every
adjustment per-record and in total, and emits immutable traces so
downstream evidence can always see what changed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────

class CalibrationApplicationDecision(str, Enum):
    APPLIED = "applied"
    SKIPPED_NO_REGISTRY = "skipped_no_registry"
    SKIPPED_KILL_SWITCH = "skipped_kill_switch"
    SKIPPED_NO_ACTIVE_RECORDS = "skipped_no_active_records"
    SKIPPED_INACTIVE_RECORD = "skipped_inactive_record"
    SKIPPED_OUT_OF_SCOPE = "skipped_out_of_scope"
    SKIPPED_EXPIRED_OR_INELIGIBLE = "skipped_expired_or_ineligible"
    SKIPPED_ZERO_DELTA = "skipped_zero_delta"
    SKIPPED_UNSUPPORTED_PROPOSAL = "skipped_unsupported_proposal"


class CalibrationAdjustmentDirection(str, Enum):
    INCREASE_SCORE = "increase_score"
    DECREASE_SCORE = "decrease_score"
    NO_CHANGE = "no_change"


class ActiveRegistryStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    KILL_SWITCHED = "kill_switched"


# ── Models ─────────────────────────────────────────────────────────────

class CalibrationConsultContext(BaseModel):
    """Decision context passed from the ranker into the registry.

    Gives the registry enough information to check scope without coupling
    directly to the whole graph.
    """
    decision_id: Optional[str] = None
    team_id: Optional[str] = None
    domain_id: Optional[str] = None
    workstream_id: Optional[str] = None
    milestone_id: Optional[str] = None
    service_ids: list[str] = Field(default_factory=list)
    repository_ids: list[str] = Field(default_factory=list)
    intervention_type: Optional[str] = None
    constraint_type: Optional[str] = None
    metric_name: Optional[str] = None
    graph_snapshot_id: Optional[str] = None
    current_decision_count: int = 0
    now: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CalibrationAdjustment(BaseModel):
    """One active calibration's contribution to one option."""
    active_calibration_id: str
    calibration_proposal_id: str
    base_score: float
    delta: float
    bounded_delta: float
    adjusted_score: float
    direction: CalibrationAdjustmentDirection
    decision: CalibrationApplicationDecision
    reason: str = ""
    scope_matched: bool = False


class CalibrationConsultResult(BaseModel):
    """The final registry answer for one option."""
    option_id: Optional[str] = None
    base_score: float
    adjusted_score: float
    total_bounded_delta: float = 0.0
    applied_adjustments: list[CalibrationAdjustment] = Field(default_factory=list)
    skipped_adjustments: list[CalibrationAdjustment] = Field(default_factory=list)
    decision: CalibrationApplicationDecision = (
        CalibrationApplicationDecision.SKIPPED_NO_ACTIVE_RECORDS
    )
    explanation: str = ""


class RankerCalibrationTrace(BaseModel):
    """Audit trace emitted when the ranker consults the registry.

    Runtime observability, not governance authority.
    """
    id: str
    decision_id: Optional[str] = None
    option_id: Optional[str] = None
    base_score: float
    adjusted_score: float
    total_bounded_delta: float
    applied_calibration_ids: list[str] = Field(default_factory=list)
    skipped_calibration_ids: list[str] = Field(default_factory=list)
    explanation: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ActiveCalibrationRegistry(BaseModel):
    """Holds active calibration records and consults them.

    Records are stored duck-typed (``object``) so the registry stays
    decoupled from the Commit 9 control-plane model — it reads records,
    it never creates their authority.
    """
    status: ActiveRegistryStatus = ActiveRegistryStatus.ACTIVE
    active_records: dict[str, object] = Field(default_factory=dict)
    traces: list[RankerCalibrationTrace] = Field(default_factory=list)
    max_total_adjustment: float = 0.25
    max_single_adjustment: float = 0.15
    kill_switch_engaged: bool = False

    model_config = {"arbitrary_types_allowed": True}


# ── Duck-typed accessors ─────────────────────────────────────────────

def get_option_id(option: object) -> str | None:
    return (
        getattr(option, "id", None)
        or getattr(option, "intervention_id", None)
        or getattr(option, "option_id", None)
    )


def get_option_type(option: object) -> str | None:
    raw = (
        getattr(option, "intervention_type", None)
        or getattr(option, "type", None)
        or getattr(option, "option_type", None)
    )
    return getattr(raw, "value", raw)


def get_active_record_id(record: object) -> str | None:
    return (
        getattr(record, "id", None)
        or getattr(record, "active_calibration_id", None)
        or getattr(record, "record_id", None)
    )


def get_record_proposal_id(record: object) -> str | None:
    return (
        getattr(record, "calibration_proposal_id", None)
        or getattr(record, "proposal_id", None)
    )


def get_record_status_value(record: object) -> str | None:
    raw = getattr(record, "status", None)
    return getattr(raw, "value", raw)


def is_record_active(record: object) -> bool:
    """Conservative: unknown status does not apply."""
    status = get_record_status_value(record)
    inactive_values = {
        "inactive",
        "reverted",
        "revoked",
        "expired",
        "quarantined",
        "disabled",
        "superseded",
    }
    if status in inactive_values:
        return False
    if hasattr(record, "is_active"):
        return bool(getattr(record, "is_active"))
    if status in {"active", "applied"}:
        return True
    return False


def get_proposed_delta(record: object) -> float:
    proposal = getattr(record, "proposal", None)
    delta = getattr(record, "proposed_delta", None)
    if delta is None:
        delta = getattr(record, "delta", None)
    if delta is None and proposal is not None:
        delta = getattr(proposal, "proposed_delta", None)
    if delta is None:
        return 0.0
    return float(delta)


def get_target_intervention_type(record: object) -> str | None:
    proposal = getattr(record, "proposal", None)
    raw = getattr(record, "target_intervention_type", None)
    if raw is None and proposal is not None:
        raw = getattr(proposal, "target_intervention_type", None)
    return getattr(raw, "value", raw)


def _get_nested_attr(obj: object | None, names: list[str]) -> object | None:
    if obj is None:
        return None
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _as_value(value: object | None) -> object | None:
    return getattr(value, "value", value)


def _list_overlap(a: object, b: object) -> bool:
    return bool(set(a or []) & set(b or []))


# ── Scope matching ───────────────────────────────────────────────────

def scope_matches(
    *,
    record: object,
    option: object,
    context: CalibrationConsultContext,
) -> bool:
    """A calibration applies only inside its governed scope.

    Conservative defaults: every scope dimension the record declares must
    match; a record that declares no scope at all does not apply. No scope
    means no runtime authority.
    """
    proposal = getattr(record, "proposal", None)
    scope_signature = _get_nested_attr(record, ["scope_signature"]) or _get_nested_attr(
        proposal, ["scope_signature"]
    )

    option_type = get_option_type(option) or context.intervention_type
    target_type = get_target_intervention_type(record) or _as_value(
        _get_nested_attr(scope_signature, ["target_intervention_type"])
    )

    scoped_anything = False

    if target_type:
        scoped_anything = True
        if option_type != target_type:
            return False

    allowed_team = _get_nested_attr(
        record, ["team_id", "origin_team_id"]
    ) or _get_nested_attr(scope_signature, ["origin_team_id"])
    if allowed_team:
        scoped_anything = True
        if isinstance(allowed_team, (list, set, tuple)):
            if context.team_id not in allowed_team:
                return False
        elif context.team_id != allowed_team:
            return False

    allowed_domain = _get_nested_attr(
        record, ["domain_id", "origin_domain_id"]
    ) or _get_nested_attr(scope_signature, ["origin_domain_id"])
    if allowed_domain:
        scoped_anything = True
        if isinstance(allowed_domain, (list, set, tuple)):
            if context.domain_id not in allowed_domain:
                return False
        elif context.domain_id != allowed_domain:
            return False

    scoped_services = _get_nested_attr(
        record, ["service_ids", "origin_service_ids"]
    ) or _get_nested_attr(scope_signature, ["origin_service_ids"])
    if scoped_services:
        scoped_anything = True
        if not _list_overlap(scoped_services, context.service_ids):
            return False

    scoped_repos = _get_nested_attr(
        record, ["repository_ids", "origin_repository_ids"]
    ) or _get_nested_attr(scope_signature, ["origin_repository_ids"])
    if scoped_repos:
        scoped_anything = True
        if not _list_overlap(scoped_repos, context.repository_ids):
            return False

    return scoped_anything


# ── Adjustment logic ─────────────────────────────────────────────────

def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def calculate_bounded_adjustment(
    *,
    record: object,
    option: object,
    base_score: float,
    context: CalibrationConsultContext,
    max_single_adjustment: float,
) -> CalibrationAdjustment:
    """One record's bounded contribution to one option. Never mutates."""
    record_id = get_active_record_id(record) or "unknown"
    proposal_id = get_record_proposal_id(record) or "unknown"

    if not is_record_active(record):
        return CalibrationAdjustment(
            active_calibration_id=record_id,
            calibration_proposal_id=proposal_id,
            base_score=base_score,
            delta=0.0,
            bounded_delta=0.0,
            adjusted_score=base_score,
            direction=CalibrationAdjustmentDirection.NO_CHANGE,
            decision=CalibrationApplicationDecision.SKIPPED_INACTIVE_RECORD,
            reason="Active calibration record is not active.",
            scope_matched=False,
        )

    matched = scope_matches(record=record, option=option, context=context)
    if not matched:
        return CalibrationAdjustment(
            active_calibration_id=record_id,
            calibration_proposal_id=proposal_id,
            base_score=base_score,
            delta=0.0,
            bounded_delta=0.0,
            adjusted_score=base_score,
            direction=CalibrationAdjustmentDirection.NO_CHANGE,
            decision=CalibrationApplicationDecision.SKIPPED_OUT_OF_SCOPE,
            reason="Calibration scope does not match option/context.",
            scope_matched=False,
        )

    raw_delta = get_proposed_delta(record)
    if raw_delta == 0.0:
        return CalibrationAdjustment(
            active_calibration_id=record_id,
            calibration_proposal_id=proposal_id,
            base_score=base_score,
            delta=0.0,
            bounded_delta=0.0,
            adjusted_score=base_score,
            direction=CalibrationAdjustmentDirection.NO_CHANGE,
            decision=CalibrationApplicationDecision.SKIPPED_ZERO_DELTA,
            reason="Calibration delta is zero.",
            scope_matched=True,
        )

    bounded_delta = _clamp(
        raw_delta,
        -abs(max_single_adjustment),
        abs(max_single_adjustment),
    )
    adjusted = base_score + bounded_delta

    if bounded_delta > 0:
        direction = CalibrationAdjustmentDirection.INCREASE_SCORE
    elif bounded_delta < 0:
        direction = CalibrationAdjustmentDirection.DECREASE_SCORE
    else:
        direction = CalibrationAdjustmentDirection.NO_CHANGE

    return CalibrationAdjustment(
        active_calibration_id=record_id,
        calibration_proposal_id=proposal_id,
        base_score=base_score,
        delta=raw_delta,
        bounded_delta=bounded_delta,
        adjusted_score=adjusted,
        direction=direction,
        decision=CalibrationApplicationDecision.APPLIED,
        reason="Bounded calibration adjustment applied.",
        scope_matched=True,
    )


def combine_adjustments(
    *,
    option_id: str | None,
    base_score: float,
    adjustments: list[CalibrationAdjustment],
    max_total_adjustment: float,
) -> CalibrationConsultResult:
    """Sum applied bounded deltas, clamp the total, preserve both lists."""
    applied = [
        adjustment
        for adjustment in adjustments
        if adjustment.decision == CalibrationApplicationDecision.APPLIED
    ]
    skipped = [
        adjustment
        for adjustment in adjustments
        if adjustment.decision != CalibrationApplicationDecision.APPLIED
    ]

    if not applied:
        skipped_decision = (
            skipped[0].decision
            if skipped
            else CalibrationApplicationDecision.SKIPPED_NO_ACTIVE_RECORDS
        )
        return CalibrationConsultResult(
            option_id=option_id,
            base_score=base_score,
            adjusted_score=base_score,
            total_bounded_delta=0.0,
            applied_adjustments=[],
            skipped_adjustments=skipped,
            decision=skipped_decision,
            explanation="No active calibration adjustment applied.",
        )

    raw_total = sum(adjustment.bounded_delta for adjustment in applied)
    total = _clamp(raw_total, -abs(max_total_adjustment), abs(max_total_adjustment))

    return CalibrationConsultResult(
        option_id=option_id,
        base_score=base_score,
        adjusted_score=base_score + total,
        total_bounded_delta=total,
        applied_adjustments=applied,
        skipped_adjustments=skipped,
        decision=CalibrationApplicationDecision.APPLIED,
        explanation=(
            f"Applied {len(applied)} active calibration adjustment(s); "
            f"total bounded delta={total}."
        ),
    )


# ── Registry functions ───────────────────────────────────────────────

def register_active_calibration(
    *,
    registry: ActiveCalibrationRegistry,
    record: object,
) -> ActiveCalibrationRegistry:
    """Register a record the Commit 9 control plane already applied.

    Creates no authority — it only makes an existing record consultable.
    """
    record_id = get_active_record_id(record)
    if not record_id:
        raise ValueError("Cannot register active calibration without record id.")
    updated_records = dict(registry.active_records)
    updated_records[record_id] = record
    return registry.model_copy(update={"active_records": updated_records})


def deactivate_active_calibration(
    *,
    registry: ActiveCalibrationRegistry,
    active_calibration_id: str,
) -> ActiveCalibrationRegistry:
    """Remove a record from the consultable set. Removal-only."""
    updated_records = dict(registry.active_records)
    updated_records.pop(active_calibration_id, None)
    return registry.model_copy(update={"active_records": updated_records})


def get_active_calibrations_for_context(
    *,
    registry: ActiveCalibrationRegistry,
    option: object,
    context: CalibrationConsultContext,
) -> list[object]:
    """Active, in-scope records that might apply to this option."""
    if registry.status != ActiveRegistryStatus.ACTIVE:
        return []
    if registry.kill_switch_engaged:
        return []
    records: list[object] = []
    for record in registry.active_records.values():
        if not is_record_active(record):
            continue
        if scope_matches(record=record, option=option, context=context):
            records.append(record)
    return records


def consult_active_calibrations(
    *,
    registry: ActiveCalibrationRegistry | None,
    option: object,
    base_score: float,
    context: CalibrationConsultContext,
) -> CalibrationConsultResult:
    """The main runtime consult. Base score is always preserved.

    No registry, disabled registry, engaged kill switch, or no records →
    the base score comes back unchanged with the matching skip decision.
    """
    option_id = get_option_id(option)

    if registry is None:
        return CalibrationConsultResult(
            option_id=option_id,
            base_score=base_score,
            adjusted_score=base_score,
            decision=CalibrationApplicationDecision.SKIPPED_NO_REGISTRY,
            explanation="No active calibration registry provided.",
        )

    if registry.kill_switch_engaged or registry.status == ActiveRegistryStatus.KILL_SWITCHED:
        return CalibrationConsultResult(
            option_id=option_id,
            base_score=base_score,
            adjusted_score=base_score,
            decision=CalibrationApplicationDecision.SKIPPED_KILL_SWITCH,
            explanation="Active calibration registry kill switch is engaged.",
        )

    if registry.status != ActiveRegistryStatus.ACTIVE:
        return CalibrationConsultResult(
            option_id=option_id,
            base_score=base_score,
            adjusted_score=base_score,
            decision=CalibrationApplicationDecision.SKIPPED_NO_ACTIVE_RECORDS,
            explanation="Active calibration registry is disabled.",
        )

    if not registry.active_records:
        return CalibrationConsultResult(
            option_id=option_id,
            base_score=base_score,
            adjusted_score=base_score,
            decision=CalibrationApplicationDecision.SKIPPED_NO_ACTIVE_RECORDS,
            explanation="No active calibration records registered.",
        )

    adjustments: list[CalibrationAdjustment] = []
    for record in registry.active_records.values():
        adjustments.append(
            calculate_bounded_adjustment(
                record=record,
                option=option,
                base_score=base_score,
                context=context,
                max_single_adjustment=registry.max_single_adjustment,
            )
        )

    return combine_adjustments(
        option_id=option_id,
        base_score=base_score,
        adjustments=adjustments,
        max_total_adjustment=registry.max_total_adjustment,
    )


# ── Trace helpers ────────────────────────────────────────────────────

def build_ranker_calibration_trace(
    *,
    context: CalibrationConsultContext,
    result: CalibrationConsultResult,
) -> RankerCalibrationTrace:
    """Immutable observability record of one consult."""
    return RankerCalibrationTrace(
        id=str(uuid4()),
        decision_id=context.decision_id,
        option_id=result.option_id,
        base_score=result.base_score,
        adjusted_score=result.adjusted_score,
        total_bounded_delta=result.total_bounded_delta,
        applied_calibration_ids=[
            adjustment.active_calibration_id
            for adjustment in result.applied_adjustments
        ],
        skipped_calibration_ids=[
            adjustment.active_calibration_id
            for adjustment in result.skipped_adjustments
        ],
        explanation=result.explanation,
    )


def record_calibration_trace(
    *,
    registry: ActiveCalibrationRegistry,
    trace: RankerCalibrationTrace,
) -> ActiveCalibrationRegistry:
    """Append-only by copy: returns a new registry, the old one untouched."""
    updated_traces = list(registry.traces)
    updated_traces.append(trace)
    return registry.model_copy(update={"traces": updated_traces})
