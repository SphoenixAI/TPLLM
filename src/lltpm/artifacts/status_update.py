"""Status update generator — produces structured status reports."""

from __future__ import annotations

from datetime import date

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.update import StatusUpdate, UpdateSeverity

ARTIFACT_PREFIX = "status"


class StatusUpdateGenerator:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def create(
        self,
        program_id: str,
        program_name: str,
        overall_health: str = "GREEN",
        progress: list[str] | None = None,
        top_risks: list[str] | None = None,
        asks: list[str] | None = None,
        upcoming: list[str] | None = None,
    ) -> StatusUpdate:
        update = StatusUpdate(
            program_id=program_id,
            program_name=program_name,
            overall_health=UpdateSeverity(overall_health),
            progress=progress or [],
            top_risks=top_risks or [],
            asks=asks or [],
            upcoming=upcoming or [],
        )
        # Save with date-stamped name
        artifact_name = f"{ARTIFACT_PREFIX}_{update.report_date.isoformat()}"
        self._store.save(program_id, artifact_name, update)
        # Also save rendered markdown
        self._store.save_raw(program_id, artifact_name, update.render())
        return update

    def get_latest(self, program_id: str) -> StatusUpdate | None:
        artifacts = self._store.list_artifacts(program_id)
        status_artifacts = sorted(
            [a for a in artifacts if a.startswith(ARTIFACT_PREFIX)],
            reverse=True,
        )
        if not status_artifacts:
            return None
        return self._store.load(program_id, status_artifacts[0], StatusUpdate)

    def render_latest(self, program_id: str) -> str:
        update = self.get_latest(program_id)
        if update is None:
            return "No status updates found."
        return update.render()
