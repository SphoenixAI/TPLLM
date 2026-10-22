# Security

## Reporting a vulnerability

Please report security issues privately rather than opening a public
issue. Use GitHub's private vulnerability reporting on this repository,
or email the maintainers.

Please include what an attacker could do, the smallest reproduction you
have, and the version or commit you tested. We will acknowledge receipt
and tell you what we find, including if we disagree that it is a
vulnerability.

## What this project already does

These are design commitments, not aspirations, and they have tests.

**Credentials never enter durable state.** OAuth tokens, refresh tokens,
JWT secrets, webhook signing keys, API keys and passwords are refused at
the model level — both in evidence envelopes and in the journal, which
is where a leak would be permanent. Key-shaped metadata names and
credential-shaped values are both rejected. Secrets are generated at
runtime and held in the existing secrets infrastructure.

**Durable history is hash-chained.** Each journal record links to its
predecessor, so accidental mutation, truncation, a partially written
backup, or a manual `UPDATE` against the database file is detectable
from the head. This is not a defence against an attacker with write
access — it is detection of corruption, and it reports rather than
repairs.

**Replay cannot act on the world.** Rebuilding history never re-sends a
message, re-files a ticket, or re-finalizes a decision. The fence is
enforced at the outbound boundary, and suppressed effects are counted
rather than silently dropped.

**Autonomous authority is bounded and cannot self-expand.** The agent
cannot modify its own allowed decision types, allowed projects, risk
ceiling, confidence threshold or autonomy budget. Authority is
re-checked at the moment of execution, with the kill switch evaluated
first, and every policy and kill-switch change is journalled with its
actor.

**Raw third-party content is not mirrored by default.** Retention policy
defaults to normalized evidence plus a source pointer and content hash,
not a permanent copy of everyone's Slack.

**Durable payloads are JSON, never pickle.** Nothing in durable storage
deserializes into code execution, and a user can read their own history
with ordinary tools.

## Known boundaries

Worth being explicit about, since none of these are bugs:

- The hash chain detects corruption; it does not prevent a party with
  write access to the database from rewriting history and re-chaining
  it.
- The reference SQLite deployment stores data unencrypted on local disk.
  Disk encryption is the operator's responsibility.
- `tpllm runtime export` produces the organizational record in plain
  JSON. Treat an export as sensitive even though it contains no
  credentials.
- Baseline providers (`runtime/baselines.py`) are keyword and structural
  heuristics, labelled as such in the reproducibility manifest. They are
  a floor for running offline, not a security control and not a
  substitute for reading.

## Scope

In scope: credential leakage into durable state or logs, authority
escalation by the agent, replay causing external side effects, journal
integrity bypass, anything that lets derived state be presented as
durable truth.

Out of scope: the strength of the baseline heuristics, resource
exhaustion from inputs you control yourself, and findings that require
an attacker who already has write access to your database file.
