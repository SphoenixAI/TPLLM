"""Decision log management."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.decision import Decision, DecisionLog, DecisionOption, DecisionStatus

ARTIFACT_NAME = "decision_log"


class DecisionLogManager:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def _load(self, program_id: str) -> DecisionLog:
        existing = self._store.load(program_id, ARTIFACT_NAME, DecisionLog)
        if existing is None:
            existing = DecisionLog(program_id=program_id)
        return existing

    def _save(self, log: DecisionLog) -> None:
        self._store.save(log.program_id, ARTIFACT_NAME, log)

    def propose_decision(
        self,
        program_id: str,
        title: str,
        context: str = "",
        options: list[dict] | None = None,
    ) -> Decision:
        log = self._load(program_id)
        opts = [DecisionOption(**o) for o in (options or [])]
        decision = Decision(
            id=f"DEC-{uuid4().hex[:6]}",
            title=title,
            context=context,
            options=opts,
        )
        log.decisions.append(decision)
        self._save(log)
        return decision

    def record_decision(
        self,
        program_id: str,
        decision_id: str,
        decision_text: str,
        decided_by: str,
        rationale: str = "",
        follow_ups: list[str] | None = None,
    ) -> bool:
        log = self._load(program_id)
        for d in log.decisions:
            if d.id == decision_id:
                d.decision = decision_text
                d.decided_by = decided_by
                d.rationale = rationale
                d.follow_ups = follow_ups or []
                d.status = DecisionStatus.DECIDED
                d.decided_at = datetime.utcnow()
                self._save(log)
                return True
        return False

    def get_log(self, program_id: str) -> DecisionLog:
        return self._load(program_id)

    def get_summary(self, program_id: str) -> str:
        return self._load(program_id).summary()

    def get_pending(self, program_id: str) -> list[Decision]:
        return self._load(program_id).pending
