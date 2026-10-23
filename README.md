# TPLLM — an AI Technical Program Manager

TPLLM reads what a program's source systems already know — pull
requests, tickets, messages, meeting notes — and maintains a current,
explainable account of where the program stands, what changed, and what
needs a human decision. It is built to replace status meetings with
something more useful than a summary: a reasoned position with its
evidence attached.

The reference deployment is one SQLite file on a laptop. No cloud
account, no broker, no daemon, and the core install needs no API key.

---

## The problem it addresses

A technical program manager's hardest work is not writing updates. It is
holding a model of the program in their head — the dependency graph, who
is waiting on whom, which risks have quietly stopped being hypothetical
— and keeping it current while the program changes underneath them.

That model degrades in predictable ways:

- **It is reconstructed from meetings**, so it is only as fresh as the
  last one, and the meeting exists mainly to rebuild it.
- **It cannot hold the whole graph.** Four projects sharing one approval
  authority is invisible from inside any one of them.
- **It loses its evidence.** By the time a risk is stated out loud,
  nobody can say which observation produced it or when.
- **It mistakes arrival order for truth.** The thing mentioned most
  recently sounds like the thing that happened most recently.

## The thesis

**Evidence in, intelligence out, and almost nothing demands attention.**

Every input becomes an immutable piece of evidence with two timestamps:
when the fact happened, and when we found out. From that history the
engines derive a position — understanding, scenarios, decisions,
dependencies, portfolio topology — and publish it as one coherent
revision.

Then the interesting part: most of it is routed to nobody. Each pass
chooses the weakest rung that is still honest.

```
silent                 nothing moved that anyone needs to know
update_internal_state  the model changed; no human action follows
async_information      worth reading, not worth interrupting for
decision_request       a named decision is now blocking
high_priority_alert    this cannot wait for the next read
```

A system that escalates everything has only moved the meeting. The
ladder is the product.

## The loop

```
   GitHub · Jira · Slack · meeting notes · documents
                       │
                       ▼
       ┌─────────────────────────────┐
       │  normalizers                │  refuses to guess a project,
       │  provider payload →         │  invent an event class, or
       │  EvidenceEnvelope           │  stamp `now` on a past fact
       └─────────────────────────────┘
                       │   keyed on the provider's own event id,
                       │   so re-reading a source appends nothing
                       ▼
       ┌─────────────────────────────┐
       │  append-only journal        │  reality time and knowledge
       │  hash-chained, replayable   │  time, kept separately
       └─────────────────────────────┘
                       │   journal first, promote last
                       ▼
       ┌─────────────────────────────┐
       │  engines                    │  understanding · scenarios
       │  one coherent revision,     │  decisions · dependencies
       │  never half-recomputed      │  portfolio topology
       └─────────────────────────────┘
                       │
                       ▼
       ┌─────────────────────────────┐
       │  attention routing          │  the weakest rung that is
       │  silent → … → alert         │  still honest
       └─────────────────────────────┘
```

Two properties of that picture are load-bearing. Evidence is journalled
**before** anything derives from it, so a failed recompute loses no
facts and a later `replay` rebuilds the projection. And every derived
thing is rebuildable from the journal alone — delete the projections and
replay; the result is the same, with external effects fenced off.

---

## Five minutes, offline

No API key, no account, one file. The two engines that would otherwise
need a model fall back to deterministic baselines, labelled as baselines
in the reproducibility manifest so their output is never mistaken for
model-derived output.

```bash
git clone https://github.com/SphoenixAI/TPLLM && cd TPLLM
python -m pip install -e .

python -m tpllm runtime ingest examples/evidence.json \
  --db tpllm.db --organization acme
```

[`examples/evidence.json`](examples/evidence.json) is three observations
across two projects, the kind of thing a week produces:

| From | Reality time | What it said |
|---|---|---|
| Slack | Oct 19, 09:00 | Billing activation depends on Platform approving the contract standard |
| Jira | Oct 19, 09:30 | Platform Architecture is in incident response; review capacity is reduced |
| Meeting | Oct 19, 08:58 | Identity owns the migration; the target is 60% activation |

Nobody in that week said "billing is at risk". The ingest says what
moved, and which observations moved it:

```
Journalled 3 evidence record(s).
published revision revision-1 across 2 project(s)
  attention: async_information

billing
scenario  scenario_lattice changed materially. P(blocker_persisted) moved
          0% → 45%. The estimate changed materially, but its confidence
          is 0.00 — treat it as a signal to investigate, not as a firm
          number. P(blockers_cleared) moved 0% → 55%. […]
changed   decision state projected from reconstructed state for 2 […]
changed   decision recommendation projected from reconstructed state […]
changed   Scenario lattice rebuilt for 2 project(s).
changed   scenario decision recommendation projected from […]
because   Meeting transcript at 2026-10-19T08:58:00.
because   Architecture evidence at 2026-10-19T09:00:00.
because   Blocker opened at 2026-10-19T09:30:00.
```

(`[…]` marks text cut to fit this page; nothing is reordered. A second
block for `identity` follows, which the engines compute the same way.)

Read what that output refuses to do. It moved a probability and then
**said its confidence is zero in the same breath** — the baseline
generator is structural, not predictive, and a 45% with no confidence
attached is a prompt to go look, not a forecast. It named which evidence
moved it. It called one artifact a *projection* rather than letting it
pass for a dedicated engine. And it routed the whole thing to
`async_information`: worth reading, not worth a meeting.

The structural reading is the useful one here. Billing is not slow.
Billing is downstream of an approval authority that is currently
absorbed by an incident — a fact about the approval authority, which
stays true and stays actionable. Converted into a judgement about the
billing team, the same evidence becomes both less true and less useful.

Then inspect the history it kept:

```bash
python -m tpllm runtime journal --db tpllm.db
```

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Event                         ┃ Occurred         ┃ Projects          ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ 1 │ evidence_accepted             │ 2026-10-19T09:00 │ billing           │
│ 2 │ evidence_accepted             │ 2026-10-19T09:30 │ billing           │
│ 3 │ evidence_accepted             │ 2026-10-19T08:58 │ identity          │
│ 4 │ intelligence_revision_publis… │ 2026-10-22T19:24 │ billing, identity │
└───┴───────────────────────────────┴──────────────────┴───────────────────┘
```

The evidence occurred on October 19th. The conclusion was reached when
the command ran — the fourth row will carry the day you run it, while
the first three stay on the 19th forever. Both times are kept,
separately, which is what makes "what did we believe last Tuesday, and
on what basis" an answerable question rather than an archaeology
project.

The rest of the surface:

```bash
# Current revision, journal head, storage health
python -m tpllm runtime status --db tpllm.db --organization acme

# Verify the hash chain (read-only; reports corruption, never repairs it)
python -m tpllm runtime verify --db tpllm.db

# Retain the head hash out of band, so a wholesale rewrite is detectable
python -m tpllm runtime anchor --db tpllm.db

# Rebuild every projection from the journal (dry run unless --publish)
python -m tpllm runtime replay --db tpllm.db --organization acme

# Checkpoint, so future replays start here instead of at record 1
python -m tpllm runtime snapshot --db tpllm.db --organization acme

# Which artifact types run a dedicated engine, and which are projections
python -m tpllm runtime coverage

# Durable history as JSON, carrying its own integrity verdict
python -m tpllm runtime export --db tpllm.db --organization acme \
  --what decisions

# Watch records land, like tail -f (read-only; Ctrl-C to stop)
python -m tpllm runtime journal --db tpllm.db --follow
```

`replay` reports what it suppressed:

```
│ Replayed:   4 record(s) (1..4)                                   │
│ Suppressed: 1 external effect(s)                                 │
```

Rebuilding history must not re-send a message or re-finalize a decision.
Counting what the fence caught is what makes that checkable rather than
merely promised. (Run it twice and the second pass reports the evidence
as already processed, which is the same discipline seen from the other
side.)

### Watching the process itself

`--log-level` adds the process's own account of itself on **stderr** —
which engine ran, what was deduplicated, why a revision was not
promoted — so redirecting stdout keeps both streams usable.

```bash
python -m tpllm runtime --log-level info --log-format json \
  ingest examples/evidence.json --db tpllm.db 2> run.jsonl
```

Log lines carry the ids that join them back to durable history
(`revision_id`, `journal_sequence`, `causal_event_id`,
`organization_id`), and anything a replay produces is marked
`execution_mode=replay`, so reconstruction is never mistaken for live
work. Credential-shaped material is redacted at format time.

The log and the journal are deliberately not the same thing. The journal
records **what happened to the organization**: durable, ordered,
hash-chained, replayed. The log records **what the process did**:
transient, cheap, safe to lose. No log line is authoritative, none is
ever replayed, and turning logging off changes no result — a test
asserts it.

---

## Reading a real source system

`tpllm integrations` is the path from a provider's API to durable
evidence. It is read-only against the provider by construction: each
reader can issue nothing but GET, and a test asserts no write verb is
nameable in those modules.

```bash
# What can be read, and how good the evidence for each contract is
python -m tpllm integrations sources

# See what a payload would become — offline, no network, no database
python -m tpllm integrations normalize \
  tests/fixtures/github/pull_requests.json \
  --kind pull_request --repository acme/atlas-service --project atlas
```

```
5 envelope(s); 0 payload(s) produced none
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ Event class ┃ Occurred (reality time) ┃ Source               ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ pr_merged   │ 2026-02-25T05:45        │ acme/atlas-service#8 │
│ pr_merged   │ 2026-02-25T05:30        │ acme/atlas-service#7 │
│ …           │ …                       │ …                    │
└─────────────┴─────────────────────────┴──────────────────────┘
```

Merged in February, read today, recorded as February. Then the live
path. Pass the credential through the environment rather than the
`--token` argument the commands also accept: an argument lands in shell
history and in the process list, which is a worse place for a token than
anything the code does with it.

```bash
read -rs TPLLM_GITHUB_TOKEN && export TPLLM_GITHUB_TOKEN

python -m tpllm integrations ingest --repository acme/atlas \
  --project atlas --db tpllm.db --organization acme

# Do the recorded contracts still match the live API?
python -m tpllm integrations verify --repository acme/atlas
```

Slack reads `TPLLM_SLACK_TOKEN` (or `SLACK_BOT_TOKEN`) and Jira reads
`TPLLM_JIRA_API_TOKEN` the same way. A token never enters the journal, a
log line, a fixture, an export or an exception message, and each of
those five routes is closed and tested separately in
`tests/test_provider_security.py` — the one that leaks is always the one
nobody enumerated.

### Three things a normalizer refuses to do

Each alternative is a specific false statement, so each is a reported
refusal rather than a best guess:

- **It never guesses a project.** Bindings are declared. An unbound
  repository, workspace or Jira project is reported, not filed under a
  plausible-looking project.
- **It never invents an event class.** A push, a comment-only review, a
  draft pull request, a Slack reaction, a Jira worklog — each is
  reported as uninterpreted with a stated reason. Merged code stays
  technical evidence; it is not promoted to feature completion.
- **It never stamps `now` on a past fact.** A payload with no usable
  timestamp is rejected rather than dated today.

Idempotency follows the same discipline: the key is the provider's own
event id — a webhook delivery id, an Events API event id, or a polled
resource's identity plus its version — and never a clock, a timestamp or
a payload hash. **Reading the same repository twice appends nothing.**

### Where each source actually stands

Evidence quality differs between providers, so it is reported per
provider rather than flattened into "supported".

| Source | Read path | Contract evidence | Webhook signature | Overall |
|---|---|---|---|---|
| **GitHub** | verified against the live API | **recorded** from live responses (pull requests, events); issues derived from those recordings, reviews hand-written | not implemented | **live verified** |
| **Jira** | implemented — REST v3, token pagination | **official documentation** — six payload kinds from Atlassian's published examples | implemented, never seen a real delivery | **contract verified, awaiting live verification** |
| **Slack** | implemented — Web API, rate-limit aware | **official documentation** — six payload kinds from Slack's published examples | implemented, never seen a real delivery | **contract verified, awaiting live verification** |

The reason is boring and worth stating: `slack.com` and
`api.atlassian.com` were unreachable from the environment this was built
in. Rather than let documentation-derived fixtures pass as recordings,
provenance is a first-class property — `recorded` outranks
`official_documentation`, which outranks `derived` and `hand_written`,
and `integrations verify` reports anything unrecorded as *unverifiable*
rather than as passing. `integrations sources` prints the whole table.

Moving Slack and Jira to **live verified** needs network egress and a
read-scoped token; nothing in the code is waiting.

### Keeping the recordings honest

The offline tests run against payloads recorded from live APIs, in the
shape of Fowler's self-initializing fake: record once, replay offline,
re-check the recordings on their own schedule. A contract is the set of
fields the normalizers **actually read**, with the types they may take —
so a new GitHub field cannot fail the build, and a renamed `merged_at`
must.

Recorded fixtures name nobody. Handles, account ids and repository names
are pseudonymized before anything is written, and
`integrations record` refuses to write a fixture that still discloses
something.

---

## What TPLLM is not

Stated plainly, because each of these is a thing a reader could
reasonably assume.

- **Not a tool for measuring people.** It measures dependency
  congestion, approval queues, information divergence, capacity
  constraints, decision latency, KPI state, context handoff depth and
  organizational topology. It does not grade anyone's responsiveness,
  reliability or collaboration, and it does not name who "was the
  blocker". This is enforced, not just intended:
  [`tests/test_person_measurement_scope.py`](tests/test_person_measurement_scope.py)
  asserts that no model anywhere has a person as its subject *and* a
  grade, score, rate or hours count, that no agent tool can be asked for
  a verdict on a colleague, that the dashboard serves no per-person
  review, and that the log field vocabulary stays a closed list. An
  earlier version of this project had a quarterly-evaluation surface;
  removing it made the system more capable, not less, for the reason the
  scenario above shows.
- **Not a replacement for GitHub, Jira or Slack.** The evidence path
  only reads: `github_reader`, `slack_reader` and `jira_reader` can
  issue nothing but GET, and a test asserts no other verb is nameable in
  those modules. Nothing writes to a pull request or a Jira issue at
  all. The TPM surface is a separate thing and *does* send — Slack and
  Google Chat messages, email digests, and Linear or Notion items when
  those are configured — which is how an async update reaches anyone
  instead of a meeting doing it. Reading and sending are different
  modules on purpose.
- **Not a hosted multi-tenant service.** There is no tenant isolation —
  not partial, none. `organization_id` exists on records but nothing
  below the query layer enforces it.
  [`docs/threat-model.md`](docs/threat-model.md) §5 lists what a hosted
  mode would need.
- **Not safe to expose to a network.** 25 of the dashboard's 28 routes
  enforce no authentication, the set is enumerated in code, and a test
  fails if it changes. The shipped container binds `0.0.0.0`; the CLI
  binds loopback. See [`docs/threat-model.md`](docs/threat-model.md) §1.
- **Not a forecasting engine that hides its uncertainty.** Probability
  and confidence are separate numbers and both are displayed. Pruned
  scenario branches keep their mass, so displayed futures never falsely
  sum to 100%.
- **Not finished.** `memory`, `evaluation`, `application`, `structural`,
  `ontology` and `advisory` are built and tested but not yet on the live
  path. `runtime coverage` reports which artifact types run a dedicated
  engine and which are thinner projections, rather than letting a
  projection pass for an engine.

---

## Architecture

Two layers. The **TPM surface** is the agent, its tools and the
artifacts it produces. Underneath sits a **layered
organizational-reasoning stack**.

The layering is enforced rather than described:
[`tests/test_architecture_boundaries.py`](tests/test_architecture_boundaries.py)
builds the first-party import graph by parsing source and asserts it
stays acyclic, that the foundations import nothing first-party, and that
nothing imports the orchestrator.

```
src/tpllm/
│   # ── Foundations: import no first-party package ──
├── core/            shared domain primitives
├── temporal/        bitemporal truth — reality time vs knowledge time
├── identity/        cross-system entity resolution
├── structural/      causal/structural graph engine
├── persistence/     journal, snapshots, replay, SQLite reference backend
│
│   # ── Domain intelligence ──
├── intelligence/    project understanding, KPIs, launch readiness
├── coordination/    decisions, authority policy, bounded autonomy
├── scenarios/       probabilistic scenario lattice
├── portfolio/       cross-project topology, capacity, collisions
├── memory/          procedural coordination memory
├── evaluation/      historical replay, forecast calibration
├── application/     governed calibration application, auto-revert
│
│   # ── Orchestration: composes the domains; nothing imports it ──
├── runtime/         dedupe, temporal ordering, coalesced recompute,
│                    coherent revisions, materiality, causal compression,
│                    attention routing, durability seam
│
│   # ── Application infrastructure: imports no first-party package ──
├── observability/   log formatting, field vocabulary, redaction
│
│   # ── The TPM surface ──
├── models/          Pydantic data models
├── workflows/       meeting gate, risk engine, sprint, standup, security
├── integrations/    two kinds, deliberately separate modules:
│                    *_reader.py + *_normalizer.py — evidence in, GET only
│                    *_integration.py — outbound: Slack, Google Chat,
│                      email, Calendar, Linear, Notion, Zoom, voice
├── artifacts/       artifact store
├── tools/           LLM tool definitions
└── dashboard.py     FastAPI dashboard with JWT auth and RBAC
```

### Invariants

These are asserted by tests, and relaxing one is a design decision
rather than a refactor.

- **Dependency direction.** The foundations import no first-party
  package; no domain imports `persistence` or `sqlite3`; nothing imports
  `runtime`.
- **Journal first, promote last.** Durable changes are appended before
  the runtime treats them as real. Derived state is always rebuildable.
- **Arrival order is not temporal authority.** A late-arriving event
  cannot move an already-decided decision back to pending.
- **Coherent revisions.** Intelligence is published as a revision
  computed against one evidence cutoff; a partial recompute never
  becomes current.
- **Probability honesty.** Probability, confidence and impact are three
  separate quantities. Pruned branches keep their mass.
- **No stale autonomous action.** Validation and finalization happen in
  one guarded call, kill switch checked first, and nothing widens its
  own authority.
- **Identity is resolved conservatively.** A curated alias resolves; a
  name resemblance does not. An ambiguous match is left unlinked for a
  person to settle, because merging two colleagues who share a surname
  is worse than reporting an unlinked identity.
- **Replay touches nothing external.** Rebuilding history never
  re-sends a message or re-finalizes a decision, and the suppressions
  are counted.
- **An idempotency key never depends on our clock.**
- **Reality time is the time of the fact.**
- **A normalizer guesses nothing.**
- **Logs are not a second journal.**
- **No worker scoring or activity tracking.**

### Durable storage

The reference deployment is one local SQLite file. Every backend sits
behind a protocol, and `persistence` imports no other first-party
package.

```bash
cp tpllm.db tpllm.db.backup   # back up everything
```

A hash chain detects an in-place edit. It cannot detect a history
rewritten wholesale, because whoever rewrites the file recomputes every
link. That is what `runtime anchor` is for: retain the head hash
somewhere the database is not, and `verify --expect-head` compares
against it. Unanchored, `verify` says in its own output what it did not
check rather than implying more than it verified.

---

## The TPM agent surface

The agent runs a tool-use loop over the workflow engines using your chosen
provider and model. Set `TPLLM_PROVIDER` to `anthropic` or `openai` (for an
OpenAI-compatible API), and set `TPLLM_MODEL` to a model ID your endpoint
accepts. Neither has a default. Anthropic uses `ANTHROPIC_API_KEY`;
OpenAI-compatible endpoints use `OPENAI_API_KEY` and optionally
`OPENAI_BASE_URL`. Install `tpllm[openai]` for the OpenAI-compatible backend.
The offline runtime needs none of these settings.

```bash
python -m tpllm init          # choose provider/model and enter credentials
source .env                  # load the generated environment
python -m tpllm chat          # interactive
python -m tpllm ask "Set up a program called Atlas V2 to rebuild auth"
python -m tpllm daily atlas   # the daily cadence brief
python -m tpllm pulse atlas   # aggregated program pulse
python -m tpllm risks atlas   # risk signal scan
python -m tpllm today         # today's focus
python -m tpllm tools         # every tool the agent can call
python -m tpllm dashboard     # local web dashboard
```

The **meeting gate** is the surface where the thesis is most visible.
Every meeting request is evaluated against four criteria — a named
decision owner, written options and trade-offs, an available pre-read,
and a decide-by date. If any fail, the agent proposes the async
workflow instead of booking the meeting.

Risk detection runs on every pass rather than once a week in a meeting,
scanning for schedule compression, dependency drift, unmitigated
high-severity risks, dependency clustering on one team, and milestones
due but not started. There is no daemon: a pass happens when a command
runs or a scheduler runs one.

This is also the layer that sends. An async update reaches people
through Slack, Google Chat or an email digest, and Linear or Notion
items where those are configured — the alternative to the meeting has to
arrive somewhere.

---

## Install

Not yet published to a package index; install from source.

```bash
git clone https://github.com/SphoenixAI/TPLLM && cd TPLLM
python -m pip install -e .                      # core: stack, CLI, journal
```

The core install needs no account and no network. Everything else is an
extra, so nothing heavy is pulled in by default:

```bash
python -m pip install -e ".[integrations]"   # GitHub, Jira, Slack, Calendar
python -m pip install -e ".[dashboard]"      # the FastAPI dashboard
python -m pip install -e ".[email]"          # SendGrid delivery
python -m pip install -e ".[openai]"         # an OpenAI-compatible backend
python -m pip install -e ".[pdf]"            # PDF export (WeasyPrint)
python -m pip install -e ".[voice]"          # local TTS backends
python -m pip install -e ".[all]"            # all but pdf, voice, dev
```

`all` deliberately leaves out `pdf` and `voice`: both pull in large
native dependencies and neither is needed to run the stack.

Requires Python 3.11, 3.12 or 3.13.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
```

Use `python -m pip` and `python -m pytest` rather than the bare
executables — on some systems `pip` is bound to a different interpreter
than `python`, which installs the dependencies where the tests cannot
see them.

CI runs the suite on 3.11, 3.12 and 3.13, plus four jobs that exist so a
specific kind of regression is visible in the checks list rather than
buried in a passing suite: the architecture boundaries, release
readiness, the security and secret-scanning pass, and an offline
quickstart that executes the commands this README promises.

[`docs/engineering-handbook.md`](docs/engineering-handbook.md) covers
conventions, wiring state, and which invariant each test file defends.
Read it before moving an import between packages.

---

## Design, security and limits

- [`docs/research-alignment.md`](docs/research-alignment.md) — where the
  design overlaps published work (CloudEvents, Snodgrass bitemporality,
  Haber–Stornetta hash chaining, Fellegi–Sunter record linkage, Murphy's
  Brier decomposition, Pearl's noisy-OR, Evans' anti-corruption layer,
  Fowler's self-initializing fake, W3C PROV-O), including three places
  where reading the literature exposed a real gap, two deliberate
  departures, and §11's open limitations.
- [`docs/threat-model.md`](docs/threat-model.md) — what the system
  withstands, what it does not, and the configurations that are
  explicitly unsupported.
- [`docs/security-audit.md`](docs/security-audit.md) — the history scan,
  dependency audit, SBOM, the dashboard XSS that was found and fixed,
  the authentication findings, and what is still uncovered.
- [`docs/release-audit.md`](docs/release-audit.md) — what shipped, every
  dependency's licence, and which public claims were checked.
- [`docs/engineering-handbook.md`](docs/engineering-handbook.md) — the
  layering rules, every architectural invariant, and the test file
  that defends each one.
- [`SECURITY.md`](SECURITY.md) — reporting route.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) and
  [`CHANGELOG.md`](CHANGELOG.md).

Nothing in those documents uses "secure" as a state. Each says what was
checked, what the result means, and what was not checked.

## Licence

MIT. See [`LICENSE`](LICENSE).
