"""RAID log management — create, update, query."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.risk import (
    Assumption,
    Dependency,
    Issue,
    RAIDItemStatus,
    RAIDLog,
    Probability,
    Risk,
    Severity,
)

ARTIFACT_NAME = "raid_log"


class RAIDLogManager:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def _load(self, program_id: str) -> RAIDLog:
        existing = self._store.load(program_id, ARTIFACT_NAME, RAIDLog)
        if existing is None:
            existing = RAIDLog(program_id=program_id)
        return existing

    def _save(self, log: RAIDLog) -> None:
        self._store.save(log.program_id, ARTIFACT_NAME, log)

    # ── Risks ──────────────────────────────────────────────────

    def add_risk(
        self,
        program_id: str,
        title: str,
        description: str = "",
        severity: str = "MEDIUM",
        probability: str = "MEDIUM",
        impact: str = "",
        mitigation: str = "",
        owner: str = "",
    ) -> Risk:
        log = self._load(program_id)
        risk = Risk(
            id=f"R-{uuid4().hex[:6]}",
            title=title,
            description=description,
            severity=Severity(severity),
            probability=Probability(probability),
            impact=impact,
            mitigation=mitigation,
            owner=owner,
        )
        log.risks.append(risk)
        self._save(log)
        return risk

    def resolve_risk(self, program_id: str, risk_id: str, resolution: str = "") -> bool:
        log = self._load(program_id)
        for r in log.risks:
            if r.id == risk_id:
                r.status = RAIDItemStatus.RESOLVED
                r.mitigation = resolution or r.mitigation
                r.updated_at = datetime.utcnow()
                self._save(log)
                return True
        return False

    # ── Issues ─────────────────────────────────────────────────

    def add_issue(
        self,
        program_id: str,
        title: str,
        description: str = "",
        severity: str = "MEDIUM",
        impact: str = "",
        owner: str = "",
    ) -> Issue:
        log = self._load(program_id)
        issue = Issue(
            id=f"I-{uuid4().hex[:6]}",
            title=title,
            description=description,
            severity=Severity(severity),
            impact=impact,
            owner=owner,
        )
        log.issues.append(issue)
        self._save(log)
        return issue

    # ── Dependencies ───────────────────────────────────────────

    def add_dependency(
        self,
        program_id: str,
        title: str,
        upstream_team: str = "",
        downstream_team: str = "",
        owner: str = "",
        commitment_date: str | None = None,
    ) -> Dependency:
        from datetime import date as _date

        log = self._load(program_id)
        dep = Dependency(
            id=f"D-{uuid4().hex[:6]}",
            title=title,
            upstream_team=upstream_team,
            downstream_team=downstream_team,
            owner=owner,
            commitment_date=_date.fromisoformat(commitment_date) if commitment_date else None,
        )
        log.dependencies.append(dep)
        self._save(log)
        return dep

    # ── Assumptions ────────────────────────────────────────────

    def add_assumption(
        self,
        program_id: str,
        title: str,
        description: str = "",
        owner: str = "",
    ) -> Assumption:
        log = self._load(program_id)
        assumption = Assumption(
            id=f"A-{uuid4().hex[:6]}",
            title=title,
            description=description,
            owner=owner,
        )
        log.assumptions.append(assumption)
        self._save(log)
        return assumption

    # ── Queries ────────────────────────────────────────────────

    def get_log(self, program_id: str) -> RAIDLog:
        return self._load(program_id)

    def get_summary(self, program_id: str) -> str:
        return self._load(program_id).summary()
