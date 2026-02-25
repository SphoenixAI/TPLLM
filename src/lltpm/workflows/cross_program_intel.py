"""Cross-Program Intelligence — portfolio-level analysis and risk correlation.

Analyzes across all programs to detect:
- Resource conflicts (same person overloaded across programs)
- Shared dependencies (same team blocking multiple programs)
- Cascading risks (one program's slip affects another)
- Portfolio-level health aggregation
"""

from __future__ import annotations

from datetime import datetime

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.portfolio import (
    CrossProgramRisk,
    PortfolioHealth,
    PortfolioView,
    ProgramSnapshot,
    ResourceConflict,
    SharedDependency,
)


class CrossProgramIntel:
    """Portfolio-level intelligence across all programs."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def generate_portfolio_view(self) -> PortfolioView:
        """Generate a comprehensive portfolio view across all programs."""
        program_ids = self._store.list_programs()
        snapshots = [self._snapshot_program(pid) for pid in program_ids]

        conflicts = self._detect_resource_conflicts(program_ids)
        shared_deps = self._detect_shared_dependencies(program_ids)
        cross_risks = self._detect_cross_program_risks(program_ids, snapshots)

        # Determine overall health
        health = PortfolioHealth.HEALTHY
        if any(s.health == "RED" for s in snapshots):
            health = PortfolioHealth.CRITICAL
        elif any(s.health == "YELLOW" for s in snapshots) or conflicts or cross_risks:
            health = PortfolioHealth.AT_RISK

        view = PortfolioView(
            programs=snapshots,
            overall_health=health,
            resource_conflicts=conflicts,
            shared_dependencies=shared_deps,
            cross_program_risks=cross_risks,
        )

        # Persist
        self._store.save_raw(
            "_system", "portfolio_view",
            view.model_dump_json(indent=2),
        )

        return view

    def get_portfolio_summary(self) -> str:
        """Get a human-readable portfolio summary."""
        view = self.generate_portfolio_view()
        return view.summary()

    def get_resource_conflicts(self) -> list[ResourceConflict]:
        """Get just the resource conflicts across programs."""
        program_ids = self._store.list_programs()
        return self._detect_resource_conflicts(program_ids)

    def get_shared_dependencies(self) -> list[SharedDependency]:
        """Get shared dependencies across programs."""
        program_ids = self._store.list_programs()
        return self._detect_shared_dependencies(program_ids)

    # ── Internal Analysis Methods ─────────────────────────────

    def _snapshot_program(self, program_id: str) -> ProgramSnapshot:
        """Create a point-in-time snapshot of a program."""
        from lltpm.models.program import Program
        from lltpm.models.milestone import MilestonePlan
        from lltpm.models.risk import RAIDLog
        from lltpm.models.decision import DecisionLog
        from lltpm.models.delegation import DelegationPlan
        from lltpm.models.scope import ScopeBaseline

        program = self._store.load(program_id, "program", Program)
        name = program.name if program else program_id

        # Milestones
        plan = self._store.load(program_id, "milestone_plan", MilestonePlan)
        total_ms = len(plan.milestones) if plan else 0
        completed_ms = (
            len([m for m in plan.milestones if m.status.value == "COMPLETED"])
            if plan else 0
        )
        at_risk_ms = (
            len([m for m in plan.milestones if m.status.value == "AT_RISK"])
            if plan else 0
        )

        # RAID
        raid = self._store.load(program_id, "raid_log", RAIDLog)
        open_risks = (
            len([r for r in raid.risks if r.status.value == "OPEN"])
            if raid else 0
        )

        # Decisions
        log = self._store.load(program_id, "decision_log", DecisionLog)
        pending_decisions = (
            len([d for d in log.decisions if d.status.value != "DECIDED"])
            if log else 0
        )

        # Delegations
        delegation = self._store.load(program_id, "delegation_plan", DelegationPlan)
        active_delegations = len(delegation.open_items) if delegation else 0

        # Scope
        scope = self._store.load(program_id, "scope_baseline", ScopeBaseline)
        scope_pending = len(scope.pending_changes) if scope else 0

        # Health
        health = "GREEN"
        if program and hasattr(program, "health"):
            health = program.health.value if hasattr(program.health, "value") else str(program.health)
        if at_risk_ms > 0 or open_risks > 2:
            health = "YELLOW"
        if at_risk_ms > 2 or open_risks > 5:
            health = "RED"

        return ProgramSnapshot(
            program_id=program_id,
            program_name=name,
            health=health,
            milestone_progress=completed_ms / total_ms if total_ms > 0 else 0.0,
            at_risk_milestones=at_risk_ms,
            open_risks=open_risks,
            active_delegations=active_delegations,
            pending_decisions=pending_decisions,
            scope_changes_pending=scope_pending,
        )

    def _detect_resource_conflicts(
        self, program_ids: list[str]
    ) -> list[ResourceConflict]:
        """Detect people overloaded across multiple programs."""
        from lltpm.models.delegation import DelegationPlan

        # person -> {program_id: count}
        person_load: dict[str, dict[str, int]] = {}

        for pid in program_ids:
            plan = self._store.load(pid, "delegation_plan", DelegationPlan)
            if not plan:
                continue
            for item in plan.open_items:
                if item.assigned_to:
                    person_load.setdefault(item.assigned_to, {})
                    person_load[item.assigned_to].setdefault(pid, 0)
                    person_load[item.assigned_to][pid] += 1

        conflicts = []
        for person, programs in person_load.items():
            if len(programs) < 2:
                continue

            total = sum(programs.values())
            severity = "low"
            recommendation = "Monitor workload"

            if total >= 10:
                severity = "critical"
                recommendation = (
                    f"Reassign items from {person} — {total} items across "
                    f"{len(programs)} programs is unsustainable"
                )
            elif total >= 7:
                severity = "high"
                recommendation = f"Consider redistributing {person}'s workload"
            elif total >= 4:
                severity = "medium"
                recommendation = f"Track {person}'s capacity closely"

            conflicts.append(ResourceConflict(
                person=person,
                programs=list(programs.keys()),
                total_active_items=total,
                per_program=programs,
                severity=severity,
                recommendation=recommendation,
            ))

        return sorted(conflicts, key=lambda c: c.total_active_items, reverse=True)

    def _detect_shared_dependencies(
        self, program_ids: list[str]
    ) -> list[SharedDependency]:
        """Find dependencies shared across multiple programs."""
        from lltpm.models.risk import RAIDLog

        # team -> list of program_ids that depend on them
        dep_map: dict[str, dict[str, list[str]]] = {}

        for pid in program_ids:
            raid = self._store.load(pid, "raid_log", RAIDLog)
            if not raid:
                continue
            for dep in raid.dependencies:
                team = dep.upstream_team or dep.title
                dep_map.setdefault(team, {})
                dep_map[team].setdefault(pid, [])
                dep_map[team][pid].append(dep.title)

        shared = []
        for team, programs in dep_map.items():
            if len(programs) < 2:
                continue
            shared.append(SharedDependency(
                title=team,
                upstream_team=team,
                affected_programs=list(programs.keys()),
                status="open",
                impact="high" if len(programs) >= 3 else "medium",
                recommendation=(
                    f"Coordinate with {team} — they block {len(programs)} programs. "
                    f"Consider a dedicated liaison."
                ),
            ))

        return shared

    def _detect_cross_program_risks(
        self,
        program_ids: list[str],
        snapshots: list[ProgramSnapshot],
    ) -> list[CrossProgramRisk]:
        """Detect risks that cascade across programs."""
        risks = []

        # 1. Timeline cascade — if a red program is a dependency of others
        from lltpm.models.risk import RAIDLog

        red_programs = {s.program_id for s in snapshots if s.health == "RED"}

        for pid in program_ids:
            raid = self._store.load(pid, "raid_log", RAIDLog)
            if not raid:
                continue
            for dep in raid.dependencies:
                # Check if the upstream dependency is a program that's at risk
                for rp in red_programs:
                    if rp != pid and (
                        rp in dep.upstream_team.lower()
                        or rp in dep.title.lower()
                    ):
                        risks.append(CrossProgramRisk(
                            title=f"Cascade risk from {rp}",
                            source_program=rp,
                            affected_programs=[pid],
                            risk_type="cascade",
                            probability=0.7,
                            impact="high",
                            description=(
                                f"Program {rp} is RED and {pid} depends on it "
                                f"via {dep.title}"
                            ),
                            recommended_actions=[
                                f"Review {pid}'s dependency on {rp}",
                                "Identify fallback plans",
                                "Escalate to portfolio leadership",
                            ],
                        ))

        # 2. Resource bottleneck — same person critical to multiple at-risk programs
        from lltpm.models.delegation import DelegationPlan

        critical_people: dict[str, list[str]] = {}
        at_risk_ids = {s.program_id for s in snapshots if s.health in ("YELLOW", "RED")}

        for pid in at_risk_ids:
            plan = self._store.load(pid, "delegation_plan", DelegationPlan)
            if not plan:
                continue
            for item in plan.open_items:
                if item.assigned_to and item.priority.value in ("P0", "P1"):
                    critical_people.setdefault(item.assigned_to, [])
                    if pid not in critical_people[item.assigned_to]:
                        critical_people[item.assigned_to].append(pid)

        for person, programs in critical_people.items():
            if len(programs) >= 2:
                risks.append(CrossProgramRisk(
                    title=f"Resource bottleneck: {person}",
                    source_program=programs[0],
                    affected_programs=programs,
                    risk_type="resource",
                    probability=0.6,
                    impact="high",
                    description=(
                        f"{person} has critical (P0/P1) items in {len(programs)} "
                        f"at-risk programs: {', '.join(programs)}"
                    ),
                    recommended_actions=[
                        f"Review {person}'s priority allocation",
                        "Consider delegating non-critical items",
                        "Ensure {person} has clear priority order",
                    ],
                ))

        return risks
