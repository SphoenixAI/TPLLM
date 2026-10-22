"""Meeting notes and action item tracker."""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.update import ActionItem, ActionItemStatus

ACTIONS_ARTIFACT = "action_items"


class MeetingRecord(BaseModel):
    id: str = ""
    title: str
    meeting_date: date = Field(default_factory=date.today)
    attendees: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ActionTracker(BaseModel):
    program_id: str = ""
    items: list[ActionItem] = Field(default_factory=list)

    @property
    def open_items(self) -> list[ActionItem]:
        return [i for i in self.items if i.status != ActionItemStatus.DONE]

    @property
    def overdue_items(self) -> list[ActionItem]:
        return [i for i in self.items if i.is_overdue]

    def summary(self) -> str:
        lines = [
            "# Action Tracker",
            f"Open: {len(self.open_items)} | Overdue: {len(self.overdue_items)}",
        ]
        if self.overdue_items:
            lines.append("\n## Overdue")
            for item in self.overdue_items:
                lines.append(
                    f"- **{item.description}** — Owner: {item.owner} — "
                    f"Due: {item.due_date} — Source: {item.source}"
                )
        if self.open_items:
            lines.append("\n## Open")
            for item in self.open_items:
                if not item.is_overdue:
                    due = item.due_date.isoformat() if item.due_date else "TBD"
                    lines.append(
                        f"- {item.description} — Owner: {item.owner} — Due: {due}"
                    )
        return "\n".join(lines)


class MeetingNotesManager:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def _load_tracker(self, program_id: str) -> ActionTracker:
        existing = self._store.load(program_id, ACTIONS_ARTIFACT, ActionTracker)
        if existing is None:
            existing = ActionTracker(program_id=program_id)
        return existing

    def _save_tracker(self, tracker: ActionTracker) -> None:
        self._store.save(tracker.program_id, ACTIONS_ARTIFACT, tracker)

    def record_meeting(
        self,
        program_id: str,
        title: str,
        attendees: list[str] | None = None,
        decisions: list[str] | None = None,
        action_items: list[dict] | None = None,
        notes: str = "",
    ) -> MeetingRecord:
        items = []
        tracker = self._load_tracker(program_id)
        for ai in (action_items or []):
            item = ActionItem(
                id=f"AI-{uuid4().hex[:6]}",
                description=ai.get("description", ""),
                owner=ai.get("owner", ""),
                due_date=date.fromisoformat(ai["due_date"]) if ai.get("due_date") else None,
                source=f"Meeting: {title}",
            )
            items.append(item)
            tracker.items.append(item)

        record = MeetingRecord(
            id=f"MTG-{uuid4().hex[:6]}",
            title=title,
            attendees=attendees or [],
            decisions=decisions or [],
            action_items=items,
            notes=notes,
        )

        # Save meeting record
        self._store.save(program_id, f"meeting_{record.id}", record)
        # Update action tracker
        self._save_tracker(tracker)

        return record

    def add_action_item(
        self,
        program_id: str,
        description: str,
        owner: str,
        due_date: str | None = None,
        source: str = "",
    ) -> ActionItem:
        tracker = self._load_tracker(program_id)
        item = ActionItem(
            id=f"AI-{uuid4().hex[:6]}",
            description=description,
            owner=owner,
            due_date=date.fromisoformat(due_date) if due_date else None,
            source=source,
        )
        tracker.items.append(item)
        self._save_tracker(tracker)
        return item

    def complete_action_item(self, program_id: str, item_id: str) -> bool:
        tracker = self._load_tracker(program_id)
        for item in tracker.items:
            if item.id == item_id:
                item.status = ActionItemStatus.DONE
                item.updated_at = datetime.utcnow()
                self._save_tracker(tracker)
                return True
        return False

    def get_open_items(self, program_id: str) -> list[ActionItem]:
        return self._load_tracker(program_id).open_items

    def get_overdue_items(self, program_id: str) -> list[ActionItem]:
        return self._load_tracker(program_id).overdue_items

    def get_action_summary(self, program_id: str) -> str:
        return self._load_tracker(program_id).summary()
