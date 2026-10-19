# LLTPM — AI Technical Program Manager

## Project Overview

LLTPM is an AI agent that fully replaces the Technical Program Manager role. It automates standups, status reports, risk detection, sprint planning, stakeholder communication, and cross-team coordination — replacing meetings with insight.

## Architecture Roadmap

### Current: Python (Prototyping & Testing)

The entire stack is Python today. This is intentional — we're iterating rapidly on the AI agent logic, workflows, and integrations. Python is the right choice for this phase because:

- Fast iteration on AI/LLM integration (Anthropic SDK, prompt engineering)
- Rich ecosystem for integrations (GitHub, Jira, Slack, Google Calendar)
- Pydantic v2 for data validation
- FastAPI for the dashboard API
- Easy to test and refactor

### Planned: TypeScript API + Python Agent Workers

When we move to a hosted multi-tenant service, the architecture will split:

- **TypeScript API gateway** — handles HTTP, WebSocket, auth, rate limiting, tenant isolation, real-time dashboard. TypeScript is strong here: type safety, async performance, mature web ecosystem (Express/Fastify/Hono), and easy frontend integration.
- **Python agent workers** — the AI reasoning layer stays in Python. LLM orchestration, tool execution, workflow engines, and integration adapters run as background workers. Python remains the best choice for AI/ML workloads.
- Communication between layers via message queue (Redis, SQS, or similar).

### Future: Potential Rust Migration (API Layer)

Once the product is stable and scale demands it, the TypeScript API gateway could be migrated to Rust for:

- Lower latency and memory footprint
- Stronger compile-time safety guarantees
- Better performance under high concurrency

This is not urgent — TypeScript handles significant scale. Rust migration is a later optimization, not a near-term priority.

## Tech Stack

- **Language**: Python 3.11+
- **AI**: Anthropic Claude (manual tool-use loop, not Agent SDK)
- **Models**: Pydantic v2
- **API**: FastAPI with JWT authentication (PyJWT ≥2.11.0)
- **Auth**: RBAC (4 roles, 14 permissions), HMAC-SHA256 JWTs, API key → JWT bridge
- **TTS**: 5 backends — Chatterbox (recommended), Piper, Kokoro, ElevenLabs, System
- **Integrations**: GitHub, Jira, Slack, Google Calendar, SendGrid
- **Testing**: pytest, 2850+ tests
- **Packaging**: Hatchling build system
- **Durability**: append-only hash-chained event journal, SQLite reference
  backend (stdlib `sqlite3`), deterministic replay

## Project Structure

The original TPM surface (`agent.py`, `models/`, `workflows/`,
`artifacts/`, `tools/`, `dashboard.py`) sits on top of a layered
organizational-reasoning stack built out over a long commit arc. The
layering is enforced by `tests/test_architecture_boundaries.py`, which
builds a first-party import graph by AST and asserts it stays acyclic —
read that file before moving an import between packages.

```
src/lltpm/
  # ── Foundations (import no first-party package) ──
  core/            — shared domain primitives (ActorReference, evidence refs)
  temporal/        — bitemporal truth: reality time vs knowledge time, lineage
  identity/        — cross-system entity resolution, canonical identity graph
  structural/      — causal/structural graph engine (networkx)
  persistence/     — durable journal, snapshots, replay, SQLite backend

  # ── Domain intelligence ──
  intelligence/    — project understanding, KPI intelligence, launch readiness,
                     AI quality, self-research
  coordination/    — decisions, capsules, recommendations, authority policy,
                     bounded autonomy, scenario-aware decisioning
  scenarios/       — probabilistic scenario lattice, interaction/miscommunication
  portfolio/       — cross-project topology, capacity, collisions, simulation
  memory/          — procedural coordination memory, resolution patterns
  evaluation/      — historical replay, forecast metrics, calibration
  application/     — governed calibration application and auto-revert
  ontology/        — ontology objects
  advisory/        — advisory surfaces

  # ── Orchestration (top of the graph; nothing imports it) ──
  runtime/         — live orchestrator: dedupe, temporal ordering, coalesced
                     recompute, coherent revisions, materiality, causal
                     compression, attention routing, durability seam
                     services.py  — the production IntelligenceServices;
                                    dispatches each artifact type to its engine
                     sources.py   — domain inputs read from the journal,
                                    including evidence -> TemporalEvent
                     baselines.py — deterministic offline providers for the
                                    two Protocols that otherwise need a model
                     identity_resolution.py — canonical identity on the ingest
                                    path; decisions are journalled, ambiguity
                                    is preserved rather than guessed
  runtime_cli.py   — `lltpm runtime` command group (ingest/status/verify/
                     journal/replay/snapshot/coverage)

  # ── Original TPM surface ──
  models/          — Pydantic data models (program, security, etc.)
  workflows/       — Business logic (security_manager, sprint, standup, etc.)
  integrations/    — External services (GitHub, Jira, Slack, voice, etc.)
  artifacts/       — Artifact store (ArtifactStore, JSON-backed)
  tools/           — LLM tool definitions
  dashboard.py     — FastAPI web dashboard with JWT auth
  main.py          — CLI entry point
tests/             — Test suite (pytest); `_*_fixtures.py` modules are shared
                     fixtures, not test files
```

### Architectural invariants

These are enforced by tests and should not be relaxed casually:

- **Dependency direction** — `persistence` and `core`/`temporal`/`identity`/
  `structural` import no first-party package; `runtime` composes the domains
  and nothing imports `runtime`; no domain imports `persistence` or `sqlite3`.
- **Probability honesty** — probability and confidence are separate
  quantities; probability and impact are separate; pruned branches keep their
  mass so displayed futures never falsely sum to 100%.
- **Arrival order is not temporal authority** — a late-arriving event cannot
  move an already-decided decision back to pending.
- **Coherent revisions** — intelligence is published as a revision; a partial
  recompute never becomes current.
- **No stale autonomous action** — validation and finalization happen in one
  guarded call; the kill switch is checked first and nothing widens authority.
- **Journal first, promote last** — durable changes are appended before the
  runtime treats them as real; derived state is always rebuildable.
- **Identity is resolved conservatively** — an explicit curated alias
  resolves; a mere name resemblance does not, and an ambiguous match is
  left unlinked for a person to settle. Merging two colleagues who share
  a surname is worse than reporting an unlinked identity.
- **Replay touches nothing external** — rebuilding history never re-sends a
  message or re-finalizes a decision.
- **No worker scoring or activity tracking** — the system models
  organizational mechanics (capacity, dependencies, authority, information
  flow), never individual productivity, and source-level guardrail tests
  enforce it.

## Development Conventions

- Deep research and security over speed
- All tests must pass before committing (`python -m pytest tests/ -q`)
- Use `python -m pip` / `python -m pytest`, not bare `pip`/`pytest` — the
  container's bare `pip` may be bound to a different interpreter
- Use 32+ byte keys in tests to avoid InsecureKeyLengthWarning
- JWT tokens: 30min access, 7-day refresh, rotation on refresh
- Timing-safe comparison for all secret validation
- No hardcoded secrets — generated at runtime, persisted in ArtifactStore
- Durable payloads are JSON, never pickle; credentials never enter the journal
- Version lives only in `pyproject.toml`; `LLTPM_VERSION` reads it from
  installed package metadata

## Quick Start

```bash
bash setup.sh          # Full setup: install, test, smoke test
python -m lltpm init   # Configuration wizard
python -m lltpm chat   # Interactive agent
python -m lltpm dashboard  # Web dashboard

# The organizational-reasoning stack (offline, no API key needed)
python -m lltpm runtime ingest evidence.json --db lltpm.db
python -m lltpm runtime status --db lltpm.db
python -m lltpm runtime replay --db lltpm.db --publish
```

### Wiring state

`runtime/services.py` is the only production implementation of the
orchestrator's `recompute_artifact` port. Nine artifact types run a
dedicated engine; nine are thinner derived projections. `lltpm runtime
coverage` reports which is which — it is deliberately honest rather than
letting a projection pass for an engine.

Packages still not on the live path: `memory`, `evaluation`,
`application`, `structural`, `ontology`, `advisory`. They are built and
tested but no user-facing command invokes them yet — `evaluation` and
`application` are offline calibration concerns rather than live-path
ones, so that is partly by design.
