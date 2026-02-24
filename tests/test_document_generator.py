"""Tests for Document Generation Engine."""

import tempfile
from datetime import date
from pathlib import Path

from lltpm.artifacts.store import ArtifactStore
from lltpm.workflows.document_generator import DocumentGenerator


def _make_store():
    return ArtifactStore(Path(tempfile.mkdtemp()))


def _seed_program(store):
    """Seed a program with artifacts for rendering tests."""
    from lltpm.models.program import Program, Stakeholder, SuccessMetric
    program = Program(
        id="test-prog",
        name="Auth Migration",
        goal="Migrate authentication to OAuth2",
        stakeholders=[
            Stakeholder(name="Alice", role="Tech Lead", contact="alice@co.com"),
            Stakeholder(name="Bob", role="EM", contact="bob@co.com"),
        ],
        success_metrics=[
            SuccessMetric(name="Latency", target="<100ms", current="150ms"),
        ],
    )
    store.save("test-prog", "program", program)

    from lltpm.models.update import StatusUpdate, UpdateSeverity
    update = StatusUpdate(
        program_id="test-prog",
        program_name="Auth Migration",
        overall_health=UpdateSeverity.YELLOW,
        progress=["OAuth2 flow implemented", "Testing in staging"],
        top_risks=["API rate limits unclear"],
        asks=["Need security review by March 1"],
        upcoming=["Beta launch March 15"],
    )
    from lltpm.artifacts.status_update import StatusUpdateGenerator
    gen = StatusUpdateGenerator(store)
    gen.create(
        program_id="test-prog",
        program_name="Auth Migration",
        overall_health="YELLOW",
        progress=["OAuth2 flow implemented"],
        top_risks=["API rate limits unclear"],
        asks=["Need security review"],
        upcoming=["Beta launch"],
    )

    from lltpm.models.risk import RAIDLog, Risk, Severity, Probability, RAIDItemStatus
    raid = RAIDLog(
        risks=[Risk(
            id="R-001", title="Rate limits", description="API rate limits may block launch",
            severity=Severity.HIGH, probability=Probability.MEDIUM,
            owner="Alice", mitigation="Implement caching", status=RAIDItemStatus.OPEN,
        )],
    )
    store.save("test-prog", "raid_log", raid)

    from lltpm.models.decision import DecisionLog, Decision, DecisionOption, DecisionStatus
    log = DecisionLog(decisions=[Decision(
        id="DEC-001", title="Auth provider", status=DecisionStatus.DECIDED,
        options=[
            DecisionOption(label="OAuth2", pros=["Standard"], cons=["Complex"], chosen=True),
            DecisionOption(label="SAML", pros=["Enterprise"], cons=["Legacy"]),
        ],
        decided_by="Alice", rationale="Industry standard",
    )])
    store.save("test-prog", "decision_log", log)

    from lltpm.models.milestone import MilestonePlan, Milestone, MilestoneStatus
    plan = MilestonePlan(milestones=[Milestone(
        id="MS-001", name="Beta", target_date=date(2026, 3, 15),
        owner="Alice", status=MilestoneStatus.IN_PROGRESS,
        on_critical_path=True,
    )])
    store.save("test-prog", "milestone_plan", plan)


def test_render_program_brief_html():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    html = gen.render_program_brief("test-prog", "html")
    assert "Auth Migration" in html
    assert "<html" in html
    assert "alice@co.com" in html
    assert "LLTPM" in html


def test_render_program_brief_markdown():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    md = gen.render_program_brief("test-prog", "markdown")
    assert "# Auth Migration" in md


def test_render_status_update_html():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    html = gen.render_status_update("test-prog", "html")
    assert "YELLOW" in html
    assert "OAuth2" in html


def test_render_raid_log_html():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    html = gen.render_raid_log("test-prog", "html")
    assert "R-001" in html
    assert "Rate limits" in html
    assert "HIGH" in html


def test_render_decision_log_html():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    html = gen.render_decision_log("test-prog", "html")
    assert "DEC-001" in html
    assert "OAuth2" in html
    assert "DECIDED" in html


def test_render_milestone_plan_html():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    html = gen.render_milestone_plan("test-prog", "html")
    assert "MS-001" in html
    assert "Beta" in html
    assert "critical path" in html


def test_render_not_found():
    store = _make_store()
    gen = DocumentGenerator(store)

    html = gen.render_raid_log("nonexistent", "html")
    assert "not found" in html.lower() or "Not Found" in html


def test_render_full_report():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    report = gen.render_full_report("test-prog", "markdown")
    assert "RAID" in report or "raid" in report
    assert "Decision" in report or "decision" in report


def test_list_renderable():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    docs = gen.list_renderable("test-prog")
    names = [d["name"] for d in docs]
    assert "Full Program Report" in names
    assert len(docs) >= 3  # at minimum: program, raid_log, decision_log, milestone, + full report


def test_html_has_css():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    html = gen.render_program_brief("test-prog", "html")
    assert "<style>" in html
    assert "font-family" in html


def test_markdown_fallback():
    store = _make_store()
    _seed_program(store)
    gen = DocumentGenerator(store)

    md = gen.render_raid_log("test-prog", "markdown")
    assert "risks" in md.lower()
