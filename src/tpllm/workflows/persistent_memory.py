"""Persistent Memory Engine — never lose context across sessions.

Core capabilities:
1. Conversation persistence — every turn saved and retrievable
2. Context snapshots — periodic summaries of program state
3. Memory anchors — key facts extracted and pinned
4. Gap detection — identifies missing context and queries data sources
5. Context reconstruction — rebuilds full context from all available sources
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.memory import (
    ContextSnapshot,
    ConversationTurn,
    GapDetection,
    MemoryAnchor,
    MemorySourceType,
    MemoryStore,
)

MEMORY_ARTIFACT = "persistent_memory"
SNAPSHOT_INTERVAL_TURNS = 10  # take a snapshot every N turns


class PersistentMemoryEngine:
    """Persistent memory with gap-filling from all data sources."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Conversation persistence ─────────────────────────────

    def record_turn(
        self,
        program_id: str,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
        context_references: list[str] | None = None,
    ) -> ConversationTurn:
        """Record a conversation turn to persistent memory."""
        memory = self._load_memory(program_id)

        turn = ConversationTurn(
            id=f"TURN-{uuid.uuid4().hex[:8]}",
            role=role,
            content=content,
            program_id=program_id,
            tool_calls=tool_calls or [],
            context_references=context_references or [],
        )

        memory.conversation_turns.append(turn)
        memory.total_turns += 1

        # Auto-snapshot periodically
        if memory.total_turns % SNAPSHOT_INTERVAL_TURNS == 0:
            self._auto_snapshot(memory, program_id)

        self._save_memory(program_id, memory)
        return turn

    def get_recent_turns(
        self,
        program_id: str,
        limit: int = 20,
    ) -> list[ConversationTurn]:
        """Get the most recent conversation turns."""
        memory = self._load_memory(program_id)
        return memory.conversation_turns[-limit:]

    def search_turns(
        self,
        program_id: str,
        query: str,
        limit: int = 10,
    ) -> list[ConversationTurn]:
        """Search conversation history for relevant turns."""
        memory = self._load_memory(program_id)
        query_lower = query.lower()
        matches = []
        for turn in reversed(memory.conversation_turns):
            if query_lower in turn.content.lower():
                matches.append(turn)
                if len(matches) >= limit:
                    break
        return matches

    # ── Context snapshots ─────────────────────────────────────

    def take_snapshot(
        self,
        program_id: str,
        summary: str = "",
        active_milestones: list[str] | None = None,
        open_risks: list[str] | None = None,
        pending_decisions: list[str] | None = None,
        active_delegations: list[str] | None = None,
        pending_nudges: list[str] | None = None,
        scope_status: str = "",
        key_people: dict[str, str] | None = None,
    ) -> ContextSnapshot:
        """Take a manual context snapshot of the program state."""
        memory = self._load_memory(program_id)

        snapshot = ContextSnapshot(
            id=f"SNAP-{uuid.uuid4().hex[:8]}",
            program_id=program_id,
            summary=summary,
            active_milestones=active_milestones or [],
            open_risks=open_risks or [],
            pending_decisions=pending_decisions or [],
            active_delegations=active_delegations or [],
            pending_nudges=pending_nudges or [],
            scope_status=scope_status,
            key_people=key_people or {},
        )

        memory.snapshots.append(snapshot)
        memory.last_snapshot_at = snapshot.timestamp
        self._save_memory(program_id, memory)
        return snapshot

    def get_latest_snapshot(self, program_id: str) -> ContextSnapshot | None:
        """Get the most recent context snapshot."""
        memory = self._load_memory(program_id)
        if memory.snapshots:
            return memory.snapshots[-1]
        return None

    # ── Memory anchors ────────────────────────────────────────

    def add_anchor(
        self,
        program_id: str,
        fact: str,
        source: str = "",
        source_type: str = "conversation",
        people: list[str] | None = None,
        tags: list[str] | None = None,
        confidence: float = 1.0,
    ) -> MemoryAnchor:
        """Pin a key fact that must be preserved across context windows."""
        memory = self._load_memory(program_id)

        try:
            st = MemorySourceType(source_type)
        except ValueError:
            st = MemorySourceType.CONVERSATION

        anchor = MemoryAnchor(
            id=f"ANC-{uuid.uuid4().hex[:8]}",
            fact=fact,
            source=source,
            source_type=st,
            program_id=program_id,
            people=people or [],
            confidence=confidence,
            tags=tags or [],
        )

        memory.anchors.append(anchor)
        self._save_memory(program_id, memory)
        return anchor

    def get_anchors(
        self,
        program_id: str,
        person: str = "",
        tag: str = "",
    ) -> list[MemoryAnchor]:
        """Get memory anchors, optionally filtered by person or tag."""
        memory = self._load_memory(program_id)
        anchors = memory.anchors
        if person:
            anchors = [a for a in anchors if person in a.people]
        if tag:
            anchors = [a for a in anchors if tag in a.tags]
        return anchors

    def verify_anchor(self, program_id: str, anchor_id: str) -> bool:
        """Mark an anchor as recently verified."""
        memory = self._load_memory(program_id)
        for anchor in memory.anchors:
            if anchor.id == anchor_id:
                anchor.last_verified = datetime.utcnow()
                self._save_memory(program_id, memory)
                return True
        return False

    # ── Gap detection ─────────────────────────────────────────

    def detect_gaps(self, program_id: str) -> list[GapDetection]:
        """Detect gaps in the agent's memory by checking all data sources.

        Returns a list of gaps with suggested sources to check.
        """
        memory = self._load_memory(program_id)
        gaps: list[GapDetection] = []

        # Check if we have stale snapshots
        if memory.last_snapshot_at:
            hours_since = (datetime.utcnow() - memory.last_snapshot_at).total_seconds() / 3600
            if hours_since > 24:
                gaps.append(GapDetection(
                    topic="Program state snapshot is stale",
                    last_known_state=f"Last snapshot {hours_since:.0f}h ago",
                    last_updated=memory.last_snapshot_at,
                    sources_to_check=[
                        MemorySourceType.MILESTONE_PLAN,
                        MemorySourceType.RAID_LOG,
                        MemorySourceType.DELEGATION,
                        MemorySourceType.STATUS_UPDATE,
                    ],
                    questions=["What has changed since the last snapshot?"],
                    priority="high",
                ))

        # Check for anchors that haven't been verified recently
        stale_anchors = [
            a for a in memory.anchors
            if (datetime.utcnow() - a.last_verified).total_seconds() > 72 * 3600
        ]
        if stale_anchors:
            gaps.append(GapDetection(
                topic="Stale memory anchors need verification",
                last_known_state=f"{len(stale_anchors)} anchors unverified >72h",
                sources_to_check=[a.source_type for a in stale_anchors[:5]],
                questions=[f"Is '{a.fact}' still accurate?" for a in stale_anchors[:3]],
                priority="medium",
            ))

        # Check what artifacts exist and flag missing ones
        existing = set(self._store.list_artifacts(program_id))
        expected = {
            "raid_log", "milestone_plan", "decision_log",
            "delegation_plan", "scope_baseline",
        }
        missing = expected - existing
        if missing:
            source_map = {
                "raid_log": MemorySourceType.RAID_LOG,
                "milestone_plan": MemorySourceType.MILESTONE_PLAN,
                "decision_log": MemorySourceType.DECISION_LOG,
                "delegation_plan": MemorySourceType.DELEGATION,
                "scope_baseline": MemorySourceType.SCOPE_BASELINE,
            }
            gaps.append(GapDetection(
                topic="Missing program artifacts",
                last_known_state=f"Missing: {', '.join(missing)}",
                sources_to_check=[source_map.get(m, MemorySourceType.ARTIFACT) for m in missing],
                questions=[f"Should {m} be created?" for m in missing],
                priority="medium",
            ))

        memory.detected_gaps = gaps
        self._save_memory(program_id, memory)
        return gaps

    def fill_gap(
        self,
        program_id: str,
        topic: str,
        resolution: str,
        source_type: str = "artifact",
    ) -> bool:
        """Record that a gap has been filled."""
        memory = self._load_memory(program_id)
        filled = False
        for gap in memory.detected_gaps:
            if gap.topic == topic:
                memory.detected_gaps.remove(gap)
                filled = True
                break
        self._save_memory(program_id, memory)
        if filled:
            # Create an anchor from the resolution (after saving gap removal)
            self.add_anchor(
                program_id,
                fact=f"Gap resolved: {topic} → {resolution}",
                source=source_type,
                tags=["gap_filled"],
            )
        return filled

    # ── Context reconstruction ────────────────────────────────

    def reconstruct_context(self, program_id: str) -> str:
        """Reconstruct full program context from all available sources.

        This is used when the agent starts a new session or when
        context window compression has lost important details.
        """
        sections: list[str] = []

        # 1. Latest snapshot
        snapshot = self.get_latest_snapshot(program_id)
        if snapshot:
            sections.append(f"## Last Known State ({snapshot.timestamp.strftime('%Y-%m-%d %H:%M')})")
            sections.append(snapshot.summary)
            if snapshot.active_milestones:
                sections.append(f"Active milestones: {', '.join(snapshot.active_milestones)}")
            if snapshot.open_risks:
                sections.append(f"Open risks: {', '.join(snapshot.open_risks)}")
            if snapshot.pending_decisions:
                sections.append(f"Pending decisions: {', '.join(snapshot.pending_decisions)}")
            if snapshot.key_people:
                people_str = ", ".join(f"{k}: {v}" for k, v in snapshot.key_people.items())
                sections.append(f"Key people: {people_str}")

        # 2. Memory anchors (key facts)
        anchors = self.get_anchors(program_id)
        if anchors:
            sections.append("\n## Key Facts (Memory Anchors)")
            for anchor in sorted(anchors, key=lambda a: a.created_at, reverse=True)[:20]:
                sections.append(f"- {anchor.fact} (from {anchor.source_type.value})")

        # 3. Scan existing artifacts for latest state
        artifacts = self._store.list_artifacts(program_id)
        if artifacts:
            sections.append(f"\n## Available Artifacts")
            sections.append(f"Program has {len(artifacts)} artifacts: {', '.join(sorted(artifacts))}")

        # 4. Recent conversation context
        recent = self.get_recent_turns(program_id, limit=5)
        if recent:
            sections.append("\n## Recent Conversation")
            for turn in recent:
                preview = turn.content[:200] + ("..." if len(turn.content) > 200 else "")
                sections.append(f"- [{turn.role}] {preview}")

        # 5. Detected gaps
        gaps = self.detect_gaps(program_id)
        if gaps:
            sections.append("\n## Detected Gaps (Need Attention)")
            for gap in gaps:
                sections.append(
                    f"- **{gap.topic}** ({gap.priority}): "
                    f"Check {', '.join(s.value for s in gap.sources_to_check)}"
                )

        return "\n".join(sections) if sections else "No context available for this program."

    def get_summary(self, program_id: str) -> str:
        """Get a concise summary of the memory state."""
        memory = self._load_memory(program_id)
        lines = [
            "# Persistent Memory Summary",
            f"Total conversation turns: {memory.total_turns}",
            f"Context snapshots: {len(memory.snapshots)}",
            f"Memory anchors: {len(memory.anchors)}",
            f"Detected gaps: {len(memory.detected_gaps)}",
        ]
        if memory.last_snapshot_at:
            lines.append(f"Last snapshot: {memory.last_snapshot_at.strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)

    # ── Auto-snapshot ─────────────────────────────────────────

    def _auto_snapshot(self, memory: MemoryStore, program_id: str) -> None:
        """Take an automatic snapshot from recent conversation context."""
        recent = memory.conversation_turns[-SNAPSHOT_INTERVAL_TURNS:]
        summary_parts = []
        for turn in recent:
            if turn.role == "assistant" and turn.content:
                summary_parts.append(turn.content[:100])

        snapshot = ContextSnapshot(
            id=f"SNAP-AUTO-{uuid.uuid4().hex[:8]}",
            program_id=program_id,
            summary=f"Auto-snapshot at turn {memory.total_turns}: " +
                    "; ".join(summary_parts[:3]),
            recent_changes=[t.content[:80] for t in recent if t.role == "user"],
        )
        memory.snapshots.append(snapshot)
        memory.last_snapshot_at = snapshot.timestamp

    # ── Persistence ───────────────────────────────────────────

    def _load_memory(self, program_id: str) -> MemoryStore:
        raw = self._store.load_raw(program_id, MEMORY_ARTIFACT)
        if raw is None:
            return MemoryStore(program_id=program_id)
        try:
            data = json.loads(raw)
            return MemoryStore.model_validate(data)
        except (json.JSONDecodeError, TypeError):
            return MemoryStore(program_id=program_id)

    def _save_memory(self, program_id: str, memory: MemoryStore) -> None:
        self._store.save_raw(
            program_id,
            MEMORY_ARTIFACT,
            json.dumps(memory.model_dump(), indent=2, default=str),
        )
