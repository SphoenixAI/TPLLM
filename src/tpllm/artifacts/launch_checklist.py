"""Launch readiness checklist management."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from tpllm.artifacts.store import ArtifactStore

ARTIFACT_NAME = "launch_checklist"


class CheckStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"
    WAIVED = "WAIVED"


class ChecklistItem(BaseModel):
    category: str  # e.g. "Quality", "Security", "Privacy", "Monitoring"
    description: str
    owner: str = ""
    status: CheckStatus = CheckStatus.NOT_STARTED
    notes: str = ""
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LaunchChecklist(BaseModel):
    program_id: str = ""
    items: list[ChecklistItem] = Field(default_factory=list)
    rollback_plan: str = ""
    monitoring_plan: str = ""
    comms_plan: str = ""

    @property
    def ready(self) -> bool:
        return all(
            item.status in (CheckStatus.PASSED, CheckStatus.WAIVED)
            for item in self.items
        )

    @property
    def blockers(self) -> list[ChecklistItem]:
        return [
            item for item in self.items
            if item.status in (CheckStatus.NOT_STARTED, CheckStatus.IN_PROGRESS, CheckStatus.FAILED)
        ]

    def summary(self) -> str:
        total = len(self.items)
        passed = sum(1 for i in self.items if i.status == CheckStatus.PASSED)
        waived = sum(1 for i in self.items if i.status == CheckStatus.WAIVED)
        failed = sum(1 for i in self.items if i.status == CheckStatus.FAILED)
        lines = [
            "# Launch Readiness",
            f"**Status**: {'READY' if self.ready else 'NOT READY'}",
            f"Passed: {passed}/{total} | Waived: {waived} | Failed: {failed}",
        ]
        blockers = self.blockers
        if blockers:
            lines.append("\n## Blockers")
            for b in blockers:
                lines.append(f"- [{b.status.value}] {b.category}: {b.description} — Owner: {b.owner}")
        if self.rollback_plan:
            lines.append(f"\n## Rollback Plan\n{self.rollback_plan}")
        return "\n".join(lines)


# ── Default launch gates ──────────────────────────────────────

DEFAULT_GATES = [
    ("Quality", "All P0/P1 bugs resolved or waived"),
    ("Quality", "Test coverage meets threshold"),
    ("Quality", "Performance benchmarks pass"),
    ("Security", "Security review completed"),
    ("Privacy", "Privacy review completed"),
    ("Monitoring", "Alerts and dashboards configured"),
    ("Monitoring", "Rollback procedure tested"),
    ("Ops", "Runbook created and reviewed"),
    ("Ops", "On-call rotation confirmed"),
    ("Comms", "Release notes drafted"),
    ("Comms", "Support team briefed"),
]


class LaunchChecklistManager:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def _load(self, program_id: str) -> LaunchChecklist:
        existing = self._store.load(program_id, ARTIFACT_NAME, LaunchChecklist)
        if existing is None:
            existing = LaunchChecklist(program_id=program_id)
        return existing

    def _save(self, checklist: LaunchChecklist) -> None:
        self._store.save(checklist.program_id, ARTIFACT_NAME, checklist)

    def initialize(self, program_id: str) -> LaunchChecklist:
        checklist = LaunchChecklist(
            program_id=program_id,
            items=[
                ChecklistItem(category=cat, description=desc)
                for cat, desc in DEFAULT_GATES
            ],
        )
        self._save(checklist)
        return checklist

    def update_item(
        self,
        program_id: str,
        index: int,
        status: str,
        owner: str = "",
        notes: str = "",
    ) -> bool:
        checklist = self._load(program_id)
        if index < 0 or index >= len(checklist.items):
            return False
        item = checklist.items[index]
        item.status = CheckStatus(status)
        if owner:
            item.owner = owner
        if notes:
            item.notes = notes
        item.updated_at = datetime.utcnow()
        self._save(checklist)
        return True

    def set_rollback_plan(self, program_id: str, plan: str) -> None:
        checklist = self._load(program_id)
        checklist.rollback_plan = plan
        self._save(checklist)

    def get_checklist(self, program_id: str) -> LaunchChecklist:
        return self._load(program_id)

    def get_summary(self, program_id: str) -> str:
        return self._load(program_id).summary()

    def is_ready(self, program_id: str) -> bool:
        return self._load(program_id).ready
