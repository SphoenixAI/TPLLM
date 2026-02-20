# LLTPM — AI Technical Program Manager Agent

An AI-powered TPM agent that replaces unnecessary meetings with concise, decision-ready insight. Built on Claude (Opus 4.6) with a full agentic tool-use loop, LLTPM manages programs the way a senior TPM would — but faster, because it can see the whole program simultaneously.

## The Problem

Human TPMs are bottlenecked by:
- **Meeting overhead**: Status syncs that could be async updates
- **Context switching**: Can't hold the full dependency graph in their heads
- **Manual aggregation**: Hours spent compiling status from tickets, PRs, and Slack
- **Delayed risk detection**: Risks surface in meetings, not when they actually emerge

## What LLTPM Does Differently

- **Async-first**: Never proposes a meeting when a structured async update will do. Enforces a Meeting Gate that requires decision owner, options doc, pre-read, and deadline before any meeting is approved.
- **Automatic risk detection**: Scans milestones, dependencies, and tickets for drift, compression, unmitigated risks, and approaching deadlines — continuously, not once a week.
- **Decision-ready briefs**: Aggregates data from tickets, PRs, bugs, and action items into a single program pulse. No more "let me check and get back to you."
- **Living artifacts**: RAID logs, decision logs, milestone plans, and status updates are auto-maintained — always current, never stale.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    CLI / Chat Interface                  │
├─────────────────────────────────────────────────────────┤
│                  TPM Agent (Claude Opus 4.6)            │
│              Agentic loop with tool use                 │
├──────────────┬──────────────┬───────────────────────────┤
│    Tools     │   Workflows  │       Artifacts           │
│  (30+ tools) │              │                           │
│  - Project   │  - Meeting   │  - Program Brief          │
│  - Tracking  │    Gate      │  - RAID Log               │
│  - Comms     │  - Risk      │  - Decision Log           │
│  - GitHub    │    Engine    │  - Milestone Plan          │
│  - Analytics │  - Dep       │  - Status Updates          │
│  - Calendar  │    Tracker   │  - Launch Checklist        │
│              │  - Daily     │  - Action Tracker          │
│              │    Cadence   │  - Bug Dashboard           │
│              │  - Weekly    │                            │
│              │    Planner   │                            │
├──────────────┴──────────────┴───────────────────────────┤
│                   Integrations                          │
│         GitHub · Jira · Slack · Linear · Calendar       │
├─────────────────────────────────────────────────────────┤
│                  Artifact Store (JSON)                   │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Install
pip install -e .

# Set your API key
export ANTHROPIC_API_KEY=sk-...

# Interactive chat with the TPM agent
lltpm chat

# One-shot command
lltpm ask "Set up a new program called Phoenix to rebuild our payments service"

# Run the daily brief
lltpm daily phoenix

# Check program health
lltpm pulse phoenix

# Scan for risks
lltpm risks phoenix

# See today's focus
lltpm today
```

## Agent Capabilities

### Program Management
- Create programs with briefs (goal, non-goals, stakeholders, success metrics)
- Manage milestones with entry/exit criteria and critical path tracking
- Maintain RAID logs (Risks, Assumptions, Issues, Dependencies)
- Track decisions with options, rationale, and follow-ups
- Generate structured status updates for different audiences

### Meeting Gate
Every meeting request is evaluated against four criteria:
1. Decision owner identified
2. Options/tradeoffs documented
3. Pre-read available
4. Decide-by deadline set

If any fail, the agent proposes an async alternative workflow.

### Risk Detection
Automatic scanning for:
- **Schedule compression**: Too many milestones in too little time
- **Dependency drift**: Tickets stale for 48h+, overdue commitments
- **Unmitigated risks**: High/critical risks with no mitigation plan
- **Dependency clustering**: Too many deps blocked on one team
- **Approaching deadlines**: Milestones not started but due soon

### Daily Cadence
Runs the full TPM daily routine in seconds:
1. Aggregate program pulse from all data sources
2. Check for dependency drift
3. Scan for risk signals
4. Synthesize recommended actions

### Weekly Rhythm
Structured weekly themes:
- **Monday**: Plan Integrity
- **Tuesday**: Execution Acceleration
- **Wednesday**: Quality + Integration
- **Thursday**: Launch Readiness
- **Friday**: Reporting + Retro

### Bug Triage
- Severity classification (P0-P3)
- SLA tracking and violation detection
- Stop-ship bug identification
- Burn-down tracking

## Integrations

Configure via environment variables:

| Integration | Variables | What It Enables |
|-------------|-----------|-----------------|
| GitHub | `GITHUB_TOKEN` | PR tracking, build health |
| Jira | `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL` | Ticket tracking, sprint status |
| Slack | `SLACK_BOT_TOKEN` | Async updates, structured nudges |
| Linear | `LINEAR_API_KEY` | Issue tracking |

## Example Session

```
You: Set up a new program called Atlas V2 to rebuild the Atlas service
     with new auth. Target date September 1. Alice is eng lead, Bob is PM.

LLTPM: [tool: create_program]
       [tool: add_milestone] x3
       [tool: init_launch_checklist]

I've created the Atlas V2 program with the following structure:

## Atlas V2
**Goal**: Rebuild the Atlas service with new auth
**Target date**: 2025-09-01
**Health**: GREEN

**Stakeholders**:
- Alice (Eng Lead)
- Bob (PM)

I've also set up:
- 3 milestones (Alpha → Beta → GA) on the critical path
- Launch readiness checklist with 11 default gates

You: Should we have a weekly status meeting?

LLTPM: [tool: evaluate_meeting]

**Meeting verdict: REJECTED — use async instead.**

Failures:
- No decision owner identified
- Options/tradeoffs are not pre-written
- No pre-read available
- No decide-by deadline set

Instead:
1. I'll generate a structured weekly status update every Friday
2. Post it to the team channel with a 24h review deadline
3. If anyone has concerns, they comment inline
4. Only escalate to a 15-min focused meeting if there's an actual decision
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with integrations
pip install -e ".[integrations]"
```

## Project Structure

```
src/lltpm/
├── agent.py              # Core TPM agent (LLM tool-use loop)
├── main.py               # CLI entry point
├── config.py             # Configuration management
├── system_prompt.py      # Agent personality and rules
├── models/               # Pydantic data models
│   ├── program.py        # Program, stakeholders, metrics
│   ├── risk.py           # RAID items
│   ├── decision.py       # Decision log entries
│   ├── milestone.py      # Milestones, gates, plans
│   └── update.py         # Status updates, action items
├── artifacts/            # Artifact persistence and generation
│   ├── store.py          # JSON file-backed store
│   ├── raid_log.py       # RAID log CRUD
│   ├── decision_log.py   # Decision log management
│   ├── milestone_plan.py # Milestone planning
│   ├── status_update.py  # Status report generation
│   ├── launch_checklist.py # Launch readiness gates
│   ├── meeting_notes.py  # Action item tracking
│   └── program_brief.py  # Program one-pager
├── workflows/            # TPM workflow engines
│   ├── meeting_gate.py   # Anti-meeting enforcer
│   ├── dependency_tracker.py # Drift detection
│   ├── risk_engine.py    # Automatic risk scanning
│   ├── update_aggregator.py  # Program pulse
│   ├── daily_cadence.py  # Daily TPM routine
│   ├── weekly_planner.py # Weekly rhythm
│   └── bug_triage.py     # Bug economy management
├── integrations/         # External service connectors
│   ├── github_integration.py
│   ├── jira_integration.py
│   ├── slack_integration.py
│   ├── linear_integration.py
│   └── calendar_integration.py
└── tools/                # LLM tool definitions
    ├── registry.py       # Tool registry
    ├── project_tools.py  # 20+ program management tools
    ├── tracking_tools.py # Workflow and tracking tools
    ├── analytics_tools.py # Pulse and simulation tools
    ├── communication_tools.py # Slack tools
    ├── github_tools.py   # GitHub tools
    └── calendar_tools.py # Scheduling tools
```
