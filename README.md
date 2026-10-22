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

# Run tests (3,400+)
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
│   # ── Application infrastructure ──
├── observability/        # Log formatting, redaction; only the CLI uses it
│
│   # ── Original TPM surface ──
├── models/               # Pydantic data models
├── artifacts/            # Artifact store and generators
├── workflows/            # TPM workflow engines
├── integrations/         # GitHub, Jira, Slack, Linear, Calendar, voice
└── tools/                # LLM tool definitions
```

Design decisions are checked against the published work they overlap
with in [docs/research-alignment.md](docs/research-alignment.md) — including
three places where reading the literature exposed a real gap, and two
where LLTPM departs from the standard answer on purpose.

What was checked before publishing this — and what was deliberately
not checked — is in [docs/release-audit.md](docs/release-audit.md) and
[docs/security-audit.md](docs/security-audit.md). The deployment
boundary, including what is **not** supported, is in
[docs/threat-model.md](docs/threat-model.md).

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
- **Not a surveillance tool** — it measures dependency congestion, approval
  queues, information divergence, capacity constraints, decision latency,
  KPI state, context handoff depth and organizational topology. It does not
  grade anyone's responsiveness, reliability or collaboration, and it does
  not name who "was the blocker". A source-level guardrail asserts that no
  model anywhere has a person as its subject *and* a grade, score, rate or
  hours count, that no agent tool can be asked for a verdict on a
  colleague, and that no per-person review is served over HTTP.

  This is also why the structural reading is the more capable one. A team
  can look slow because four projects share one approval authority. The
  system can say that. A quarterly letter grade takes the same evidence
  and converts a structural failure into an individual's score — less
  true, and less useful to everyone including the person graded.
- **Your history is yours** — durable state is JSON in one SQLite file, and
  `lltpm runtime export` gives you the journal, decisions, recommendations
  and evidence provenance without parsing anything opaque.

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

# Watch records land, like tail -f (read-only; Ctrl-C to stop)
lltpm runtime journal --db lltpm.db --follow

# Rebuild projections from the journal (dry run by default)
lltpm runtime replay --db lltpm.db --organization acme --publish

# Checkpoint so future replays start from here
lltpm runtime snapshot --db lltpm.db --organization acme

# Which artifact types run a dedicated engine
lltpm runtime coverage

# Export durable history as JSON (read-only)
lltpm runtime export --db lltpm.db --organization acme > history.json
lltpm runtime export --db lltpm.db --what decisions
```

#### Watching it work

Each command reports what it did on stdout. `--log-level` adds the
process's own account of itself on stderr — which engine ran, what was
deduplicated, why a revision was not promoted — so redirecting stdout to
a file keeps both usable:

```bash
# Human-readable, on stderr
lltpm runtime --log-level info ingest evidence.json --db lltpm.db

# One JSON object per line, for a log pipeline
lltpm runtime --log-level debug --log-format json \
  ingest evidence.json --db lltpm.db 2> run.jsonl

# Clean JSON on stdout, process account on stderr, at the same time
lltpm runtime --log-level info export --db lltpm.db > history.json
```

Log lines carry the ids that join them back to durable history —
`revision_id`, `journal_sequence`, `causal_event_id`,
`organization_id` — and anything produced by a replay is marked
`execution_mode=replay`, so reconstruction is never mistaken for live
work. Credential-looking material is redacted at format time.

The log and the journal answer different questions and are deliberately
not the same thing. The journal records *what happened to the
organization*: durable, ordered, hash-chained, replayable. The log
records *what the process did*: transient, cheap, and safe to lose. No
log line is authoritative and none is ever replayed.

Nothing in the log describes an individual's activity. The field
vocabulary is a closed list and a source guardrail walks every log call
in the package to keep it that way.

### Optional extras

The core install needs no account and no network. Everything else is an
extra, so nothing heavy is pulled in by default:

```bash
pip install lltpm                  # core: the stack, the CLI, the journal
pip install "lltpm[integrations]"  # GitHub, Jira, Slack, Google Calendar
pip install "lltpm[dashboard]"     # the FastAPI dashboard
pip install "lltpm[email]"         # SendGrid delivery
pip install "lltpm[openai]"        # an OpenAI-compatible model backend
pip install "lltpm[pdf]"           # PDF export (WeasyPrint)
pip install "lltpm[voice]"         # local TTS backends
pip install "lltpm[all]"           # integrations, email, openai, dashboard
```

`all` deliberately leaves out `pdf` and `voice`: both pull in large
native dependencies, and neither is needed to run the stack.

### Reading a real source system

`lltpm integrations` is the path from a provider's API to durable
evidence. It is read-only against the provider by construction: the
reader can issue nothing but GET, and a test asserts no write verb
appears in it.

```bash
# What can be read, and how trustworthy each contract is
lltpm integrations sources

# See what a payload would become — offline, no network, no database
lltpm integrations normalize pulls.json --kind pull_request \
  --repository acme/atlas --project atlas

# The live path: read a repository and journal what it says
lltpm integrations ingest --repository acme/atlas --project atlas \
  --db lltpm.db --organization acme

# Check the recorded contracts still match the live API
lltpm integrations verify --repository acme/atlas
```

Three things it refuses to do, because each alternative is a specific
false statement:

- **It never guesses a project.** Bindings are declared. An unbound
  repository is reported, not filed under a plausible project.
- **It never invents an event class.** A push, a comment-only review and
  a draft pull request are reported as uninterpreted with a stated
  reason. Merged code stays technical evidence, not feature completion.
- **It never stamps `now` on a past fact.** A pull request merged in
  February and read in October occurred in February; a payload with no
  usable timestamp is rejected rather than dated today.

Idempotency follows the published guidance for webhook delivery: the key
is the provider's own event id — `X-GitHub-Delivery` for a delivery, the
event id from the Events API, or a polled resource's identity plus its
version — never a timestamp and never a payload hash. Reading the same
repository twice appends nothing.

#### Keeping the recordings honest

The offline tests run against payloads recorded from the live API, in
the shape of Fowler's self-initializing fake: record once, replay
offline, re-check the recordings on their own schedule. The contract is
the set of fields the normalizers actually read, with the types they may
take — so a new GitHub field cannot fail the build and a renamed
`merged_at` must.

`lltpm integrations sources` reports the provenance of every contract,
and `verify` refuses to report an unrecorded one as passing:

```
│ github │ event        │ recorded     │
│ github │ issue        │ derived      │
│ github │ pull_request │ recorded     │
│ github │ review       │ hand-written │
```

Recorded fixtures name nobody: handles, account ids and repository names
are pseudonymized before anything is written, and `record` refuses to
write a fixture that still discloses something.

A worked example lives in `examples/evidence.json` — three pieces of
evidence across two projects, enough for the engines to produce a
revision. The whole walkthrough runs in CI on every push, so if the
offline promise above stops being true, the build says so.

Every export carries its own integrity verdict. An export of damaged
history says so in the JSON and exits non-zero, so a copy that was never
verified cannot be mistaken for one that was. Caveats go to stderr, so
redirecting stdout gives clean JSON.

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

