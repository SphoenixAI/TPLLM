"""Tests for Cross-Program Intelligence."""

import tempfile
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.models.delegation import DelegationItem, DelegationPlan, DelegationStatus
from tpllm.models.milestone import Milestone, MilestonePlan, MilestoneStatus
from tpllm.models.program import Program
from tpllm.models.risk import RAIDLog, Dependency, Risk, Severity, Probability, RAIDItemStatus
from tpllm.workflows.cross_program_intel import CrossProgramIntel


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def _seed_programs(store, count=3):
    """Create multiple programs with artifacts for cross-program testing."""
    for i in range(count):
        pid = f"prog-{i}"
        store.save(pid, "program", Program(
            id=pid, name=f"Program {i}", goal=f"Goal for program {i}"
        ))
        store.save(pid, "milestone_plan", MilestonePlan(milestones=[
            Milestone(
                id=f"MS-{i}-1", name=f"Milestone {i}",
                owner="Alice" if i == 0 else "Bob",
                status=MilestoneStatus.AT_RISK if i == 0 else MilestoneStatus.IN_PROGRESS,
            ),
        ]))
        # Give Alice items across all programs
        store.save(pid, "delegation_plan", DelegationPlan(items=[
            DelegationItem(
                id=f"DEL-{i}-{j}", title=f"Task {j}",
                assigned_to="Alice", status=DelegationStatus.IN_PROGRESS,
            )
            for j in range(3)
        ]))


def _seed_shared_deps(store):
    """Create shared dependencies across programs."""
    for i, pid in enumerate(["prog-0", "prog-1"]):
        store.save(pid, "program", Program(
            id=pid, name=f"Program {i}", goal=f"Goal {i}"
        ))
        store.save(pid, "raid_log", RAIDLog(
            dependencies=[Dependency(
                id=f"DEP-{pid}", title="Platform API",
                upstream_team="Platform Team",
                status=RAIDItemStatus.OPEN,
            )],
        ))


def test_portfolio_view():
    store = _make_store()
    _seed_programs(store)
    intel = CrossProgramIntel(store)

    view = intel.generate_portfolio_view()

    assert len(view.programs) == 3
    assert view.overall_health.value in ("HEALTHY", "AT_RISK", "CRITICAL")


def test_resource_conflicts_detected():
    store = _make_store()
    _seed_programs(store)
    intel = CrossProgramIntel(store)

    conflicts = intel.get_resource_conflicts()

    # Alice has items across all 3 programs
    alice_conflicts = [c for c in conflicts if c.person == "Alice"]
    assert len(alice_conflicts) == 1
    assert alice_conflicts[0].total_active_items == 9  # 3 items x 3 programs
    assert len(alice_conflicts[0].programs) == 3


def test_shared_dependencies():
    store = _make_store()
    _seed_shared_deps(store)
    intel = CrossProgramIntel(store)

    deps = intel.get_shared_dependencies()

    platform_deps = [d for d in deps if "Platform" in d.title]
    assert len(platform_deps) == 1
    assert len(platform_deps[0].affected_programs) == 2


def test_portfolio_summary():
    store = _make_store()
    _seed_programs(store)
    intel = CrossProgramIntel(store)

    summary = intel.get_portfolio_summary()

    assert "Portfolio Overview" in summary
    assert "3 programs" in summary


def test_empty_portfolio():
    store = _make_store()
    intel = CrossProgramIntel(store)

    view = intel.generate_portfolio_view()

    assert len(view.programs) == 0
    assert view.overall_health.value == "HEALTHY"


def test_program_snapshot_health():
    store = _make_store()
    pid = "test-prog"
    store.save(pid, "program", Program(id=pid, name="Test", goal="Test goal"))
    store.save(pid, "milestone_plan", MilestonePlan(milestones=[
        Milestone(id="MS-1", name="M1", status=MilestoneStatus.AT_RISK),
        Milestone(id="MS-2", name="M2", status=MilestoneStatus.AT_RISK),
        Milestone(id="MS-3", name="M3", status=MilestoneStatus.AT_RISK),
    ]))
    store.save(pid, "raid_log", RAIDLog(risks=[
        Risk(id=f"R-{i}", title=f"Risk {i}", severity=Severity.HIGH,
             probability=Probability.HIGH, status=RAIDItemStatus.OPEN)
        for i in range(6)
    ]))

    intel = CrossProgramIntel(store)
    view = intel.generate_portfolio_view()

    # With 3 at-risk milestones and 6 open risks, should be RED
    assert view.programs[0].health == "RED"
