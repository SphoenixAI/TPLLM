"""Portfolio and cross-program intelligence models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PortfolioHealth(str, Enum):
    """Aggregate health across a portfolio of programs."""

    HEALTHY = "HEALTHY"  # all programs green
    AT_RISK = "AT_RISK"  # some programs yellow
    CRITICAL = "CRITICAL"  # any program red


class ResourceConflict(BaseModel):
    """A person overloaded across multiple programs."""

    person: str
    programs: list[str] = Field(default_factory=list)
    total_active_items: int = 0
    per_program: dict[str, int] = Field(default_factory=dict)
    severity: str = "medium"  # low, medium, high, critical
    recommendation: str = ""

    @property
    def is_critical(self) -> bool:
        return self.total_active_items >= 10 or len(self.programs) >= 3


class SharedDependency(BaseModel):
    """A dependency shared across multiple programs."""

    dependency_id: str = ""
    title: str
    upstream_team: str = ""
    affected_programs: list[str] = Field(default_factory=list)
    status: str = "open"
    impact: str = "medium"
    recommendation: str = ""


class CrossProgramRisk(BaseModel):
    """A risk that correlates across programs."""

    id: str = ""
    title: str
    source_program: str = ""
    affected_programs: list[str] = Field(default_factory=list)
    risk_type: str = ""  # cascade, resource, dependency, timeline
    probability: float = 0.5
    impact: str = "medium"
    description: str = ""
    recommended_actions: list[str] = Field(default_factory=list)


class ProgramSnapshot(BaseModel):
    """Point-in-time snapshot of a program's state for portfolio view."""

    program_id: str
    program_name: str = ""
    health: str = "GREEN"
    milestone_progress: float = 0.0  # 0.0 to 1.0
    at_risk_milestones: int = 0
    open_risks: int = 0
    open_blockers: int = 0
    active_delegations: int = 0
    pending_decisions: int = 0
    scope_changes_pending: int = 0
    overall_velocity: str = "stable"  # improving, stable, declining
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class PortfolioView(BaseModel):
    """Aggregate view across all programs."""

    programs: list[ProgramSnapshot] = Field(default_factory=list)
    overall_health: PortfolioHealth = PortfolioHealth.HEALTHY
    resource_conflicts: list[ResourceConflict] = Field(default_factory=list)
    shared_dependencies: list[SharedDependency] = Field(default_factory=list)
    cross_program_risks: list[CrossProgramRisk] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    def summary(self) -> str:
        lines = [
            f"# Portfolio Overview ({len(self.programs)} programs)",
            f"**Overall: {self.overall_health.value}**",
            "",
        ]

        # Program health table
        lines.append("## Programs")
        for p in self.programs:
            health_icon = {"GREEN": "G", "YELLOW": "Y", "RED": "R"}.get(p.health, "?")
            lines.append(
                f"- [{health_icon}] **{p.program_name or p.program_id}** "
                f"— {p.milestone_progress:.0%} done, "
                f"{p.at_risk_milestones} at risk, "
                f"{p.open_risks} risks, "
                f"{p.pending_decisions} pending decisions"
            )

        # Resource conflicts
        if self.resource_conflicts:
            lines.append(f"\n## Resource Conflicts ({len(self.resource_conflicts)})")
            for rc in self.resource_conflicts:
                lines.append(
                    f"- **{rc.person}**: {rc.total_active_items} items across "
                    f"{', '.join(rc.programs)} — {rc.recommendation}"
                )

        # Shared dependencies
        if self.shared_dependencies:
            lines.append(f"\n## Shared Dependencies ({len(self.shared_dependencies)})")
            for sd in self.shared_dependencies:
                lines.append(
                    f"- **{sd.title}** ({sd.upstream_team}): "
                    f"affects {', '.join(sd.affected_programs)} — {sd.status}"
                )

        # Cross-program risks
        if self.cross_program_risks:
            lines.append(f"\n## Cross-Program Risks ({len(self.cross_program_risks)})")
            for risk in self.cross_program_risks:
                lines.append(
                    f"- **{risk.title}** ({risk.risk_type}): "
                    f"from {risk.source_program} → affects {', '.join(risk.affected_programs)}"
                )

        return "\n".join(lines)
