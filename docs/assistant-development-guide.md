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
- **Testing**: pytest, 360+ tests
- **Packaging**: Hatchling build system

## Project Structure

```
src/lltpm/
  models/          — Pydantic data models (program, security, etc.)
  workflows/       — Business logic (security_manager, sprint, standup, etc.)
  integrations/    — External services (GitHub, Jira, Slack, voice, etc.)
  artifacts/       — Persistence layer (ArtifactStore, JSON-backed)
  dashboard.py     — FastAPI web dashboard with JWT auth
  main.py          — CLI entry point
tests/             — Test suite (pytest)
```

## Development Conventions

- Deep research and security over speed
- All tests must pass before committing (`python -m pytest tests/ -q`)
- Use 32+ byte keys in tests to avoid InsecureKeyLengthWarning
- JWT tokens: 30min access, 7-day refresh, rotation on refresh
- Timing-safe comparison for all secret validation
- No hardcoded secrets — generated at runtime, persisted in ArtifactStore

## Quick Start

```bash
bash setup.sh          # Full setup: install, test, smoke test
python -m lltpm init   # Configuration wizard
python -m lltpm chat   # Interactive agent
python -m lltpm dashboard  # Web dashboard
```
