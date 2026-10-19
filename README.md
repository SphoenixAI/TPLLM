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
python -m pip install -e ".[dev]"

# Run tests (2850+)
python -m pytest tests/ -q

# Run with integrations
python -m pip install -e ".[integrations]"
```

Use `python -m pip` and `python -m pytest` rather than the bare
executables — on some systems `pip` is bound to a different interpreter
than `python`, which installs the dependencies where the tests cannot
see them.


## Project Structure

LLTPM has two layers. The **TPM surface** is the agent, its tools, and
the artifacts it produces. Underneath sits a **layered
organizational-reasoning stack** — temporal truth, scenarios, portfolio
topology, governed autonomy, a live orchestrator, and a durable journal.

The layering is not aspirational: `tests/test_architecture_boundaries.py`
builds a first-party import graph by parsing source and asserts it stays
acyclic, that the foundations import nothing, and that nothing imports
the orchestrator.

```
src/lltpm/
├── agent.py              # Core TPM agent (LLM tool-use loop)
├── main.py               # CLI entry point
├── config.py             # Configuration management
├── system_prompt.py      # Agent personality and rules
├── dashboard.py          # FastAPI dashboard with JWT auth
│
│   # ── Foundations: import no first-party package ──
├── core/                 # Shared domain primitives
├── temporal/             # Bitemporal truth (reality time vs knowledge time)
├── identity/             # Cross-system entity resolution
├── structural/           # Causal/structural graph engine
├── persistence/          # Durable journal, snapshots, replay, SQLite
│
│   # ── Domain intelligence ──
├── intelligence/         # Project understanding, KPIs, launch readiness
├── coordination/         # Decisions, authority policy, bounded autonomy
├── scenarios/            # Probabilistic scenario lattice
├── portfolio/            # Cross-project topology and simulation
├── memory/               # Procedural coordination memory
├── evaluation/           # Replay, forecast metrics, calibration
├── application/          # Governed calibration application, auto-revert
│
│   # ── Orchestration: nothing imports this ──
├── runtime/              # Live orchestrator + durability seam
│
│   # ── Original TPM surface ──
├── models/               # Pydantic data models
├── artifacts/            # Artifact store and generators
├── workflows/            # TPM workflow engines
├── integrations/         # GitHub, Jira, Slack, Linear, Calendar, voice
└── tools/                # LLM tool definitions
```

### What the stack guarantees

- **Probability honesty** — likelihood and confidence are separate numbers,
  and pruned branches keep their mass, so displayed futures never falsely
  sum to 100%.
- **Arrival order is not temporal authority** — a webhook that lands late
  cannot move an approved decision back to pending.
- **Coherent revisions** — intelligence is published as a set computed
  against one evidence cutoff; a partial recompute never becomes current.
- **No stale autonomous action** — a recommendation is revalidated and
  finalized inside one guarded call, kill switch first.
- **Durability** — an append-only hash-chained journal is the source of
  truth; every derived projection can be deleted and rebuilt from it.
- **Not a surveillance tool** — the system models organizational mechanics,
  never individual productivity, and tests enforce that at the source level.

### Running the organizational stack

The reasoning stack has its own command group. It works offline — no API
key, no cloud account, one local SQLite file — because the two engines
that would otherwise need a model fall back to deterministic baselines
that are labelled as baselines in the reproducibility manifest.

```bash
# Journal evidence, then recompute intelligence from it
lltpm runtime ingest evidence.json --db lltpm.db --organization acme

# Current revision, journal head, storage health
lltpm runtime status --db lltpm.db --organization acme

# Verify the journal hash chain (read-only; never repairs)
lltpm runtime verify --db lltpm.db

# Inspect durable history
lltpm runtime journal --db lltpm.db --event-type evidence_accepted

# Rebuild projections from the journal (dry run by default)
lltpm runtime replay --db lltpm.db --organization acme --publish

# Checkpoint so future replays start from here
lltpm runtime snapshot --db lltpm.db --organization acme

# Which artifact types run a dedicated engine
lltpm runtime coverage
```

`ingest` is journal-first: the evidence is durable before anything
derives from it, so a recompute that fails leaves the evidence intact
and a later `replay` rebuilds the projection.

### Durable storage

The reference deployment is one local SQLite file. No Kafka, no Redis,
no Postgres, no object store, no daemon — every backend sits behind a
protocol, and `persistence` imports no other first-party package.

```bash
# Back up everything: copy one file
cp lltpm.db lltpm.db.backup
```

