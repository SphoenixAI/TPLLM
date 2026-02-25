"""Milestone plan management."""

from __future__ import annotations

from datetime import date as _date, datetime
from uuid import uuid4

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.milestone import (
    GateCriterion,
    Milestone,
    MilestonePlan,
    MilestoneStatus,
)

ARTIFACT_NAME = "milestone_plan"


class MilestonePlanManager:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def _load(self, program_id: str) -> MilestonePlan:
        existing = self._store.load(program_id, ARTIFACT_NAME, MilestonePlan)
        if existing is None:
            existing = MilestonePlan(program_id=program_id)
        return existing

    def _save(self, plan: MilestonePlan) -> None:
        self._store.save(plan.program_id, ARTIFACT_NAME, plan)

    def add_milestone(
        self,
        program_id: str,
        name: str,
        description: str = "",
        phase: str = "",
        target_date: str | None = None,
        owner: str = "",
        on_critical_path: bool = False,
        dependencies: list[str] | None = None,
    ) -> Milestone:
        plan = self._load(program_id)
        ms = Milestone(
            id=f"MS-{uuid4().hex[:6]}",
            name=name,
            description=description,
            phase=phase,
            target_date=_date.fromisoformat(target_date) if target_date else None,
            owner=owner,
            on_critical_path=on_critical_path,
            dependencies=dependencies or [],
        )
        plan.milestones.append(ms)
        self._save(plan)
        return ms

    def update_milestone_status(
        self,
        program_id: str,
        milestone_id: str,
        status: str,
    ) -> bool:
        plan = self._load(program_id)
        for m in plan.milestones:
            if m.id == milestone_id:
                m.status = MilestoneStatus(status)
                m.updated_at = datetime.utcnow()
                if status == "COMPLETED":
                    m.actual_date = _date.today()
                self._save(plan)
                return True
        return False

    def add_gate_criterion(
        self,
        program_id: str,
        milestone_id: str,
        kind: str,  # "entry" or "exit"
        description: str,
        owner: str = "",
    ) -> bool:
        plan = self._load(program_id)
        for m in plan.milestones:
            if m.id == milestone_id:
                criterion = GateCriterion(description=description, owner=owner)
                if kind == "entry":
                    m.entry_criteria.append(criterion)
                else:
                    m.exit_criteria.append(criterion)
                self._save(plan)
                return True
        return False

    def get_plan(self, program_id: str) -> MilestonePlan:
        return self._load(program_id)

    def get_summary(self, program_id: str) -> str:
        return self._load(program_id).summary()

    def get_at_risk(self, program_id: str) -> list[Milestone]:
        return self._load(program_id).at_risk

    def get_overdue(self, program_id: str) -> list[Milestone]:
        return self._load(program_id).overdue
