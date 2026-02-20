"""Program brief (one-pager) generator."""

from __future__ import annotations

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.program import Program

ARTIFACT_NAME = "program"


class ProgramBriefGenerator:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def create_program(
        self,
        program_id: str,
        name: str,
        goal: str,
        non_goals: list[str] | None = None,
        target_date: str | None = None,
        stakeholders: list[dict] | None = None,
        success_metrics: list[dict] | None = None,
    ) -> Program:
        from datetime import date as _date
        from lltpm.models.program import Stakeholder, SuccessMetric

        program = Program(
            id=program_id,
            name=name,
            goal=goal,
            non_goals=non_goals or [],
            target_date=_date.fromisoformat(target_date) if target_date else None,
            stakeholders=[Stakeholder(**s) for s in (stakeholders or [])],
            success_metrics=[SuccessMetric(**m) for m in (success_metrics or [])],
        )
        self._store.save(program_id, ARTIFACT_NAME, program)
        self._store.save_raw(program_id, "brief", program.summary())
        return program

    def get_program(self, program_id: str) -> Program | None:
        return self._store.load(program_id, ARTIFACT_NAME, Program)

    def update_health(self, program_id: str, health: str) -> bool:
        from lltpm.models.program import ProgramHealth
        program = self.get_program(program_id)
        if program is None:
            return False
        program.health = ProgramHealth(health)
        self._store.save(program_id, ARTIFACT_NAME, program)
        return True

    def get_brief(self, program_id: str) -> str:
        program = self.get_program(program_id)
        if program is None:
            return "Program not found."
        return program.summary()
