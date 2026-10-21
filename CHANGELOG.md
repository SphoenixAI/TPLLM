# Changelog

Notable changes, newest first. Versions follow the commit arc the
project is built in: `0.41.0` is the state after Commit 41.

Each entry says what changed and, where it matters, what was wrong
before. Several entries record the removal of something that shipped —
those are the ones worth reading.

## 0.41.0 — first public release

The release that the open-source audit was done against.

### Removed

- **Individual performance grading, entirely.** `PerformanceGrade`,
  `PersonMetrics`, `QuarterlyEvaluation`, `ResponsivenessGrade`,
  `PersonTrend` and `AttendeeInfo.engagement_score`, along with the two
  agent tools (`generate_evaluation`, `get_evaluation`), the
  `GET /api/programs/{id}/evaluations/{person}` endpoint, and two risk
  models that predicted people rather than structure. The system
  measures dependency congestion, approval queues, information
  divergence, capacity constraints, decision latency, KPI state, context
  handoff depth and organizational topology — and the structural reading
  is the more capable one: a team that looks slow because four projects
  share one approval authority is a finding about the approval
  authority. A letter grade destroys it. (Commit 41)
- Four `RiskSignalType` members that named an individual as the risk:
  `RESPONSE_DEGRADATION`, `ENGAGEMENT_DROP`, `COMMITMENT_MISS`,
  `BLOCKER_PATTERN`.

### Changed

- `CAPACITY_OVERLOAD` → `ALLOCATION_LOAD`: the same count, stated as how
  work was distributed rather than as a judgement of whoever holds it.
- The trend report's `engagement_trend`, computed by counting
  individually degrading people, → `response_latency_trend`: median
  time-to-reply across the program, with no per-person breakdown.
- Outreach advice no longer branches on a grade. It states latency as a
  scheduling allowance and changes the plan rather than the person.
- A request that was never answered is now recorded as nothing, instead
  of counted against someone.

### Added

- **`lltpm integrations`** — the path from a provider's API to durable
  evidence: `sources`, `normalize`, `ingest`, `verify`, `record`.
  Read-only against the provider by construction; a test asserts the
  only HTTP verb the reader can name is GET. (Commits 37–38)
- **The anti-corruption layer** (`integrations/normalizers.py`). Before
  it, no adapter produced an `EvidenceEnvelope`, so no real integration
  data could reach any engine. Keys on the provider's own event id,
  never on the clock; refuses to guess a project or invent an event
  class.
- **Recorded provider contracts** following Fowler's self-initializing
  fake: real payloads recorded once, replayed offline, with `verify` as
  the occasional live check. The contract is the set of fields the
  normalizers read, so a new GitHub field cannot fail the build and a
  renamed `merged_at` must. Each fixture declares whether it is
  `recorded`, `derived` or `hand_written`, and `verify` reports the
  latter two as unverifiable rather than passing.
- **Operational logging** (`observability/`): JSON-lines and human
  formatters, format-time credential redaction, a closed structured-field
  vocabulary. Libraries emit through the standard library and configure
  nothing. (Commit 36)
- **`lltpm runtime journal --follow`** — tail the journal as records
  land; read-only, no broker, no daemon.
- **`lltpm runtime anchor`** and `verify --anchor`. Hash chaining makes
  an in-place edit detectable; it cannot detect a history rewritten
  wholesale, because every link in a rewritten chain recomputes.
  Verification now says which of the two it checked. (Commit 39)
- **Murphy's Brier decomposition** — reliability, resolution and
  uncertainty reported separately, which catches the forecaster that
  always predicts the base rate: perfect calibration, zero resolution,
  and flawless by expected calibration error alone.
- **`docs/research-alignment.md`** — the design checked against
  CloudEvents 1.0, Snodgrass bitemporality, Haber–Stornetta hash
  chaining, Fellegi–Sunter record linkage, Murphy's decomposition,
  Pearl's noisy-OR, Evans' anti-corruption layer, Fowler's
  self-initializing fake and W3C PROV-O, with the open limitations
  listed rather than discovered later.
- `python -m lltpm integrations`/`runtime` share `--log-level` and
  `--log-format`.

### Fixed

- **The idempotency key included a timestamp.** Published webhook
  guidance is unanimous that the key must be the provider's event id,
  because the same delivery can arrive with a different timestamp. Keys
  are now the delivery id, the Events API event id, or a polled
  resource's identity plus its version. Re-reading a source appends
  nothing.
- **A credential pasted into a pull request body would have become
  permanent journal history.** The envelope promised to carry no secrets
  but enforced it only on metadata keys, not on content.
- **A latent timezone crash at the ingest boundary.** The runtime's clock
  is naive UTC and every provider emits an offset; mixing them raised
  `TypeError` inside temporal ordering, far from the cause.
- `lltpm runtime ingest` printed "published revision None" whenever a
  pass completed without publishing.
- `evidence_records_appended` counted envelopes handed in rather than
  records that became durable, so a redelivered webhook was reported as
  new work.
- `assess_decision_propagation` accepted a `superseded_decision_ids`
  argument and never read it, while claiming every stale recipient
  reflected a superseded decision.
- The dashboard command started a real server from inside the test
  suite, which is why the suite hung on Python 3.13 until the serve call
  was extracted behind a seam. (`d7ad749`)
- The journal was written outside the container's data volume by
  default, so durable history was destroyed on restart.

### Documentation

- The no-worker-scoring guarantee was stated for the whole system while
  the guardrails only covered part of it. Commit 40 measured the gap and
  inventoried what broke the claim; Commit 41 removed it. The invariant
  is now one statement, and `tests/test_person_measurement_scope.py`
  asserts it everywhere.
- Packaging metadata for a public release: authors, keywords,
  classifiers for every supported Python, and repository/issue links.

## 0.31.0 — durable journal and deterministic replay

- Append-only hash-chained event journal, SQLite reference backend,
  snapshots, and replay that reconstructs intelligence from history
  without touching any external system. (Commit 31)
- `lltpm runtime` — the organizational stack becomes reachable from the
  command line, offline, with no API key. (Commit 33)
- Canonical identity resolution on the ingest path: an explicit alias
  resolves, a name resemblance does not, and an ambiguous match is left
  for a person to settle. (Commit 34)
- The real engines wired behind the live orchestrator, with
  `lltpm runtime coverage` reporting honestly which artifact types run a
  dedicated engine and which are thinner derived projections.
  (Commit 32)
- MIT licence, CI across Python 3.11–3.13, and history export.
  (Commit 35)

## 0.30.0 and earlier — the organizational-reasoning stack

Built over Commits 1–30: bitemporal truth, project understanding, KPI
intelligence, launch readiness, decision capsules and authority policy,
bounded autonomy, the probabilistic scenario lattice, organizational
interaction and miscommunication modelling, portfolio topology and
cross-project simulation, procedural memory, calibration, and the live
orchestrator that composes them.

Before that, the original TPM surface: the LLM tool-use loop, meeting
gate, artifact management, integrations, dashboard, security and JWT
auth, voice, and deployment packaging.
