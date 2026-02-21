"""Scope Lock — change control that prevents scope creep and protects timelines.

Every scope change follows a formal process:
1. Request filed with justification + business value
2. Auto-generated impact analysis
3. Routed to approver
4. Documented in change log

Scope can be "frozen" for critical program phases (e.g., code freeze,
launch prep), at which point ALL changes require a change request.
"""

from __future__ import annotations

import json
from datetime import datetime, date
from uuid import uuid4

from lltpm.artifacts.store import ArtifactStore
from lltpm.artifacts.milestone_plan import MilestonePlanManager
from lltpm.artifacts.raid_log import RAIDLogManager
from lltpm.models.scope import (
    ImpactAnalysis,
    ImpactLevel,
    ScopeBaseline,
    ScopeChangeRequest,
    ScopeChangeStatus,
    ScopeChangeType,
    ScopeItem,
)

SCOPE_ARTIFACT = "scope_baseline"


class ScopeLock:
    """Scope change control engine."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store
        self._milestones = MilestonePlanManager(store)
        self._raid = RAIDLogManager(store)

    # ── Baseline management ───────────────────────────────────

    def add_scope_item(
        self,
        program_id: str,
        title: str,
        description: str = "",
        owner: str = "",
        target_date: str | None = None,
        locked: bool = False,
    ) -> ScopeItem:
        """Add an item to the scope baseline."""
        baseline = self._load_baseline(program_id)
        item = ScopeItem(
            id=f"SC-{uuid4().hex[:6]}",
            title=title,
            description=description,
            owner=owner,
            target_date=date.fromisoformat(target_date) if target_date else None,
            is_locked=locked,
            locked_by="LLTPM" if locked else "",
            locked_at=datetime.utcnow() if locked else None,
        )
        baseline.items.append(item)
        self._save_baseline(program_id, baseline)
        return item

    def lock_item(self, program_id: str, item_id: str, locked_by: str = "LLTPM") -> bool:
        """Lock a scope item — changes now require a formal change request."""
        baseline = self._load_baseline(program_id)
        for item in baseline.items:
            if item.id == item_id:
                item.is_locked = True
                item.locked_by = locked_by
                item.locked_at = datetime.utcnow()
                self._save_baseline(program_id, baseline)
                return True
        return False

    def freeze_scope(self, program_id: str, frozen_by: str = "LLTPM") -> bool:
        """Freeze ALL scope — every change requires a change request."""
        baseline = self._load_baseline(program_id)
        baseline.frozen = True
        baseline.frozen_by = frozen_by
        baseline.frozen_at = datetime.utcnow()
        for item in baseline.items:
            item.is_locked = True
            item.locked_by = frozen_by
            item.locked_at = datetime.utcnow()
        self._save_baseline(program_id, baseline)
        return True

    def unfreeze_scope(self, program_id: str) -> bool:
        """Unfreeze scope — normal change process resumes."""
        baseline = self._load_baseline(program_id)
        baseline.frozen = False
        self._save_baseline(program_id, baseline)
        return True

    # ── Change requests ───────────────────────────────────────

    def request_change(
        self,
        program_id: str,
        title: str,
        description: str = "",
        change_type: str = "MODIFY_REQUIREMENT",
        requested_by: str = "",
        justification: str = "",
        business_value: str = "",
        affected_items: list[str] | None = None,
        new_timeline: str | None = None,
        new_owner: str = "",
        approver: str = "",
    ) -> ScopeChangeRequest:
        """File a scope change request."""
        baseline = self._load_baseline(program_id)

        try:
            ct = ScopeChangeType(change_type)
        except ValueError:
            ct = ScopeChangeType.MODIFY_REQUIREMENT

        scr = ScopeChangeRequest(
            id=f"SCR-{uuid4().hex[:6]}",
            program_id=program_id,
            title=title,
            description=description,
            change_type=ct,
            requested_by=requested_by,
            justification=justification,
            business_value=business_value,
            affected_items=affected_items or [],
            new_timeline=date.fromisoformat(new_timeline) if new_timeline else None,
            new_owner=new_owner,
            approver=approver,
        )

        # Auto-generate impact analysis
        scr.impact = self._analyze_impact(program_id, scr)

        baseline.change_requests.append(scr)
        self._save_baseline(program_id, baseline)
        return scr

    def approve_change(
        self,
        program_id: str,
        scr_id: str,
        approved_by: str,
        rationale: str = "",
    ) -> bool:
        """Approve a scope change request."""
        baseline = self._load_baseline(program_id)
        for scr in baseline.change_requests:
            if scr.id == scr_id:
                scr.status = ScopeChangeStatus.APPROVED
                scr.approved_by = approved_by
                scr.approval_rationale = rationale
                scr.reviewed_at = datetime.utcnow()
                scr.resolved_at = datetime.utcnow()
                self._save_baseline(program_id, baseline)
                return True
        return False

    def reject_change(
        self,
        program_id: str,
        scr_id: str,
        rejected_by: str,
        reason: str,
    ) -> bool:
        """Reject a scope change request."""
        baseline = self._load_baseline(program_id)
        for scr in baseline.change_requests:
            if scr.id == scr_id:
                scr.status = ScopeChangeStatus.REJECTED
                scr.approved_by = rejected_by
                scr.rejection_reason = reason
                scr.reviewed_at = datetime.utcnow()
                scr.resolved_at = datetime.utcnow()
                self._save_baseline(program_id, baseline)
                return True
        return False

    # ── Queries ───────────────────────────────────────────────

    def get_baseline(self, program_id: str) -> ScopeBaseline:
        return self._load_baseline(program_id)

    def get_summary(self, program_id: str) -> str:
        return self._load_baseline(program_id).summary()

    def get_pending_changes(self, program_id: str) -> list[ScopeChangeRequest]:
        return self._load_baseline(program_id).pending_changes

    def is_frozen(self, program_id: str) -> bool:
        return self._load_baseline(program_id).frozen

    def validate_change(self, program_id: str, item_id: str) -> dict:
        """Check if a change to an item requires a change request."""
        baseline = self._load_baseline(program_id)
        if baseline.frozen:
            return {"requires_scr": True, "reason": "Scope is frozen — all changes require a change request."}
        for item in baseline.items:
            if item.id == item_id and item.is_locked:
                return {"requires_scr": True, "reason": f"Item '{item.title}' is locked by {item.locked_by}."}
        return {"requires_scr": False, "reason": "No restriction."}

    # ── Internal helpers ──────────────────────────────────────

    def _analyze_impact(self, program_id: str, scr: ScopeChangeRequest) -> ImpactAnalysis:
        """Auto-generate impact analysis for a change request."""
        impact = ImpactAnalysis()

        # Timeline impact
        if scr.change_type == ScopeChangeType.TIMELINE_CHANGE:
            impact.timeline_impact = ImpactLevel.HIGH
            impact.timeline_detail = "Direct timeline change requested."
        elif scr.change_type == ScopeChangeType.ADD_FEATURE:
            impact.timeline_impact = ImpactLevel.MEDIUM
            impact.timeline_detail = "New feature may require additional sprint cycles."
        elif scr.change_type == ScopeChangeType.REMOVE_FEATURE:
            impact.timeline_impact = ImpactLevel.LOW
            impact.timeline_detail = "Removing scope may accelerate timeline."

        # Resource impact
        if scr.change_type in (ScopeChangeType.ADD_FEATURE, ScopeChangeType.RESOURCE_CHANGE):
            impact.resource_impact = ImpactLevel.MEDIUM
            impact.resource_detail = "May require additional engineering capacity."

        # Dependency impact
        if scr.affected_items:
            plan = self._milestones.get_plan(program_id)
            affected_milestones = [
                m for m in plan.milestones
                if any(dep in scr.affected_items for dep in m.dependencies)
            ]
            if affected_milestones:
                impact.dependency_impact = ImpactLevel.HIGH
                impact.dependency_detail = (
                    f"Affects {len(affected_milestones)} downstream milestones: "
                    f"{', '.join(m.name for m in affected_milestones[:3])}"
                )

        # Risk impact
        if scr.change_type in (ScopeChangeType.TIMELINE_CHANGE, ScopeChangeType.OWNERSHIP_CHANGE):
            impact.risk_impact = ImpactLevel.MEDIUM
            impact.risk_detail = "Timeline or ownership changes increase delivery risk."

        impact.compute_overall()
        return impact

    def _load_baseline(self, program_id: str) -> ScopeBaseline:
        baseline = self._store.load(program_id, SCOPE_ARTIFACT, ScopeBaseline)
        if baseline is None:
            baseline = ScopeBaseline(program_id=program_id)
        return baseline

    def _save_baseline(self, program_id: str, baseline: ScopeBaseline) -> None:
        self._store.save(program_id, SCOPE_ARTIFACT, baseline)
