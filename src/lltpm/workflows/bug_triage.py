"""Bug triage automation — severity classification, SLA tracking, and stop-ship detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.artifacts.store import ArtifactStore


class BugSeverity(str, Enum):
    P0 = "P0"  # Stop-ship
    P1 = "P1"  # Must fix before launch
    P2 = "P2"  # Should fix
    P3 = "P3"  # Nice to fix


class BugStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    FIXED = "FIXED"
    WONT_FIX = "WONT_FIX"
    DUPLICATE = "DUPLICATE"


# SLA in calendar days by severity
DEFAULT_SLAS = {
    BugSeverity.P0: 1,
    BugSeverity.P1: 3,
    BugSeverity.P2: 7,
    BugSeverity.P3: 14,
}


class Bug(BaseModel):
    id: str = ""
    title: str
    description: str = ""
    severity: BugSeverity = BugSeverity.P2
    status: BugStatus = BugStatus.OPEN
    owner: str = ""
    component: str = ""
    reported_by: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BugDashboard(BaseModel):
    program_id: str = ""
    bugs: list[Bug] = Field(default_factory=list)

    @property
    def stop_ship(self) -> list[Bug]:
        return [b for b in self.bugs if b.severity == BugSeverity.P0 and b.status == BugStatus.OPEN]

    @property
    def open_bugs(self) -> list[Bug]:
        return [b for b in self.bugs if b.status in (BugStatus.OPEN, BugStatus.IN_PROGRESS)]

    def burndown(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sev in BugSeverity:
            counts[sev.value] = sum(
                1 for b in self.bugs
                if b.severity == sev and b.status in (BugStatus.OPEN, BugStatus.IN_PROGRESS)
            )
        return counts

    def summary(self) -> str:
        bd = self.burndown()
        lines = [
            "# Bug Dashboard",
            f"Open: {len(self.open_bugs)} | Stop-ship: {len(self.stop_ship)}",
            "",
            "## Burn-down by Severity",
        ]
        for sev, count in bd.items():
            lines.append(f"- {sev}: {count}")
        if self.stop_ship:
            lines.append("\n## Stop-Ship Bugs")
            for b in self.stop_ship:
                lines.append(f"- **{b.title}** — Owner: {b.owner} — Component: {b.component}")
        return "\n".join(lines)


ARTIFACT_NAME = "bug_dashboard"


@dataclass
class SLAViolation:
    bug_id: str
    title: str
    severity: str
    days_open: int
    sla_days: int
    owner: str


class BugTriageEngine:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def _load(self, program_id: str) -> BugDashboard:
        existing = self._store.load(program_id, ARTIFACT_NAME, BugDashboard)
        if existing is None:
            existing = BugDashboard(program_id=program_id)
        return existing

    def _save(self, dashboard: BugDashboard) -> None:
        self._store.save(dashboard.program_id, ARTIFACT_NAME, dashboard)

    def add_bug(
        self,
        program_id: str,
        bug_id: str,
        title: str,
        severity: str = "P2",
        owner: str = "",
        component: str = "",
        description: str = "",
    ) -> Bug:
        dashboard = self._load(program_id)
        bug = Bug(
            id=bug_id,
            title=title,
            severity=BugSeverity(severity),
            owner=owner,
            component=component,
            description=description,
        )
        dashboard.bugs.append(bug)
        self._save(dashboard)
        return bug

    def update_bug_status(self, program_id: str, bug_id: str, status: str) -> bool:
        dashboard = self._load(program_id)
        for b in dashboard.bugs:
            if b.id == bug_id:
                b.status = BugStatus(status)
                b.updated_at = datetime.utcnow()
                self._save(dashboard)
                return True
        return False

    def check_sla_violations(self, program_id: str) -> list[SLAViolation]:
        dashboard = self._load(program_id)
        violations = []
        now = datetime.utcnow()
        for bug in dashboard.open_bugs:
            sla = DEFAULT_SLAS.get(bug.severity, 14)
            days_open = (now - bug.created_at).days
            if days_open > sla:
                violations.append(SLAViolation(
                    bug_id=bug.id,
                    title=bug.title,
                    severity=bug.severity.value,
                    days_open=days_open,
                    sla_days=sla,
                    owner=bug.owner,
                ))
        return violations

    def get_stop_ship(self, program_id: str) -> list[Bug]:
        return self._load(program_id).stop_ship

    def get_summary(self, program_id: str) -> str:
        return self._load(program_id).summary()
