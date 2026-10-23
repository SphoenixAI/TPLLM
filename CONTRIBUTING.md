# Contributing to TPLLM

Thanks for looking. This document is short on ceremony and specific
about the few things that actually matter in this codebase.

The [engineering handbook](docs/engineering-handbook.md) documents the
layering rules and invariants. Read it before changing package boundaries.
The [research alignment](docs/research-alignment.md) and
[threat model](docs/threat-model.md) explain the evidence and deployment limits.

## Getting set up

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/ -q

# See the organizational stack actually run, offline and with no API key
python -m tpllm runtime ingest examples/evidence.json --db /tmp/demo.db
python -m tpllm runtime status --db /tmp/demo.db
```

Use `python -m pip` and `python -m pytest` rather than the bare
executables. On some systems `pip` is bound to a different interpreter
than `python`, which installs the dependencies where the tests cannot
see them — a confusing hour we have already spent for you.

The test suite is large and fast — a few thousand tests in under a
minute. Run all of it before opening a PR.

## The invariants

TPLLM is an opinionated system, and most of its opinions are enforced by
tests rather than left to review. If one of these fails, the test is
usually right.

**Dependency direction.** `tests/test_architecture_boundaries.py` builds
a first-party import graph by parsing source and asserts it stays
acyclic. `core`, `temporal`, `identity`, `structural` and `persistence`
import no other first-party package. `runtime` composes the domains and
nothing imports `runtime`. No domain imports `persistence` or `sqlite3`.
Read that file before moving an import between packages.

**Probability and confidence are separate numbers.** "70% likely, held
with 30% confidence" is a valid and useful statement. Averaging them
destroys both. Probability and impact are separate too: a 4%
catastrophic path can matter more than a 60% inconvenience.

**Pruned possibilities keep their mass.** If a forecast drops
low-probability branches, the omitted mass is reported. Displayed
futures must never appear to sum to 100% when they do not.

**Arrival order is not temporal authority.** A webhook that lands late
carries an old timestamp and must not move an already-decided decision
back to pending. Reality time and knowledge time are distinct
throughout.

**Intelligence is published as coherent revisions.** A partial recompute
never becomes current. A KPI from one computation shown beside a
scenario from another, under one heading, is the failure mode this
exists to prevent.

**Autonomous action is re-validated at the moment of execution.**
Validation and finalization happen inside one guarded call, kill switch
first. Nothing may widen the agent's own authority.

**The journal is append-only.** Corrections are new records — a reopen,
a supersession — never an edit. Derived state is always rebuildable from
it, and `persistence` stores JSON, never pickle.

**Replay touches nothing external.** Rebuilding history must not re-send
a message, re-file a ticket, or re-finalize a decision.

## The product guardrail

TPLLM models organizational mechanics: capacity constraints, dependency
state, authority ambiguity, information divergence, decision age,
evidence freshness. It does not model individual productivity, and it
never will.

Concretely, do not add: time spent per person, screen or browser
activity, message volume as a performance signal, hours worked,
utilization, employee efficiency or ranking, personality or competence
predictions, or anything timesheet-shaped. Source-level tests assert the
absence of these across the runtime and persistence layers, including in
prose.

Capacity is a structural resource estimate — "Platform review has room
for approximately two decisions this week" — never a statement about
what a named individual can produce.

The question this system answers is *what does the organization need to
understand or decide?* Not *what was each person doing?*

## Writing a change

- Match the surrounding code: its naming, its comment density, its idiom.
- Comments should say why, not what. Most of this codebase's comments
  explain a decision or a trap, because that is what a reader cannot
  recover from the code.
- Prefer composing existing primitives over adding parallel ones.
- New behaviour needs tests that would fail without it. A test asserting
  a function returns what it returns is not one.
- Where a component refuses to act — no declared capacity, no claim
  extractor, a stale recommendation — say why in the error. "Missing
  input" is less useful than "a bottleneck can only be reported from a
  real constraint".

## Reporting a problem

Open an issue with what you expected, what happened, and the smallest
input that shows it. If it involves durable state, `tpllm runtime verify`
and `tpllm runtime export --what decisions` are usually the two most
useful things to attach — and neither can change anything.

Please do not attach a database containing real organizational data. The
export command exists partly so you can send a shape rather than a
secret.
