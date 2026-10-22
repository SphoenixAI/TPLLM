"""Basic usage example — set up a program and run the daily brief.

Run with:
    ANTHROPIC_API_KEY=sk-... python examples/basic_usage.py
"""

from pathlib import Path
from tpllm.artifacts.store import ArtifactStore
from tpllm.artifacts.program_brief import ProgramBriefGenerator
from tpllm.artifacts.raid_log import RAIDLogManager
from tpllm.artifacts.milestone_plan import MilestonePlanManager
from tpllm.artifacts.meeting_notes import MeetingNotesManager
from tpllm.workflows.daily_cadence import DailyCadence
from tpllm.workflows.meeting_gate import MeetingGate, MeetingProposal

# ── Set up a program ──────────────────────────────────────────
store = ArtifactStore(Path(".tpllm-example"))

briefs = ProgramBriefGenerator(store)
program = briefs.create_program(
    program_id="atlas-v2",
    name="Atlas V2",
    goal="Rebuild the Atlas service with new auth and 10x throughput",
    non_goals=["Mobile app support", "Legacy API compatibility"],
    target_date="2025-09-01",
    stakeholders=[
        {"name": "Alice Chen", "role": "Eng Lead", "team": "Backend"},
        {"name": "Bob Park", "role": "PM", "team": "Product"},
        {"name": "Carol Wu", "role": "Design Lead", "team": "Design"},
    ],
    success_metrics=[
        {"name": "P99 latency", "target": "<100ms"},
        {"name": "Throughput", "target": "10k rps"},
    ],
)
print(program.summary())
print()

# ── Add milestones ────────────────────────────────────────────
milestones = MilestonePlanManager(store)
ms_alpha = milestones.add_milestone(
    "atlas-v2", name="Alpha", phase="Build", target_date="2025-05-01",
    owner="Alice Chen", on_critical_path=True,
)
ms_beta = milestones.add_milestone(
    "atlas-v2", name="Beta", phase="Test", target_date="2025-07-01",
    owner="Alice Chen", on_critical_path=True, dependencies=[ms_alpha.id],
)
milestones.add_milestone(
    "atlas-v2", name="GA", phase="Launch", target_date="2025-09-01",
    owner="Bob Park", on_critical_path=True, dependencies=[ms_beta.id],
)

# ── Add risks ─────────────────────────────────────────────────
raid = RAIDLogManager(store)
raid.add_risk(
    "atlas-v2", title="Auth migration complexity",
    severity="HIGH", owner="Alice Chen",
    impact="May delay Alpha by 2 weeks",
    mitigation="Spike on auth migration in week 1",
)
raid.add_dependency(
    "atlas-v2", title="New auth SDK from Security team",
    upstream_team="Security", downstream_team="Backend",
    owner="Alice Chen", commitment_date="2025-04-15",
)

# ── Add action items ──────────────────────────────────────────
actions = MeetingNotesManager(store)
actions.add_action_item(
    "atlas-v2", "Write auth migration RFC", "Alice Chen",
    due_date="2025-03-20", source="Kickoff meeting",
)

# ── Run the daily brief ──────────────────────────────────────
cadence = DailyCadence(store)
brief = cadence.run("atlas-v2")
print(brief.render())
print()

# ── Test the meeting gate ─────────────────────────────────────
gate = MeetingGate()

# This meeting should be rejected — no pre-read
result = gate.evaluate(MeetingProposal(
    topic="Weekly status sync",
    reason="Check on progress",
    decision_owner="",  # no decision owner!
    options_documented=False,
    pre_read_available=False,
))
print(f"Meeting verdict: {result.verdict.value}")
if result.async_alternative:
    print(f"Async alternative:\n{result.async_alternative}")
