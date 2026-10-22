"""Tests for Dashboard API.

Tests the dashboard API creation and endpoint logic without
requiring FastAPI to be installed (tests the underlying logic).
"""

import json
import tempfile
from datetime import date
from pathlib import Path

from tpllm.artifacts.store import ArtifactStore
from tpllm.config import TPLLMConfig


def _make_config():
    tmp = Path(tempfile.mkdtemp())
    return TPLLMConfig(data_dir=tmp)


def _seed_program(store):
    from tpllm.models.program import Program, Stakeholder
    program = Program(
        id="test-prog", name="Auth Migration",
        goal="OAuth2 migration",
        stakeholders=[Stakeholder(name="Alice", role="Lead", contact="a@co.com")],
    )
    store.save("test-prog", "program", program)

    from tpllm.models.risk import RAIDLog, Risk, Severity, Probability, RAIDItemStatus
    raid = RAIDLog(risks=[Risk(
        id="R-001", title="Rate limits", severity=Severity.HIGH,
        probability=Probability.MEDIUM, owner="Alice",
        status=RAIDItemStatus.OPEN,
    )])
    store.save("test-prog", "raid_log", raid)

    from tpllm.models.milestone import MilestonePlan, Milestone, MilestoneStatus
    plan = MilestonePlan(milestones=[Milestone(
        id="MS-001", name="Beta", owner="Alice",
        target_date=date(2026, 3, 15),
        status=MilestoneStatus.IN_PROGRESS,
    )])
    store.save("test-prog", "milestone_plan", plan)


def test_dashboard_lists_programs():
    """Test that programs can be listed from the store."""
    cfg = _make_config()
    store = ArtifactStore(cfg.data_dir)
    _seed_program(store)

    programs = store.list_programs()
    assert "test-prog" in programs


def test_dashboard_loads_program():
    """Test that program details can be loaded."""
    cfg = _make_config()
    store = ArtifactStore(cfg.data_dir)
    _seed_program(store)

    from tpllm.models.program import Program
    program = store.load("test-prog", "program", Program)
    assert program is not None
    assert program.name == "Auth Migration"


def test_dashboard_raid_endpoint_logic():
    """Test RAID log loading for dashboard."""
    cfg = _make_config()
    store = ArtifactStore(cfg.data_dir)
    _seed_program(store)

    from tpllm.models.risk import RAIDLog
    raid = store.load("test-prog", "raid_log", RAIDLog)
    assert raid is not None
    assert len(raid.risks) == 1
    assert len(raid.open_risks) == 1


def test_dashboard_milestones_endpoint_logic():
    """Test milestone loading for dashboard."""
    cfg = _make_config()
    store = ArtifactStore(cfg.data_dir)
    _seed_program(store)

    from tpllm.models.milestone import MilestonePlan
    plan = store.load("test-prog", "milestone_plan", MilestonePlan)
    assert plan is not None
    assert len(plan.milestones) == 1


def test_dashboard_predictions_endpoint_logic():
    """Test risk prediction via dashboard."""
    cfg = _make_config()
    store = ArtifactStore(cfg.data_dir)
    _seed_program(store)

    from tpllm.workflows.risk_predictor import RiskPredictor
    predictor = RiskPredictor(store)
    predictions = predictor.predict_risks("test-prog")
    # Should find at-risk items from seeded data
    assert isinstance(predictions, list)


def test_dashboard_document_generation():
    """Test document rendering via dashboard logic."""
    cfg = _make_config()
    store = ArtifactStore(cfg.data_dir)
    _seed_program(store)

    from tpllm.workflows.document_generator import DocumentGenerator
    gen = DocumentGenerator(store)

    html = gen.render_program_brief("test-prog", "html")
    assert "<html" in html
    assert "Auth Migration" in html


def test_dashboard_memory_endpoint_logic():
    """Test memory context via dashboard."""
    cfg = _make_config()
    store = ArtifactStore(cfg.data_dir)

    from tpllm.workflows.persistent_memory import PersistentMemoryEngine
    engine = PersistentMemoryEngine(store)
    summary = engine.get_summary("test-prog")
    assert "Persistent Memory" in summary


def test_dashboard_agents_endpoint_logic():
    """Test agent listing via dashboard."""
    from tpllm.workflows.agent_orchestrator import AgentOrchestrator
    orch = AgentOrchestrator()
    agents = orch.list_agents()
    assert len(agents) == 6


def test_dashboard_deployment_config():
    """Test deployment config generation."""
    from tpllm.workflows.agent_orchestrator import AgentOrchestrator
    orch = AgentOrchestrator()
    config = orch.get_deployment_config()
    assert config["name"] == "TPLLM Multi-Agent System"
    assert "/webhooks/slack" in config["integration_endpoints"]["slack_webhook"]


def test_create_app_without_fastapi():
    """Test that create_app raises ImportError without FastAPI."""
    try:
        import fastapi
        # FastAPI is installed, skip this test
        return
    except ImportError:
        pass

    from tpllm.dashboard import create_app
    try:
        create_app()
        assert False, "Should have raised ImportError"
    except ImportError as e:
        assert "FastAPI" in str(e)
