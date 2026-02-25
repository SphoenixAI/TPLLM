"""Models for Personalized Briefings — role-aware summaries.

Engineers get "your 3 items." PMs get "program health."
Execs get "risks + asks." Nobody gets noise they don't need.
"""

from __future__ import annotations

from datetime import datetime, date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RoleType(str, Enum):
    ENGINEER = "ENGINEER"
    TECH_LEAD = "TECH_LEAD"
    ENGINEERING_MANAGER = "ENGINEERING_MANAGER"
    PRODUCT_MANAGER = "PRODUCT_MANAGER"
    DESIGNER = "DESIGNER"
    EXECUTIVE = "EXECUTIVE"
    STAKEHOLDER = "STAKEHOLDER"


class BriefingFormat(str, Enum):
    FULL = "FULL"              # Everything
    EXECUTIVE = "EXECUTIVE"    # Risks, asks, blockers only
    ENGINEER = "ENGINEER"      # My items, my blockers, my deadlines
    TEAM_LEAD = "TEAM_LEAD"   # Team items, risks, decisions needed


class BriefingSection(BaseModel):
    """A single section in a personalized briefing."""

    header: str
    items: list[str] = Field(default_factory=list)
    urgency: str = "NORMAL"    # NORMAL, HIGH, CRITICAL
    action_required: bool = False


class PersonalizedBriefing(BaseModel):
    """A role-filtered briefing for one person."""

    person: str
    role: RoleType = RoleType.ENGINEER
    program_id: str = ""
    briefing_date: date = Field(default_factory=date.today)
    format: BriefingFormat = BriefingFormat.ENGINEER

    # Sections
    greeting: str = ""
    sections: list[BriefingSection] = Field(default_factory=list)
    quick_actions: list[str] = Field(default_factory=list)  # One-click actions

    created_at: datetime = Field(default_factory=datetime.utcnow)

    def render(self) -> str:
        lines = [f"# {self.person}'s Briefing — {self.briefing_date.isoformat()}"]
        if self.greeting:
            lines.append(self.greeting)

        for section in self.sections:
            urgency_tag = f" [{section.urgency}]" if section.urgency != "NORMAL" else ""
            action_tag = " (ACTION REQUIRED)" if section.action_required else ""
            lines.append(f"\n## {section.header}{urgency_tag}{action_tag}")
            for item in section.items:
                lines.append(f"- {item}")

        if self.quick_actions:
            lines.append("\n## Quick Actions")
            for i, action in enumerate(self.quick_actions, 1):
                lines.append(f"{i}. {action}")

        return "\n".join(lines)
