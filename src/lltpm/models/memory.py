"""Persistent Memory models — conversation history, context snapshots, and gap detection.

The memory system maintains:
1. Conversation turns — every interaction persisted across sessions
2. Context snapshots — periodic snapshots of program state for fast reconstruction
3. Source index — what data sources are available for gap-filling
4. Memory anchors — key facts the agent should never lose context on
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MemorySourceType(str, Enum):
    """Types of data sources the memory system can query for gap-filling."""
    CONVERSATION = "conversation"
    ARTIFACT = "artifact"
    MEETING_TRANSCRIPT = "meeting_transcript"
    MEETING_INTEL = "meeting_intel"
    DELEGATION = "delegation"
    NUDGE_LOG = "nudge_log"
    ENGAGEMENT_PROFILE = "engagement_profile"
    SCOPE_BASELINE = "scope_baseline"
    RAID_LOG = "raid_log"
    DECISION_LOG = "decision_log"
    MILESTONE_PLAN = "milestone_plan"
    STATUS_UPDATE = "status_update"
    BRIEFING = "briefing"
    CHAT_MESSAGE = "chat_message"
    PR_DIFF = "pr_diff"
    BUG_REPORT = "bug_report"
    DOCUMENT = "document"
    CALENDAR_EVENT = "calendar_event"


class ConversationTurn(BaseModel):
    """A single turn in the conversation history."""
    id: str = ""
    role: str = "user"  # user | assistant | system
    content: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    program_id: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    context_references: list[str] = Field(default_factory=list)


class ContextSnapshot(BaseModel):
    """A point-in-time snapshot of program context for fast reconstruction."""
    id: str = ""
    program_id: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    summary: str = ""
    active_milestones: list[str] = Field(default_factory=list)
    open_risks: list[str] = Field(default_factory=list)
    pending_decisions: list[str] = Field(default_factory=list)
    active_delegations: list[str] = Field(default_factory=list)
    pending_nudges: list[str] = Field(default_factory=list)
    scope_status: str = ""
    key_people: dict[str, str] = Field(default_factory=dict)
    recent_changes: list[str] = Field(default_factory=list)


class MemoryAnchor(BaseModel):
    """A key fact that must be preserved across context windows.

    Anchors are extracted from conversations when the agent detects
    critical information (names, dates, commitments, decisions).
    """
    id: str = ""
    fact: str = ""
    source: str = ""  # where this fact came from
    source_type: MemorySourceType = MemorySourceType.CONVERSATION
    program_id: str = ""
    people: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_verified: datetime = Field(default_factory=datetime.utcnow)
    confidence: float = 1.0  # 0.0 to 1.0
    tags: list[str] = Field(default_factory=list)


class GapDetection(BaseModel):
    """Represents a detected gap in the agent's memory."""
    topic: str = ""
    last_known_state: str = ""
    last_updated: datetime | None = None
    sources_to_check: list[MemorySourceType] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    priority: str = "medium"  # low | medium | high | critical


class MemoryStore(BaseModel):
    """The complete persistent memory for a program."""
    program_id: str = ""
    conversation_turns: list[ConversationTurn] = Field(default_factory=list)
    snapshots: list[ContextSnapshot] = Field(default_factory=list)
    anchors: list[MemoryAnchor] = Field(default_factory=list)
    detected_gaps: list[GapDetection] = Field(default_factory=list)
    last_snapshot_at: datetime | None = None
    total_turns: int = 0
    source_registry: dict[str, str] = Field(default_factory=dict)
