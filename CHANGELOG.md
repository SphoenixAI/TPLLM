# Changelog

Notable changes, newest first.

Each entry says what changed and, where it matters, what was wrong
before. Several entries record the removal of something that shipped —
those are the ones worth reading.

## Unreleased

Folded into a version by the release-candidate pass.

### Public-release cleanup

- **Private session links were committed in three recorded fixtures.**
  A pull request body written by a tool carries a link to the session
  that wrote it, and the GitHub fixtures were recorded from this
  project's own pull requests. A session URL is not a credential, so the
  secret scan passed it; the fixture scrubber removes values *by name*
  and nobody can name a string they did not know was there. The
  scrubber now also redacts this one category by pattern, with the
  trade-off stated, `integrations record` refuses a fixture that still
  discloses one, and a release test checks every tracked file.
- **Internal commit numbering is gone from the public surface.** The
  project was built over a numbered commit arc and the numbers had
  reached 268 docstrings and comments in shipped source — "Application
  requires Commit 7's single green light", "the Commit 24 lattice".
  Precise internally, meaningless to a reader of a repository whose
  history has hashes. Each was replaced with the name of the thing the
  number stood for, and a test keeps them from returning.
- **Renamed to TPLLM throughout** — Technical Program Manager, LLM.
  `LLTPM` was a transposition that had become the dominant spelling:
  the package, the CLI command, all eleven environment variables, the
  default database name and the repository itself, with TPLLM surviving
  only in newer docstrings. Renamed wholesale rather than half-way,
  because nothing is published and there is no user to keep
  compatible — a reader installing `lltpm` to run a thing called TPLLM
  would have been a permanent paper cut. 2,473 occurrences across 456
  files; `src/lltpm/` is now `src/tpllm/`, `tpllm` is the command, and
  the version key stamped into journal metadata changed with it, so a
  database created before this is not readable by this build.
- **`docs/assistant-development-guide.md` became
  [`docs/engineering-handbook.md`](docs/engineering-handbook.md)**, a
  contributor document rather than a set of instructions to an
  assistant, with a short `docs/assistant-development-guide.md` pointing at it.
- **`.gitignore` did not cover the files the documentation tells you to
  create.** `tpllm.db`, `run.jsonl`, `sbom.json` and `.env` were all
  committable. The journal is an organization's durable record, so
  following the quickstart could have meant publishing it. Tested with
  `git check-ignore`, including that `.env.example` still ships.

### Added

- **Slack as an evidence source.** Messages and thread replies; an edit
  becomes new versioned evidence rather than a mutation; a delete
  becomes a source-availability change rather than the disappearance of
  a fact. Reactions, DMs and bot messages are declined with a stated
  reason instead of ingested as something they are not. GET-only reader,
  rate-limit aware, HMAC-SHA256 signature verification over the raw body
  with a replay window.
- **Jira as an evidence source.** REST v3 with token pagination.
  Transitions come from the changelog and are never inferred from a
  snapshot; `statusCategory.key` is read in preference to the display
  name, which teams rename; only a `Blocks` link read from the blocked
  side becomes a dependency; worklogs and `timespent` are refused
  outright because they are hours-per-person by construction.
  `JIRA_WEBHOOK_LIFECYCLE` records that Cloud dynamic webhooks expire
  after 30 days, so a future receiver cannot quietly let registrations
  lapse.
- **A provider registry** (`integrations/providers.py`). One descriptor
  per source declares its capabilities, scope shape and normalizer entry
  point, and both CLI groups dispatch through it — a fourth provider is
  a descriptor rather than another branch. `integrations sources` prints
  capabilities and contract provenance as two tables.
- **An `official_documentation` contract-provenance tier.** Slack and
  Jira could not be reached from the environment this was built in, so
  their contracts come from the providers' own published examples.
  Rather than let those pass as recordings, provenance is ranked:
  `recorded` > `official_documentation` > `derived` > `hand_written`,
  and `verify` reports anything unrecorded as unverifiable.
- **`tpllm runtime ingest` now says what the intelligence concluded**,
  per project: what moved, which evidence moved it, which scenarios
  changed and whether a decision is required. The orchestrator always
  computed this and the command discarded it, so the useful half of the
  work was reachable only from Python. `--no-explain` reduces it to the
  counts; a test asserts that choice changes no durable state.
 
- **Security headers** on every dashboard response, with the CSP's
  limits stated rather than implied: the page's script is inline, so the
  policy does not mitigate XSS and the escaping is the control.
 
- **`docs/threat-model.md`** — what the system withstands, what it does
  not, and the configurations that are explicitly unsupported, including
  that multi-tenancy is absent rather than partial.
- **`docs/security-audit.md`** — history scan, per-extra dependency
  audit, CycloneDX SBOM, and what each result does and does not mean.
- **A self-testing history scanner** (`scripts/scan_history.py`). Its
  first version reported a confident zero while running only its own
  regexes, because `detect-secrets` returns nothing outside a
  `default_settings()` context. It now plants known synthetic
  credentials and refuses to report on a history its detectors cannot
  find them in. Findings are tiered, because entropy at line granularity
  cannot tell a long identifier from a key.

### Fixed

- **Colliding envelope ids.** `make_envelope` derived the id from the
  first sixteen characters of the idempotency key, which was a digest
  until normalizers were allowed readable keys — after which eight envelopes
  shared two ids. No test caught it because the journal deduplicates on
  the key rather than the id. The id is now a digest of the whole key.
- **A refresh token was accepted as an access token.** `dashboard.py`
  validated bearer credentials without checking `token_type`, so a
  leaked seven-day refresh token granted a week of API access.
  `validate_jwt` now takes `expect_type`, **defaulting to `"access"`**,
  so a future caller is safe without knowing this history.
- **A real DOM XSS in the dashboard.** Task titles, assignees, sprint
  names and workload keys were interpolated into `innerHTML` unescaped,
  and `priority`/`status` landed in a `class` attribute. Escaped at
  every render site; the attribute values are whitelisted against a
  closed enum rather than escaped, because an attribute context is one
  quoting mistake from breakout. The tests execute the escaping in Node
  against live payloads, since a helper that returned its input
  unchanged would satisfy any source-level check.
- **Log redaction was weaker than evidence redaction.** It replaced a
  value that *was* a credential, not one embedded in a longer string, so
  `reason="Authorization: ghp_…"` — what a failed provider read actually
  produces — passed through intact while the same string in a pull
  request body was redacted. The weaker rule was in the likelier place.
- **Atlassian `ATATT` tokens were unrecognised** by both layers, which
  Jira made a realistic paste into append-only history.
- **Development files shipped in the sdist.** The release audit
  inspected only the wheel; the sdist is built from a different file set and carried
  the triage file, CI config and deployment artefacts.
- Evidence-derived text printed to the terminal is escaped: a Jira
  summary of `[red]URGENT[/red]` prints as itself rather than being
  consumed as a style tag.

### Documentation

- **The README is now one document** rather than a chronology: the
  problem, the thesis, the attention ladder, the loop, an offline
  quickstart that runs a real organizational scenario and quotes its
  real output, what the system is *not*, per-provider evidence status,
  the architecture and its invariants. CI asserts the quoted claims
  still hold — that the output names which evidence moved it, that a
  probability move never travels without its confidence, and that
  reality time is the time of the fact.
- **The dashboard's authentication surface is now stated accurately.**
  The project advertised "FastAPI with JWT authentication": true of the
  machinery, false of the surface, where 25 of 28 routes enforce no
  credential. The open set is enumerated in
  `dashboard.UNAUTHENTICATED_READ_ROUTES`, a test fails if it changes, a
  test asserts every mutating route is guarded, and `create_app` warns
  at startup because the shipped container binds `0.0.0.0`. A first
  attempt at measuring this said "6 of 28" by scanning a fixed number of
  characters after each handler, which truncated the longer bodies; the
  count is now taken from the AST.

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
  authority. A letter grade destroys it.
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

- **`tpllm integrations`** — the path from a provider's API to durable
  evidence: `sources`, `normalize`, `ingest`, `verify`, `record`.
  Read-only against the provider by construction; a test asserts the
  only HTTP verb the reader can name is GET.
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
  nothing.
- **`tpllm runtime journal --follow`** — tail the journal as records
  land; read-only, no broker, no daemon.
- **`tpllm runtime anchor`** and `verify --anchor`. Hash chaining makes
  an in-place edit detectable; it cannot detect a history rewritten
  wholesale, because every link in a rewritten chain recomputes.
  Verification now says which of the two it checked.
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
- `python -m tpllm integrations`/`runtime` share `--log-level` and
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
- `tpllm runtime ingest` printed "published revision None" whenever a
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
  the guardrails only covered part of it. An audit measured the gap and
  inventoried what broke the claim, and the next release removed it. The
  invariant
  is now one statement, and `tests/test_person_measurement_scope.py`
  asserts it everywhere.
- Packaging metadata for a public release: authors, keywords,
  classifiers for every supported Python, and repository/issue links.

## 0.31.0 — durable journal and deterministic replay

- Append-only hash-chained event journal, SQLite reference backend,
  snapshots, and replay that reconstructs intelligence from history
  without touching any external system.
- `tpllm runtime` — the organizational stack becomes reachable from the
  command line, offline, with no API key.
- Canonical identity resolution on the ingest path: an explicit alias
  resolves, a name resemblance does not, and an ambiguous match is left
  for a person to settle.
- The real engines wired behind the live orchestrator, with
  `tpllm runtime coverage` reporting honestly which artifact types run a
  dedicated engine and which are thinner derived projections.
 
- MIT licence, CI across Python 3.11–3.13, and history export.
 

## 0.30.0 and earlier — the organizational-reasoning stack

Bitemporal truth, project understanding, KPI
intelligence, launch readiness, decision capsules and authority policy,
bounded autonomy, the probabilistic scenario lattice, organizational
interaction and miscommunication modelling, portfolio topology and
cross-project simulation, procedural memory, calibration, and the live
orchestrator that composes them.

Before that, the original TPM surface: the LLM tool-use loop, meeting
gate, artifact management, integrations, dashboard, security and JWT
auth, voice, and deployment packaging.
