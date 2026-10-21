# Research alignment

This document checks LLTPM's design against the published work it
overlaps with: where it agrees, where it deliberately goes further, and
where reading the literature exposed a gap that has since been fixed.

It exists because several of LLTPM's invariants were arrived at from
first principles while building, and a design that has never been
checked against prior art is one assertion away from reinventing a
known mistake. Three sections below record real changes made as a
result; two record places where LLTPM departs from the standard answer
on purpose, with the reason and the cost.

Each section states what was verified against a primary source and what
rests on secondary summaries, because that distinction is the point.

---

## 1. The evidence envelope and CloudEvents

**The literature.** CloudEvents 1.0 defines four required context
attributes — `specversion`, `id`, `source`, `type` — and optional
`subject`, `time`, `datacontenttype`, `dataschema`. Two clauses matter
here. Producers *must* ensure `source` + `id` is unique per distinct
event, and a duplicate re-send may keep the same `id`, so consumers may
treat a matching pair as a duplicate. And `time` is defined as *when
the occurrence happened*; where that cannot be determined a producer
may substitute another time, such as the current time, provided all
producers for a `source` are consistent. (Verified against the
spec text at `cloudevents/spec` v1.0.2.)

**What LLTPM does.** `EvidenceEnvelope` carries the same identifying
quartet under different names — `source_system` + `source_type` +
`source_id` and `event_type` — plus an explicit `idempotency_key` that
plays the role of the uniqueness constraint rather than leaving it
implicit in a pair of fields.

| CloudEvents 1.0 | `EvidenceEnvelope` |
|---|---|
| `id` | `idempotency_key` (explicit, not derived from the payload) |
| `source` | `source_system` + `source_id` |
| `type` | `event_type`, drawn from a closed vocabulary |
| `subject` | `project_ids`, declared by binding rather than by the producer |
| `time` | `observed_at` — reality time only |
| — | `received_at` — knowledge time, which the spec has no slot for |
| `dataschema` | the recorded contract in `integrations/contract_data/` |

**Where it goes further, and why.** The `time` clause is the one place
LLTPM declines to follow the spec. Substituting the current time when
the occurrence time is unknown is reasonable for a message bus, where
`time` is mostly for ordering and tracing. It is not reasonable here:
`observed_at` feeds a bitemporal reconstruction, and recording a
February merge as having happened today corrupts every scenario rooted
in it. `to_naive_utc` therefore raises `TimestampMissingError` rather
than defaulting, and the payload is reported as rejected. The spec's
substitution is an availability trade; LLTPM takes the correctness side
of it and loses the event instead.

The second addition is the split between `observed_at` and
`received_at`, which CloudEvents has no field for. See §3.

---

## 2. Idempotency under at-least-once delivery

**The literature.** The published guidance on webhook delivery is
unusually consistent. Exactly-once delivery is unachievable over plain
HTTP because an acknowledgement can be lost, so receivers must make
repeats harmless. The recommended key is the *provider's own event
id* — not a payload hash and not a timestamp, because both can
legitimately differ across what is semantically the same delivery. The
id should be recorded atomically with the side effect rather than in a
check-then-act sequence, which has a race. And deduplication alone is
insufficient: the handler must itself be idempotent, or a crash between
marking an event seen and acting on it loses the event. GitHub's
`X-GitHub-Delivery` is a GUID that stays the same across a redelivery
of the same event, which is what makes it usable as the key. (These
rest on vendor documentation and practitioner write-ups rather than a
single authoritative source; `docs.github.com` was unreachable from the
build environment, so GitHub's retry semantics specifically were not
verified first-hand and are not relied on below — only the stability of
the delivery id across a redelivery, which several sources agree on.)

**What LLTPM does — and the gap this surfaced.** The journal already
satisfied the atomicity clause: an append validates the stream
sequence, checks idempotency, allocates the sequence, computes the hash
link and inserts inside one transaction, so the key and the durable
effect commit together or not at all. The handler clause was satisfied
too, by journalling first and promoting last.

The *key derivation* was wrong. `build_idempotency_key` hashed
`source_system | source_id | event_type | observed_at.isoformat()` —
a timestamp, which is precisely what the guidance says not to use. The
failure mode is exactly the one described: a payload whose timestamp
differs across two deliveries of the same fact produces two keys and
therefore two pieces of evidence, and anything derived from counting
them is then wrong. Nothing in the codebase had hit it because the only
caller supplied a stable timestamp of its own.

The fix was to make the key an explicit parameter and have each shape
declare its own stable identity:

| Shape | Key | Why |
|---|---|---|
| Webhook delivery | `X-GitHub-Delivery` | the provider's event id, stable across redelivery |
| Events API object | the event's `id` | same, for a pulled stream |
| Polled REST resource | `node_id` + `updated_at` | resource identity plus resource version |

The process clock appears in none of them. Two tests enforce it: one
normalizes the same payload an hour apart and asserts the key and
envelope id are identical, and one re-ingests at the durable boundary
and asserts the journal grew by nothing. Making the key clock-dependent
fails three tests, including the journal one.

The event class is part of the key, because one pull request
legitimately produces a request-for-review and later a merge; those are
two organizational facts, not two versions of one.

---

## 3. Bitemporality

**The literature.** Snodgrass' *Developing Time-Oriented Database
Applications in SQL* (Morgan Kaufmann, 2000) distinguishes **valid
time** — when a fact was true in the modelled reality — from
**transaction time** — when it was stored. The two are orthogonal; a
table carrying both is *bitemporal*. A transaction-time table can be
reconstructed as of a past point; a valid-time table answers questions
about when things were true. (Verified via the publisher's description
and chapter listing plus secondary sources that quote the definitions;
the book text itself was not retrieved.)

**What LLTPM does.** The distinction is load-bearing throughout, under
domain names: `observed_at` / `occurred_at` / `valid_from` is valid
time, `received_at` / `known_at` / `known_from` is transaction time, and
the two are never collapsed. Two consequences the architecture tests
enforce:

- **Arrival order is not temporal authority.** A late-arriving event
  cannot move an already-decided decision back to pending. In Snodgrass'
  terms, a new transaction-time entry does not rewrite valid-time
  history.
- **Replay is a transaction-time reconstruction.** Rebuilding the
  journal reproduces what was concluded then, which is kept distinct
  from what today's engines would conclude from the same evidence. The
  reproducibility manifest decides which question is being asked.

`known_at = max(received_at, observed_at)` clamps rather than rejects a
source whose clock disagrees, because the evidence is real even when the
timestamp is not.

**A latent bug this framing caught.** The runtime's clock is naive UTC
throughout; every provider emits RFC 3339 with an offset. Mixing them
raises `TypeError` on the first comparison — which in this codebase
happens inside the orchestrator's temporal ordering, far from the
cause. Conversion is now done once, at the ingest boundary, by
`to_naive_utc`, with a test that the result is comparable with the
runtime clock.

---

## 4. Tamper-evident history

**The literature.** Haber and Stornetta, *How to Time-Stamp a Digital
Document* (CRYPTO '90; Journal of Cryptology, 1991), timestamp the data
rather than the medium, so that changing one bit is apparent. Their
linking scheme ties each document's timestamp to the hash of the one
processed before it, which makes later insertion or deletion
impossible; verification recomputes the hash and compares against the
chain. Crucially, the service also combined each week's hashes into one
value **published in the New York Times** — an anchor outside the
system, retained where it could not be retracted. (Verified via the
paper's abstract and multiple secondary summaries; the full text was not
retrieved. The public-anchoring detail is consistently reported.)

**What LLTPM does — and the gap this surfaced.** The journal is
hash-chained exactly as described: each record links to its
predecessor's hash, `verify_journal_hash_chain` walks the chain and
reports the first break, and corruption is reported rather than
repaired.

Reading the paper made the missing half obvious. Chain verification
detects an *in-place* edit — the link breaks, or a record stops matching
its own hash — which covers the realistic failures: a bad restore, a
partial write, a manual `UPDATE`. It does not detect a history that was
**rewritten wholesale**, because whoever rewrites the file can recompute
every link after the record they changed. The chain then verifies
cleanly. Before this commit, nothing in LLTPM said so, and the
documentation's "append-only hash-chained journal" invited the stronger
reading.

Three changes:

1. `JournalAnchor` — the head sequence and head hash, as a frozen
   object the operator retains out of band. `lltpm runtime anchor`
   emits it; `lltpm runtime verify --anchor` checks against it.
2. Verification now says what it checked. Without an anchor the
   explanation reads *"Internal consistency only: no anchor was
   supplied, so a wholesale rewrite would not be detected."*
3. An export carries its head hash, so a recipient can anchor it
   against a value given to them separately rather than re-verifying it
   against itself.

The honesty test is the uncomfortable one: `test_journal_anchor.py`
forges a record, recomputes the whole chain, asserts that chain
verification **passes**, and then asserts the anchor catches it.

**Residual limitation, stated.** An anchor stored beside the database it
anchors protects against accident, not against rewriting — the CLI says
this on every `anchor` run. A true Haber–Stornetta anchor is published
where the holder cannot retract it. LLTPM's reference deployment is one
local SQLite file with no external service, so the strongest honest
mechanism available is operator-retained. Publishing to an external
timestamping authority would be the next step and is not implemented.

---

## 5. Identity resolution

**The literature.** Fellegi and Sunter's record-linkage theory (1969)
scores each candidate pair and applies **two** cutoffs: above the upper
one a pair is a match, below the lower one a non-match, and pairs
between them fall in the *no-decision* or **clerical review** region and
go to a human. Their weights are optimal in the sense of minimising
expected Type I error at a given Type II level. The model assumes
conditional independence of field agreements, which often fails in
practice. (Verified via Census Bureau and ISI working papers that quote
the construction; Fellegi and Sunter's original paper was not
retrieved.)

**What LLTPM does.** The identity invariant was written as "an
ambiguous match is left unlinked for a person to settle; merging two
colleagues who share a surname is worse than reporting an unlinked
identity." That is the clerical review region, arrived at
independently. `EntityResolutionStatus` has exactly the three outcomes
the theory predicts — `RESOLVED`, `AMBIGUOUS`, unresolved — with a
person threshold of 0.98, a curated alias scoring 0.90 and a
canonical-name resemblance scoring 0.25, so an alias resolves where a
resemblance does not.

**Where it goes further.** Three ways, all driven by the fact that
LLTPM's errors are organizational rather than statistical:

- **Asymmetric cost, not symmetric optimality.** Fellegi–Sunter
  minimises error at a chosen operating point. Here the two errors are
  not comparable: a false merge silently attributes one person's
  authority to another and corrupts the capacity and
  information-flow models downstream, while a missed link shows up as a
  reported unlinked identity somebody can fix. The thresholds are set
  for that asymmetry rather than for a balanced point.
- **The ambiguity is durable.** A clerical review queue is usually
  operational state. Here the resolution decision — including the
  decision *not* to link — is journalled, so replay does not re-resolve
  from scratch and does not need the source systems to still answer.
- **No field-weight model, so no independence assumption.** The known
  weakness of the Fellegi–Sunter weights is that field agreements are
  usually dependent. LLTPM scores by explicit evidence class (a curated
  alias, an exact external id, a name resemblance) rather than by a
  product of per-field likelihood ratios, so it never relies on
  conditional independence. The cost is that the thresholds are
  declared rather than estimated from data, which is weaker where
  labelled training pairs exist — and LLTPM has none.

---

## 6. Probability honesty and forecast evaluation

**The literature.** The Brier score is a *proper* scoring rule: a
forecaster minimises expected score only by reporting its true belief.
Murphy's decomposition splits it into **reliability** (miscalibration),
**resolution** (discrimination) and **uncertainty** (the base rate's own
variance), as `BS = REL − RES + UNC`. The decomposition generalises to
any proper scoring rule. A finite-sample decomposition is known to be
optimistic about reliability and resolution, so the empirical terms can
mislead. The modern framing evaluates sharpness subject to calibration
rather than accuracy alone. (Verified via arXiv surveys on proper
scoring rules and classifier calibration and a Climate-Dynamics paper
on estimating the two terms; Murphy's 1973 paper itself was not
retrieved, so the exact formulas were cross-checked against two
independent secondary presentations.)

**What LLTPM already did.** `forecast_metrics.py` reported Brier score,
log loss, expected calibration error and sharpness *separately*, with
the stated reason that "a model that predicts 0.51 for everything can be
accurate and useless." That is the sharpness-subject-to-calibration
paradigm under a different name. The probability-honesty invariants go
beyond scoring: probability and confidence are separate quantities,
probability and impact are separate, and pruned scenario branches keep
their mass (`ScenarioTailMass`) so displayed futures never falsely sum
to 100%.

**What the literature added.** The decomposition was missing, and it is
the one that catches the specific failure LLTPM most needs to catch: a
forecaster that always predicts the base rate has *perfect* reliability
and *zero* resolution. Expected calibration error alone would score it
flawless. `brier_decomposition` now reports all three terms,
`is_better_than_the_base_rate` states the honest bar, and the quality
report says in words when the bar was not met.

Two implementation details follow directly from the caveats:

- The identity is exact only for a forecaster emitting finitely many
  distinct probabilities. Binning a continuous forecast leaves a
  within-bin variance term, so `residual` is reported rather than
  presenting an approximation as an identity. A test asserts the four
  terms recombine to the Brier score, and that the residual is non-zero
  for spread-out forecasts.
- `sample_count` and `bucket_count` travel with the numbers, because the
  empirical decomposition is optimistically biased on small samples and
  a reader needs to know what it was computed from.

---

## 7. Combining independent causes

**The literature.** Noisy-OR (Pearl 1988; Good 1961) models causal
independence: each cause produces its own binary contribution and the
effect is the logical OR of them, so the effect fails only when every
contribution fails. It makes the parameter count linear in the number of
causes instead of exponential. The canonical form usually carries a
**leak** (Henrion 1987) — an always-on cause that lets the effect occur
when every modelled cause is absent. (Verified via JAIR and UAI papers
on exploiting causal independence; Pearl's 1988 text was not retrieved,
so the leak's formulation rests on secondary sources.)

**What LLTPM does.** `combine_independent_transmissions` is noisy-OR:
two routes at 0.6 give 0.84, not 1.2. The project adds a rule the
canonical model does not have — two routes through the *same* shared
resource describe one mechanism and are counted once — because the
independence assumption is false for a shared approval queue, and
counting it twice manufactures diversification that does not exist. The
same principle appears in scenario sampling: one draw per distinct
uncertainty, fanned out to every affected project.

**A deliberate departure.** There is no leak term. A leak is a true
statement about the world — a project can slip for reasons outside the
graph — and it is still left out, because it would give every project a
non-zero propagated impact with no mechanism behind it. The cost is
stated in the code and in a test: these probabilities under-estimate
total risk rather than over-estimating it. For a tool asking a person to
act, that is the safer direction to be wrong in, and "no modelled
mechanism" should read as no claim rather than as a floor the system
invented.

---

## 8. The translation boundary

**The literature.** Evans' anti-corruption layer (*Domain-Driven
Design*, ch. 14) is a translation boundary between a bounded context and
an external system whose model differs, so the external vocabulary never
contaminates the internal model. Its roles are translation, a facade
over the external system's complexity, and protocol adaptation; with it,
changes in the supplier's model affect only the translation mechanism.
It is the downstream counterpart to an Open Host Service: the consumer
translates because the upstream offers no clean protocol. (Verified via
O'Reilly's context-mapping chapter and several secondary summaries;
Evans' own chapter was not retrieved.)

**What LLTPM does.** `integrations/normalizers.py` is exactly this, and
the fact that it was missing was the finding: every adapter returned its
own shapes (`PRSummary`, `BuildStatus`, `SlackMessage`), the journal
accepted only `EvidenceEnvelope`, and `WrappedToolAdapter` had always
been the seam and took a `normalize` callable that no production code
ever supplied. Real GitHub data could reach the LLM tool loop and
could not reach a single engine.

The layer adds three refusals the pattern does not prescribe, each
because the alternative is a specific false statement:

- **It never guesses a project.** Bindings are declared; an unbound
  repository is reported, not filed under a plausible project. This is
  the identity invariant applied to scope.
- **It never invents an event class.** The target vocabulary is closed
  and asserted against what the runtime actually maps. A push, a
  comment-only review and a draft pull request are reported as
  uninterpreted *with a stated reason*, rather than coerced into the
  nearest-looking class.
- **It never copies the payload.** Named fields only, so a future
  provider field cannot become durable without review — and a
  credential in a pull request body cannot become permanent history.

---

## 9. Contract testing

**The literature.** Fowler's *Self Initializing Fake*: a fake that, on
first use, forwards the call to the real service and keeps the response
for later calls, plus a separate suite that makes the fakes call the
real service and confirms the saved values still match. He also notes
stripping parameters that change between calls without affecting
results before looking up stored values. (Verified against
martinfowler.com/bliki/SelfInitializingFake.html.)

**What LLTPM does.** Both halves, as explicit commands rather than
hidden in a test run: `lltpm integrations record` is the recording half,
`lltpm integrations verify` the occasional live check. The reader sits
behind a port with a live and a recorded implementation, so the offline
suite and the live check drive the same code rather than two paths that
merely agree.

Fowler's parameter-stripping note has a direct analogue already in the
codebase: `comparable_payload` strips `evidence.received_at` before
comparing two journal payloads, because a redelivery carries a new
arrival time and is still the same fact. The convergence is worth
recording — the same problem produced the same answer in two places.

**Where it goes further.** Three ways:

- **The contract is the consumer's read set, not the payload.** It
  records the fields the normalizers actually read with the types they
  may take, so a new GitHub field cannot fail the build and a renamed
  `merged_at` must. Recording the whole payload shape would invert both.
  An AST walk asserts every field the normalizer reads is in the
  declared set, so a new read cannot escape the contract.
- **Provenance is per fixture.** `recorded`, `derived` or
  `hand_written`, with the derivation stated. Only a recording is
  evidence about a live API, and `verify` reports the others as
  *unverifiable* rather than passing. Two of the four GitHub payload
  kinds are currently unverifiable, and the report says so per kind
  instead of printing one reassuring green line.
- **Recordings name nobody.** A two-pass scrubber collects every handle
  first and rewrites it everywhere it appears, including the URL fields
  GitHub embeds it in; account ids are rewritten, avatars and emails
  dropped, opaque ids pseudonymized deterministically so a re-recording
  diffs as an API change rather than churn. `record` refuses to write a
  fixture that still discloses something. Fowler's pattern is silent on
  this, because his recordings were not being committed to a repository
  about to be open-sourced.

---

## 10. Provenance

**The literature.** W3C PROV-O (Recommendation, 2013) models provenance
with three core classes — `prov:Entity`, `prov:Activity`,
`prov:Agent` — and relates them with `prov:wasDerivedFrom` (entity to
entity, without naming the activity), `prov:wasGeneratedBy`,
`prov:wasAttributedTo` (entity to agent) and `prov:wasAssociatedWith`
(activity to agent). (Verified via the W3C PROV-O generated
documentation and the W3C blog's update post.)

**What LLTPM does.** The export's provenance structures map onto PROV-O
without having been designed from it:

| PROV-O | LLTPM |
|---|---|
| `prov:Entity` | a `DurableEvidenceRecord`; a published artifact |
| `prov:Activity` | a recompute, identified by `revision_id` |
| `prov:Agent` (SoftwareAgent) | the engine, identified by `method_version` and the reproducibility manifest |
| `wasDerivedFrom` | `artifact.evidence_ids` |
| `wasGeneratedBy` | `artifact.revision_id` |
| `wasAttributedTo` | `manifest.scenario_method_version` / `recommendation_method_version` |

**Where it goes further.** PROV-O has no notion of an entity whose
source has become unretrievable. `EvidenceProvenance.source_available`
going false does not invalidate the evidence — it was real and it was
used — but a reader deserves to know the original artifact can no longer
be fetched, and replay reports it as an evidence limitation rather than
reaching for a source that may be gone. A straight PROV-O export would
present a dangling reference as a healthy one.

No RDF serialization is emitted. The export is JSON for the same reason
the journal is: a person investigating what their agent did should be
able to open the file and read it.

---

## 11. Open limitations

Stated here rather than discovered later.

- **The anchor is operator-retained, not published.** §4. An external
  timestamping authority would be strictly stronger and is not
  implemented.
- **Two of four GitHub contracts are unverifiable.** The repository
  recorded from has no submitted reviews and no issue that is not a pull
  request, so those contracts are hand-written and derived
  respectively. `lltpm integrations sources` says so.
- **Identity thresholds are declared, not estimated.** §5. With labelled
  pairs, Fellegi–Sunter weights would be better founded. LLTPM has no
  labelled pairs.
- **No leak term in propagation.** §7. Risk is under-estimated, on
  purpose.
- **The empirical Brier decomposition is biased on small samples.** §6.
  Reported with its sample count rather than corrected.
- **`datetime.utcnow()` remains in 86 call sites across 38 files** in the
  original TPM surface, scheduled for removal from Python. It is deliberately not a
  blanket find-and-replace: `utcnow()` returns naive UTC and
  `datetime.now(UTC)` returns aware, and the bitemporal layer is
  sensitive to the difference (§3). The new ingest boundary is already
  explicit about it.
- **Jira and Slack have no normalizers yet.** Only GitHub does. Their
  endpoints are unreachable from the current build environment, so a
  recorded contract cannot be made for them, and writing adapters whose
  contracts could never be verified would be the kind of unverifiable
  surface this document argues against.

---

## Sources

- CloudEvents 1.0.2 specification — <https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md>
- Martin Fowler, *Self Initializing Fake* — <https://www.martinfowler.com/bliki/SelfInitializingFake.html>
- Haber & Stornetta, *How to Time-Stamp a Digital Document* (CRYPTO '90) — <https://www.gwern.net/doc/bitcoin/1991-haber.pdf>
- Bayer, Haber & Stornetta, *Improving the Efficiency and Reliability of Digital Time-Stamping* (1992) — <https://www.math.columbia.edu/~bayer/papers/Timestamp_BHS93.pdf>
- Fellegi–Sunter record linkage, as presented in US Census Bureau research reports — <https://www.census.gov/content/dam/Census/library/working-papers/1999/adrm/rr99-04.pdf>
- Winkler, *Record Linkage* (FCSM) — <https://www.fcsm.gov/assets/files/docs/J1_Winkler_2013FCSM.pdf>
- Snodgrass, *Developing Time-Oriented Database Applications in SQL* (Morgan Kaufmann, 2000) — <https://shop.elsevier.com/books/developing-time-oriented-database-applications-in-sql/snodgrass/978-0-08-050422-3>
- *Proper scoring rules for estimation and forecast evaluation* — <https://arxiv.org/pdf/2504.01781>
- *Classifier Calibration: a survey* — <https://arxiv.org/pdf/2112.10327>
- Ferro & Fricker, *Estimating reliability and resolution of probability forecasts* — <https://researchonline.lse.ac.uk/id/eprint/39006>
- Zhang & Poole, *Exploiting Causal Independence in Bayesian Network Inference* (JAIR 1996) — <https://arxiv.org/pdf/cs/9612101>
- Heckerman & Breese, *Causal Independence for Knowledge Acquisition and Inference* — <https://www.microsoft.com/en-us/research/uploads/prod/1993/07/Causal-Independence-for-Knowledge-Acquisition-and-Inference.pdf>
- Evans' anti-corruption layer, via *What is Domain-Driven Design?* ch. 4 (Context Mapping) — <https://oreilly.com/library/view/what-is-domain-driven/9781492057802/ch04.html>
- W3C PROV-O — <https://www.w3.org/TR/prov-o/>
- Standard Webhooks, on using the delivery id as the idempotency key — <https://www.standardwebhooks.com/>
