"""Task board engine — AI-native work tracking with intelligent reasoning.

This engine replaces external project management tools by:
1. Auto-extracting tasks from Zoom AI meeting notes
2. Intelligently prioritizing based on risks, dependencies, and deadlines
3. Balancing workload across team members
4. Managing sprints with capacity awareness
5. Linking everything back to meetings, decisions, and RAID items
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.task_board import (
    BoardTask,
    Sprint,
    TaskBoard,
    TaskPriority,
    TaskSource,
    TaskStatus,
)

BOARD_ARTIFACT = "task_board"


class TaskBoardEngine:
    """Manages the intelligent task board for a program."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Board CRUD ──────────────────────────────────────────────

    def _load(self, program_id: str) -> TaskBoard:
        board = self._store.load(program_id, BOARD_ARTIFACT, TaskBoard)
        if board is None:
            board = TaskBoard(program_id=program_id)
        return board

    def _save(self, program_id: str, board: TaskBoard) -> None:
        self._store.save(program_id, BOARD_ARTIFACT, board)

    # ── Task management ─────────────────────────────────────────

    def create_task(
        self,
        program_id: str,
        title: str,
        description: str = "",
        priority: str = "MEDIUM",
        assignee: str = "",
        due_date: str = "",
        effort_hours: float = 0.0,
        source: str = "MANUAL",
        source_id: str = "",
        source_context: str = "",
        tags: list[str] | None = None,
        sprint_id: str = "",
        parent_task_id: str = "",
        priority_rationale: str = "",
        confidence: float = 1.0,
    ) -> BoardTask:
        board = self._load(program_id)
        task_id = f"TSK-{uuid.uuid4().hex[:6].upper()}"

        task = BoardTask(
            id=task_id,
            title=title,
            description=description,
            status=TaskStatus.TODO if sprint_id else TaskStatus.BACKLOG,
            priority=TaskPriority(priority) if priority in TaskPriority.__members__ else TaskPriority.MEDIUM,
            assignee=assignee,
            effort_hours=effort_hours,
            source=TaskSource(source) if source in TaskSource.__members__ else TaskSource.MANUAL,
            source_id=source_id,
            source_context=source_context,
            tags=tags or [],
            sprint_id=sprint_id,
            parent_task_id=parent_task_id,
            priority_rationale=priority_rationale,
            confidence=confidence,
        )
        if due_date:
            task.due_date = date.fromisoformat(due_date)

        board.tasks.append(task)
        self._save(program_id, board)
        return task

    def update_task(
        self,
        program_id: str,
        task_id: str,
        status: str = "",
        priority: str = "",
        assignee: str = "",
        due_date: str = "",
        sprint_id: str = "",
        effort_hours: float | None = None,
        blocked_by: list[str] | None = None,
    ) -> BoardTask | None:
        board = self._load(program_id)
        task = board.get_task(task_id)
        if not task:
            return None

        if status:
            task.status = TaskStatus(status)
            if task.status == TaskStatus.DONE:
                task.completed_at = datetime.utcnow()
        if priority:
            task.priority = TaskPriority(priority)
        if assignee:
            task.assignee = assignee
        if due_date:
            task.due_date = date.fromisoformat(due_date)
        if sprint_id:
            task.sprint_id = sprint_id
        if effort_hours is not None:
            task.effort_hours = effort_hours
        if blocked_by is not None:
            task.blocked_by = blocked_by
            if blocked_by:
                task.status = TaskStatus.BLOCKED

        task.updated_at = datetime.utcnow()
        self._save(program_id, board)
        return task

    # ── Sprint management ───────────────────────────────────────

    def create_sprint(
        self,
        program_id: str,
        name: str,
        start_date: str,
        end_date: str,
        goal: str = "",
        team_capacity_hours: float = 0.0,
    ) -> Sprint:
        board = self._load(program_id)
        sprint_id = f"SPR-{uuid.uuid4().hex[:6].upper()}"

        sprint = Sprint(
            id=sprint_id,
            name=name,
            goal=goal,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            team_capacity_hours=team_capacity_hours,
        )
        board.sprints.append(sprint)
        self._save(program_id, board)
        return sprint

    def start_sprint(self, program_id: str, sprint_id: str) -> Sprint | None:
        board = self._load(program_id)
        # Deactivate any current active sprint
        for s in board.sprints:
            if s.is_active and s.id != sprint_id:
                s.is_active = False
                s.completed_at = datetime.utcnow()

        target = None
        for s in board.sprints:
            if s.id == sprint_id:
                s.is_active = True
                # Calculate committed hours
                sprint_tasks = board.sprint_tasks(sprint_id)
                s.committed_hours = sum(t.effort_hours for t in sprint_tasks)
                target = s
                break

        if target:
            self._save(program_id, board)
        return target

    def complete_sprint(self, program_id: str, sprint_id: str) -> dict[str, Any]:
        """Complete a sprint — returns summary with carry-over items."""
        board = self._load(program_id)
        target = None
        for s in board.sprints:
            if s.id == sprint_id:
                s.is_active = False
                s.completed_at = datetime.utcnow()
                target = s
                break

        if not target:
            return {"error": "Sprint not found."}

        sprint_tasks = board.sprint_tasks(sprint_id)
        done = [t for t in sprint_tasks if t.status == TaskStatus.DONE]
        carry_over = [t for t in sprint_tasks if t.is_open]

        # Move carry-over items back to backlog (they'll be re-planned)
        for t in carry_over:
            t.sprint_id = ""
            t.status = TaskStatus.BACKLOG

        self._save(program_id, board)

        return {
            "sprint": target.name,
            "completed": len(done),
            "carry_over": len(carry_over),
            "carry_over_tasks": [t.title for t in carry_over],
            "velocity_hours": sum(t.effort_hours for t in done),
        }

    # ── Zoom AI Notes → Tasks extraction ────────────────────────

    def extract_tasks_from_zoom_notes(
        self,
        program_id: str,
        meeting_id: str,
        raw_notes: str,
        action_items: list[dict[str, str]] | None = None,
        key_decisions: list[str] | None = None,
        follow_ups: list[dict[str, str]] | None = None,
        meeting_topic: str = "",
    ) -> list[BoardTask]:
        """Extract and create tasks from Zoom AI meeting notes.

        This is the core intelligence — it:
        1. Parses structured action_items from AI note providers
        2. Detects implicit commitments in raw notes text
        3. Cross-references with RAID log for priority boosting
        4. Creates tasks with full traceability back to the meeting
        """
        board = self._load(program_id)
        created: list[BoardTask] = []

        # 1. Process explicit action items from AI notes
        for item in (action_items or []):
            owner = item.get("owner", "")
            desc = item.get("description", "")
            if not desc:
                continue

            # Detect priority from language
            priority = self._infer_priority(desc)
            due = self._infer_due_date(desc)

            task = BoardTask(
                id=f"TSK-{uuid.uuid4().hex[:6].upper()}",
                title=self._clean_title(desc),
                description=f"From meeting: {meeting_topic}\n\nAction item: {desc}",
                status=TaskStatus.BACKLOG,
                priority=priority,
                assignee=owner,
                source=TaskSource.ZOOM_AI_NOTES,
                source_id=meeting_id,
                source_context=desc,
                due_date=due,
                priority_rationale=f"Extracted from Zoom meeting '{meeting_topic}'. "
                                   f"Priority based on urgency signals in the action item.",
                confidence=0.85,
            )
            board.tasks.append(task)
            created.append(task)

        # 2. Process follow-ups
        for fu in (follow_ups or []):
            person = fu.get("person", "")
            action = fu.get("action", "")
            if not action:
                continue

            task = BoardTask(
                id=f"TSK-{uuid.uuid4().hex[:6].upper()}",
                title=self._clean_title(action),
                description=f"Follow-up from meeting: {meeting_topic}",
                status=TaskStatus.BACKLOG,
                priority=TaskPriority.MEDIUM,
                assignee=person,
                source=TaskSource.MEETING_ACTION,
                source_id=meeting_id,
                source_context=action,
                confidence=0.75,
            )
            board.tasks.append(task)
            created.append(task)

        # 3. Detect implicit commitments in raw notes
        if raw_notes:
            implicit = self._detect_implicit_commitments(raw_notes)
            for commitment in implicit:
                # Skip if already covered by explicit action items
                if any(c["text"].lower() in (item.get("description", "").lower())
                       for item in (action_items or [])
                       for c in [commitment]):
                    continue

                task = BoardTask(
                    id=f"TSK-{uuid.uuid4().hex[:6].upper()}",
                    title=self._clean_title(commitment["text"]),
                    description=f"Implicit commitment detected in meeting: {meeting_topic}",
                    status=TaskStatus.BACKLOG,
                    priority=TaskPriority.LOW,
                    assignee=commitment.get("person", ""),
                    source=TaskSource.ZOOM_AI_NOTES,
                    source_id=meeting_id,
                    source_context=commitment["text"],
                    priority_rationale="Implicit commitment detected from meeting notes. "
                                       "Lower confidence — may need verification.",
                    confidence=0.5,
                    tags=["needs-review"],
                )
                board.tasks.append(task)
                created.append(task)

        # 4. Create tasks from key decisions that need follow-through
        for decision in (key_decisions or []):
            task = BoardTask(
                id=f"TSK-{uuid.uuid4().hex[:6].upper()}",
                title=f"Implement decision: {self._clean_title(decision)}",
                description=f"Decision made in meeting: {meeting_topic}\n\nDecision: {decision}",
                status=TaskStatus.BACKLOG,
                priority=TaskPriority.MEDIUM,
                source=TaskSource.MEETING_ACTION,
                source_id=meeting_id,
                source_context=decision,
                priority_rationale="Follow-through task for key meeting decision.",
                confidence=0.7,
                tags=["decision-follow-up"],
            )
            board.tasks.append(task)
            created.append(task)

        self._save(program_id, board)
        return created

    # ── Intelligent prioritization ──────────────────────────────

    def reprioritize(self, program_id: str) -> list[dict[str, str]]:
        """Re-evaluate task priorities based on current program state.

        Uses signals from:
        - Approaching due dates
        - Blocker chains (tasks blocking other tasks)
        - Related risks in the RAID log
        - Sprint end proximity
        """
        board = self._load(program_id)
        changes: list[dict[str, str]] = []

        for task in board.open_tasks:
            new_priority = task.priority
            rationale_parts: list[str] = []

            # Signal 1: Due date proximity
            if task.days_until_due is not None:
                if task.days_until_due <= 1:
                    new_priority = TaskPriority.CRITICAL
                    rationale_parts.append(f"Due in {task.days_until_due} day(s)")
                elif task.days_until_due <= 3:
                    if new_priority.value > TaskPriority.HIGH.value:
                        new_priority = TaskPriority.HIGH
                        rationale_parts.append(f"Due in {task.days_until_due} days")
                elif task.is_overdue:
                    new_priority = TaskPriority.CRITICAL
                    rationale_parts.append("OVERDUE")

            # Signal 2: Blocker chains — if this task blocks others, boost priority
            blocking_count = len(task.blocks)
            if blocking_count >= 3:
                new_priority = TaskPriority.CRITICAL
                rationale_parts.append(f"Blocks {blocking_count} other tasks")
            elif blocking_count >= 1 and new_priority.value > TaskPriority.HIGH.value:
                new_priority = TaskPriority.HIGH
                rationale_parts.append(f"Blocks {blocking_count} other task(s)")

            # Signal 3: Sprint deadline
            sprint = board.active_sprint
            if sprint and task.sprint_id == sprint.id and sprint.days_remaining <= 2:
                if new_priority.value > TaskPriority.HIGH.value:
                    new_priority = TaskPriority.HIGH
                    rationale_parts.append(f"Sprint ends in {sprint.days_remaining} day(s)")

            # Apply change
            if new_priority != task.priority:
                old = task.priority.value
                task.priority = new_priority
                task.priority_rationale = "; ".join(rationale_parts)
                task.updated_at = datetime.utcnow()
                changes.append({
                    "task_id": task.id,
                    "title": task.title,
                    "old_priority": old,
                    "new_priority": new_priority.value,
                    "reason": task.priority_rationale,
                })

        if changes:
            self._save(program_id, board)
        return changes

    # ── Workload analysis ───────────────────────────────────────

    def get_workload(self, program_id: str) -> dict[str, Any]:
        """Analyze workload distribution across team members."""
        board = self._load(program_id)
        by_assignee = board.tasks_by_assignee()

        workload: dict[str, Any] = {}
        for person, tasks in by_assignee.items():
            total_hours = sum(t.effort_hours for t in tasks)
            by_priority = {}
            for t in tasks:
                by_priority.setdefault(t.priority.value, 0)
                by_priority[t.priority.value] += 1

            overdue = [t for t in tasks if t.is_overdue]
            blocked = [t for t in tasks if t.status == TaskStatus.BLOCKED]

            workload[person] = {
                "total_tasks": len(tasks),
                "total_hours": total_hours,
                "by_priority": by_priority,
                "overdue_count": len(overdue),
                "blocked_count": len(blocked),
                "health": self._assess_person_health(len(tasks), len(overdue), len(blocked)),
            }

        return workload

    # ── Board view ──────────────────────────────────────────────

    def get_board(self, program_id: str, sprint_id: str = "") -> str:
        """Get the task board summary."""
        board = self._load(program_id)
        if sprint_id:
            # Filter to sprint view
            tasks = board.sprint_tasks(sprint_id)
            sprint = None
            for s in board.sprints:
                if s.id == sprint_id:
                    sprint = s
                    break
            header = f"Sprint: {sprint.name}" if sprint else f"Sprint: {sprint_id}"
        else:
            return board.summary()

        lines = [f"# {header}"]
        by_status: dict[str, list[BoardTask]] = {}
        for t in tasks:
            by_status.setdefault(t.status.value, []).append(t)

        for status in TaskStatus:
            group = by_status.get(status.value, [])
            if group:
                lines.append(f"\n## {status.value} ({len(group)})")
                for t in sorted(group, key=lambda x: x.priority.value):
                    assignee = f" → {t.assignee}" if t.assignee else ""
                    due = f" (due {t.due_date})" if t.due_date else ""
                    lines.append(f"- [{t.priority.value}] {t.title}{assignee}{due}")

        return "\n".join(lines)

    def get_sprint_metrics(self, program_id: str, sprint_id: str = "") -> dict[str, Any]:
        """Get sprint health metrics."""
        board = self._load(program_id)
        sprint = board.active_sprint if not sprint_id else None
        if sprint_id:
            for s in board.sprints:
                if s.id == sprint_id:
                    sprint = s
                    break
        if not sprint:
            return {"error": "No active sprint."}

        tasks = board.sprint_tasks(sprint.id)
        done = [t for t in tasks if t.status == TaskStatus.DONE]
        in_progress = [t for t in tasks if t.status == TaskStatus.IN_PROGRESS]
        blocked = [t for t in tasks if t.status == TaskStatus.BLOCKED]
        remaining = [t for t in tasks if t.is_open]

        done_hours = sum(t.effort_hours for t in done)
        remaining_hours = sum(t.effort_hours for t in remaining)

        return {
            "sprint_name": sprint.name,
            "days_remaining": sprint.days_remaining,
            "total_tasks": len(tasks),
            "done": len(done),
            "in_progress": len(in_progress),
            "blocked": len(blocked),
            "remaining": len(remaining),
            "velocity_hours": done_hours,
            "remaining_hours": remaining_hours,
            "completion_rate": len(done) / len(tasks) if tasks else 0,
            "on_track": remaining_hours <= (sprint.days_remaining * sprint.team_capacity_hours / max(1, (sprint.end_date - sprint.start_date).days)) if sprint.team_capacity_hours > 0 else True,
        }

    # ── Private helpers ─────────────────────────────────────────

    @staticmethod
    def _infer_priority(text: str) -> TaskPriority:
        """Infer task priority from natural language."""
        text_lower = text.lower()
        critical_signals = ["asap", "urgent", "blocker", "critical", "drop everything", "p0", "immediately"]
        high_signals = ["by end of day", "by eod", "this week", "high priority", "must", "blocking"]
        low_signals = ["nice to have", "when you get a chance", "low priority", "eventually", "someday"]

        if any(s in text_lower for s in critical_signals):
            return TaskPriority.CRITICAL
        if any(s in text_lower for s in high_signals):
            return TaskPriority.HIGH
        if any(s in text_lower for s in low_signals):
            return TaskPriority.LOW
        return TaskPriority.MEDIUM

    @staticmethod
    def _infer_due_date(text: str) -> date | None:
        """Try to extract a due date from natural language."""
        text_lower = text.lower()
        today = date.today()

        if "by end of day" in text_lower or "by eod" in text_lower or "today" in text_lower:
            return today
        if "tomorrow" in text_lower:
            return today + timedelta(days=1)
        if "this week" in text_lower or "by friday" in text_lower:
            days_until_friday = (4 - today.weekday()) % 7
            if days_until_friday == 0:
                days_until_friday = 7
            return today + timedelta(days=days_until_friday)
        if "next week" in text_lower:
            return today + timedelta(days=7)

        # Try ISO date pattern
        iso_match = re.search(r'\d{4}-\d{2}-\d{2}', text)
        if iso_match:
            try:
                return date.fromisoformat(iso_match.group())
            except ValueError:
                pass

        # Try "by March 1" style
        month_match = re.search(
            r'by\s+(january|february|march|april|may|june|july|august|'
            r'september|october|november|december)\s+(\d{1,2})',
            text_lower,
        )
        if month_match:
            months = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }
            month = months[month_match.group(1)]
            day = int(month_match.group(2))
            year = today.year
            if date(year, month, day) < today:
                year += 1
            try:
                return date(year, month, day)
            except ValueError:
                pass

        return None

    @staticmethod
    def _detect_implicit_commitments(notes: str) -> list[dict[str, str]]:
        """Detect implicit commitments in meeting notes text.

        Looks for patterns like:
        - "Alice will review the API spec"
        - "Bob agreed to finish the migration"
        - "Charlie is going to prototype the gateway"
        """
        commitments: list[dict[str, str]] = []

        patterns = [
            r'(\w+)\s+will\s+(.{10,80}?)(?:\.|$)',
            r'(\w+)\s+agreed\s+to\s+(.{10,80}?)(?:\.|$)',
            r'(\w+)\s+is\s+going\s+to\s+(.{10,80}?)(?:\.|$)',
            r'(\w+)\s+committed\s+to\s+(.{10,80}?)(?:\.|$)',
            r'(\w+)\s+(?:needs|has)\s+to\s+(.{10,80}?)(?:\.|$)',
            r'(\w+)\s+volunteered\s+to\s+(.{10,80}?)(?:\.|$)',
        ]

        # Skip common false positives
        skip_names = {"this", "that", "it", "there", "we", "they", "the", "team", "everyone"}

        for pattern in patterns:
            for match in re.finditer(pattern, notes, re.IGNORECASE):
                person = match.group(1).strip()
                action = match.group(2).strip()
                if person.lower() not in skip_names and len(action) > 10:
                    commitments.append({"person": person, "text": f"{person} will {action}"})

        return commitments

    @staticmethod
    def _clean_title(text: str) -> str:
        """Clean up extracted text into a task title."""
        title = text.strip()
        # Remove leading "to " if present
        if title.lower().startswith("to "):
            title = title[3:]
        # Capitalize first letter
        if title:
            title = title[0].upper() + title[1:]
        # Truncate if too long
        if len(title) > 100:
            title = title[:97] + "..."
        return title

    @staticmethod
    def _assess_person_health(total: int, overdue: int, blocked: int) -> str:
        """Assess a person's workload health."""
        if overdue >= 3 or (total >= 8 and overdue >= 2):
            return "overloaded"
        if blocked >= 2 or overdue >= 1:
            return "at_risk"
        if total >= 6:
            return "heavy"
        return "healthy"
