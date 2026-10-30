# Phase 2: dual-lane intelligence

Jev judges. Reasoning models synthesize. TPLLM governs.

Phase 2 extends the existing Phase 1 implementation. It preserves the
`DecisionEngine`, provider adapter, ten decision definitions, probabilistic
payloads, calibration gate, validator and historical decision journal. It adds
explicit task selection, a separate reasoning engine, common evidence lineage,
cohorts, review events and evaluation. Evaluation is off by default.

```text
                         TPLLM
                           │
                  Evidence + Ontology
                           │
                    Intelligence Task
                           │
              ┌────────────┴────────────┐
              │                         │
       Decision Engine           Reasoning Engine
              │                         │
             Jev                  LLM Provider
              │                         │
     Noul / Choice / Score       Structured Output
              │                         │
      probabilities               typed artifact
              └────────────┬────────────┘
                           │
                  Evaluation / Policy
                           │
                    validate_insight
                           │
                       Authority
```

This is the conceptual governance order, not an execution pipeline from a
model response. In this implementation, Phase 2 results stop at the journal and
Intelligence Review. Existing Phase 1 insight candidates still go through the
unchanged `validate_insight()` gate. An explanation can describe an existing
insight and its validation outcome; it cannot publish or upgrade an insight.
Only existing deterministic TPLLM authority policy and its kill switches control
external actions. Confidence, calibration status, reviews and comparisons grant
no permission.

## Repository map

The Phase 1 repository map in [decision-intelligence.md](decision-intelligence.md)
remains applicable: ontology, evidence claims, KPI observations, reconciliation,
trends, recommendations, bitemporal state, authority, and hash-chained persistence
are reused. The existing generative agent can invoke tools, so it is deliberately
not instantiated by the new inference-only reasoning adapter. Its provider
configuration and SDK dependencies are reused.

| Component | Implementation |
|---|---|
| Task and envelope contracts | `src/tpllm/intelligence_tasks/models.py` |
| Task catalog | `intelligence_tasks/registry.py` |
| Versioned reasoning registry and schemas | `intelligence_tasks/reasoning_registry.py`, `schemas.py` |
| Evidence packet / richer context | `intelligence_tasks/evidence.py`; reuses `decisions/state.py` |
| Explicit sampling | `intelligence_tasks/config.py` |
| Inference-only structured adapters | `reasoning/providers.py` |
| Local schema/reference validation | `reasoning/engine.py` |
| Task orchestration and batching | `runtime/intelligence_service.py` |
| Append-only persistence | `runtime/intelligence_journal.py`; existing journal and SQLite backend |
| Per-lane evaluation | `application/intelligence_evaluation.py` |
| Background product seam | `product_intelligence.py` |
| Scoped review routes | `product_api.py` |
| Existing UI extension | `web/intelligence.js`, `app.js`, `index.html`, `app.css` |
| Repeatable runner / isolated preview | `evals/intelligence/run.py`, `demo.py` |

Package dependencies remain acyclic. Neither the decision, reasoning, nor shared
task packages import execution tools, coordination authority or runtime services.
Runtime adapters compose the lower-level engines and stores.

## Intelligence task catalog

All definitions below are version 1 and active for explicit evaluation. All
support the eight cohorts listed below; support does not imply demonstrated
quality in every cohort. The default sampled selection includes three bounded
decision tasks and root-cause analysis only.

| Task | Lane | Output contract |
|---|---|---|
| `evidence.sufficient.v1` | Decision | Noul |
| `evidence.contradictory.v1` | Decision | Noul |
| `trend.direction.v1` | Decision | Choice |
| `trend.materiality.v1` | Decision | Score |
| `risk.present.v1` | Decision | Noul |
| `risk.family.v1` | Decision | Choice |
| `risk.severity.v1` | Decision | Score |
| `impact.scope.v1` | Decision | Choice |
| `intervention.value.v1` | Decision | Score |
| `review.human_recommended.v1` | Decision | Noul |
| `reasoning.root_cause_analysis.v1` | Reasoning | `RootCauseV1` |
| `reasoning.operational_summary.v1` | Reasoning | `OperationalSummaryV1` |
| `reasoning.dependency_analysis.v1` | Reasoning | `DependencyAnalysisV1` |
| `reasoning.recovery_options.v1` | Reasoning | `RecoveryOptionsV1` |
| `reasoning.insight_explanation.v1` | Reasoning | `InsightExplanationV1` |

Definitions carry version, lane, purpose, required context, evidence policy,
output contract, schema version, cohort policy, sensitivity, evaluation policy
and provider constraints. Duplicate IDs are rejected. Serialized definitions and
schemas are retained with each result. Fingerprint fixtures lock v1 semantics.
Adding materially different semantics requires v2 and its own local validator.
`hybrid_evaluation` is reserved as an envelope lane, not an automated router.

## Two lanes and native schema enforcement

Decision results retain Noul probability, full Choice distribution, winner,
margin and confidence, and full Score distribution, expected score and legend.
The only additive Phase 1 trace field is `attempts` (with a default for legacy
records). Provider errors now retain bounded attempt counts without exception
text. Historical records require no migration.

Reasoning artifacts contain usable synthesis, unknowns and references. Root-cause
analysis adds finite cause tags and operational families for selective overlap;
these are model claims, not probabilities. Recovery options describe authority
requirements but cannot establish that those requirements are satisfied. No
schema includes a calibrated-confidence field or action payload.

The adapter uses Anthropic `output_config.format` and OpenAI-compatible
`response_format: json_schema` with `strict: true`, through the existing SDKs.
The Anthropic minimum is raised to 1.0 for its native schema argument; no new SDK
is introduced. Unsupported endpoints fail closed; there is no prompt-only JSON,
tool-call, or provider fallback. Schemas use a portable native subset while local
Pydantic validation retains enum, string, collection and required-field bounds.

Official contracts checked on October 30, 2026:
[Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
and [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
These document the wire mechanisms; they are not evidence of TPLLM provider
performance. Model IDs remain explicitly configured by the operator.

## Shared evidence and temporal integrity

An `EvidencePacket` contains the Phase 1 compact `DecisionState`, cohort
assignment, allowed entity/task/KPI/trend/dependency/ontology references,
optional redacted excerpts, and an optional already-created insight.
Its ID hashes the full bounded snapshot. The envelope retains that ID, the
lane-specific context hash, source references, valid time, knowledge/transaction
cutoff, and inference creation time.

`DecisionStateBuilder` retains compact fields. `ReasoningContextBuilder` adds
bounded excerpts, relationships, chronology, allowed IDs and an existing insight
when available. The representations differ but originate from the same packet.
Neither receives the other model's output, review labels, fixture expectations or
later outcome events as inference context.

Source valid time and collection time must both be at or before the selected
cutoffs. Observations require known supporting sources and their own valid and
reported timestamps. Unstamped or future KPI projections and relationships are
omitted. The product's `references` edges are rederived from selected source
versions; semantic dependencies are not inferred from a mention. Source-backed
KPI observations are retained without promoting drafts or target prose into
measurements. Insight explanations retain the existing validator result.

Packets contain at most 20 source facts/excerpts, bounded observations and
relationships, 1,000 characters per optional excerpt, and 64 KB overall. Provider
responses are bounded to 64 KB and locally constrained. Excerpts default off.
Secret redaction and journal secret rejection are reused. Connector text remains
untrusted data; trusted code owns system instructions and schemas. Unknown
evidence, entity, task, ontology and insight IDs are rejected, including nested
recovery/path references. ID validity does not establish entailment.

Historical contexts are read from retained packets. The live product projection
does not reconstruct every historical source version for a new backdated run.
Missing historical context remains missing; it is never filled with current data.

## Cohorts

The small v1 taxonomy is `software.deployments`, `software.reliability`,
`project.dependencies`, `project.schedule`, `security.events`, `team.capacity`,
`cost.variance`, and `general.operations`. Assignments record cohort ID/version,
method and basis. Explicit manual selection, trusted ontology metadata and
entity types precede a deterministic deployment-status rule and a general
fallback. Connector prose never assigns cohorts. Both lanes inherit one packet's
assignment. Product cases without trusted classification remain in the fallback
cohort; fixtures are explicitly assigned, not classified by a model.

## Append-only history, reviews and outcomes

Six additive runtime-audit event kinds retain packets, task envelopes, reviews,
outcome attachments, comparisons and coverage. Phase 1 event kinds remain intact.
Replay restores both histories without inference, tool calls or authorization.
Journal IDs, hashes, organization/project scope and existing backend durability
are reused. Review corrections must supersede the latest review; optimistic
stream concurrency rejects competing corrections. Output artifacts never change.

Decision review labels include agree, disagree, ambiguous, insufficient evidence
and a corrected label. Reasoning labels include accept, partially correct,
reject, missing evidence, unsupported inference and corrected interpretation.
Only valid explicit decision labels enter probabilistic evaluation. Demo reviews
retain fixture provenance and do not count as real human acceptance.

Outcomes attach only to an existing `OUTCOME_RECORDED` event with a recognized
structured `outcome_type`, matching organization/project and timestamps. The
existing `append_outcome()` accepts this optional type without changing old
payloads. Free-form prose is not parsed into a structured outcome. Recognized
types cover recovered/rolled-back deployments, slipped releases, delivered
dependencies, confirmed/dismissed incidents and baseline KPI recovery. Mapping
an outcome to a decision reference value is explicit; no speculative outcome
inference is implemented. Attachments at T1 cannot change T0 packets.

## Evaluation and comparison semantics

Decision metrics reuse Phase 1 Brier scores, reliability buckets, false-positive
and false-negative rates, Choice top-1 accuracy/top-2 coverage/multiclass Brier
and margins, and Score absolute error/ranked probability score. Segments retain
provider, requested/resolved model, task ID/hash, cohort ID/version and provenance.
Repeated inference on one packet does not increase independent quality samples.

Reasoning tracks schema and reference validity with explicit denominators,
unsupported-reference rate, human acceptance/correction/missing-evidence rates,
completion, failures and usage. Missing human labels are null, never inferred
from schema validity. Root-cause fixture rubrics check required causes, acceptable
alternatives, forbidden cause claims and required references, rather than exact
prose. These rubrics do not solve general hallucination detection.

Current measured statuses are UNSEEN/UNTESTED, EARLY, EVALUATING and DEGRADED.
CALIBRATED and VALIDATED_FOR_REVIEW require separate governance/evidence; Phase 2
never awards them automatically. There is no universal AI accuracy score.

Cross-lane comparison v1 is deliberately limited to `risk.family` and root-cause
operational families. A shared primary family can agree or be compatible with
multiple interacting causes. A near-tied alternative can be partially compatible.
Unknowns and narrow margins can remain ambiguous. Clearly different supported
families create a conflict for review. Missing/rejected results are unresolved.
Every comparison is persisted with method/version and explanation. Comparison
states are interpretations, not proof of error or correctness. Human and outcome
comparisons reference their separate append-only events.

## Sampling, background work and failure

`INTELLIGENCE_EVAL_MODE=off` makes no new model calls or evaluation writes.
`fixtures` permits explicit synthetic evaluation. `sampled` selects configured
cohorts by a stable packet hash and sample rate; default rate is 10% when enabled.
Task lists are explicit server settings. There is no confidence-triggered second
model, historical-best-provider selection, model ensemble or automatic router.

The product queues at most eight snapshots. A daemon worker follows the existing
threading approach, independent of source collection. It holds the workspace lock
only for short journal operations, never during inference. Changing settings or
disabling evaluation prevents queued calls. Jobs are an in-process optimization,
not a durable distributed queue; restart may require the next collection pass to
reschedule unfinished work. Completed artifacts and comparisons are durable.
Hosted adapters with `collect_in_background=False` need explicit runner/drain
invocation; this phase does not add hosted scheduling infrastructure.

Timeouts, bounded retries, disabled providers, sampling exclusions, insufficient
state, unknown tasks, schema errors and reference errors remain distinguishable.
Unavailable inference never becomes false/no risk. Reasoning failures leave the
existing coordination projection, recommendations, prepared action payloads,
grants, receipts and kill switch unchanged. Phase 1's existing synchronous product
assessment remains intact; the new worker does not add a synchronous model call.

## UI and authorization

Intelligence Review adds Overview, Decisions, Reasoning, Comparisons, Cohorts and
Evaluation inside the existing Intelligence page. It preserves the Phase 1
inspector and distribution views, and adds structured artifacts, packet expansion,
comparison pairs, efficiency and append-only review forms. New data routes require
existing project-read permission; reviews/outcome attachments require project-write
permission. Scoped credentials must select a permitted project. Rich packets are
not added to the existing unauthenticated product-state endpoint. Source/model
text is escaped before rendering. No API keys enter these responses or assets.

See [the evaluation runbook](../evals/intelligence/README.md) and
[the measured Phase 2 report](intelligence-phase2-report.md).
