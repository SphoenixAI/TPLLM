# TPLLM product UI

The primary flow is connected evidence → relational context → maintained state →
prepared intervention → scoped execution or human judgment → recorded result.
Manual forms remain for setup, authority, corrections and exceptional work.
The supplied dashboard image guides the charcoal surfaces, thin borders and sidebar.

## Run and connect

```bash
python -m pip install -e ".[dashboard]"
python -m tpllm dashboard --port 8765
```

Open `http://127.0.0.1:8765`. No frontend build step or CDN is required.
Choose **Explore demo workspace** for synthetic source events with no external
credentials. **Restart demo** archives the old run before creating a new one.
The demo's simulated receipts never represent real delivery.

Live setup uses the existing server-side integration configuration (`tpllm init`).
Create an administrator key with `tpllm dashboard-key --owner your-name --role admin`
and authenticate through the profile control. Keys stay in browser memory.
In **Connections**, map an exact source to a project and enable recurring reads.
Connecting a service does not grant permission to send or change external work.

The local server checks enabled collectors every 15 seconds; each source has a
configurable interval (default five minutes). Closing the dashboard tab does not
stop collection; stopping the Python server does. This is one local process, not
a hosted multi-tenant scheduler. One mapping per provider is currently supported.

| Source | Collected context | Explicit limits |
|---|---|---|
| GitHub | PR state, author, assignees, review requests, changed files and patches, review revisions, check results, assigned issues and bug-bash labels | Three pages per listing; detailed diffs/reviews/checks for five recently updated PRs. CODEOWNERS is not resolved. Author, assignee and reviewer remain distinct. |
| Slack | Channel messages and replies, source author and edit time, thread identity | Three pages and ten threads per collection. No claim of complete workspace search. |
| Google Calendar | Existing invitations, agenda, organizer, attendees and cancellation | Seven days back and fourteen days forward; 300 events maximum. No automatic calendar changes. |
| Zoom | Existing AI summaries for the mapped host | Account summary metadata is read, then exact host filtering occurs before detail retrieval or persistence. Three pages maximum. Unavailable summaries remain gaps. |

Read failures and pagination limits appear in source coverage. The last accepted
records remain visible; unavailable data is never filled with invented content.
Provider contracts reference [GitHub pull requests](https://docs.github.com/en/rest/pulls/pulls)
and [Zoom meeting APIs](https://developers.zoom.us/docs/api/meetings/). Actual
account access, provider permissions and live delivery still require separate testing.

## Working surfaces

| Surface | Automatic behavior and review |
|---|---|
| Dashboard | Source status, prepared actions, engineering changes, human decisions and KPI outlook |
| Engineering | PR diffs, author/assignee/reviewer roles, current-revision checks, linked bug findings, evidence behind delivery concerns |
| KPIs | Existing success criteria, extracted value and target claims, health, freshness, response gaps and conflicts; a statement cannot verify itself |
| Meetings & briefs | Collected invitations and AI notes, contextual meeting assessment, prepared async alternative and maintained source-backed briefs |
| Relationships | Typed author, assignment, review-request and organizer roles; explicit URL/title references with their evidence |
| Tasks | Assigned GitHub issues mirrored into the existing task board; AI-note commitments extracted automatically with source context |
| Decisions | Existing decision log, options, rationale and authorized human review |
| Intelligence | Existing journal-backed recomputation, returned summaries and explicitly labeled structural baseline outputs |
| Connections | One-time credential status, project/source scope, recurring collection controls and coverage |
| Settings | Standing delegation, pause/revoke, execution receipts and advanced exception controls |
| History | Browser audit, immutable evidence journal and existing decision-linked outcome records |

The fictional program is **AI visual search** for a cloud-hosted shopping website.
A shopper uploads a photo and asks, "Find a red backpack, not a handbag." Four
workstreams cover the website feature, vision model and inference, data packages
and evaluations, and cloud serving and observability. The release depends on
vision v0.8.2, catalog-data/labels v3.2 and request-evals v2.1 agreeing with the cloud
serving manifest; staging still references data v3.1.

The synthetic KPIs deliberately separate **Request match accuracy** (89%, approved
gate 95%, proposed 97%), **Recognition accuracy** (96%, gate 98%), and **Package
alignment** (75%, required 100%). The first metric measures object AND requested
attributes/exclusions, rather than counting recognition alone as task success.
These illustrative numbers are not measured model performance.

The meeting acceptance scenario connects an engineering invitation to a launch
status request, PR changes, a backpack-versus-handbag bug-bash finding and AI notes. Automatic redirection
requires an explicit agenda boundary as well as the linked request. A technical
meeting title alone is insufficient. The current assessment is deterministic
structural inference, not a general semantic meeting evaluator. Request detection
is vocabulary-limited: equivalent wording can remain unrecognized.

Briefs update when evidence changes. Estimates remain separate from committed
scope, dates and success targets. PR merge or green checks do not prove product
readiness. No calibrated completion date or individual performance score is produced.

KPI values currently come from communication claims, not independent instrumentation.
Unsent drafts, authored scripts, scheduled agendas and records explicitly marked as
unobserved do not supply KPI observations or target reconciliation. They remain
available as source context, with exclusion reasons in the KPI detail panel.
Synthetic records require `data.observation_basis = "simulated_observation"` to
participate in demo KPI trends; that marker never enables live observations, and
cannot override draft/script/agenda exclusions. Demo cards label eligible values
as simulated source claims.
Conflicting values remain unresolved; target claims cannot overwrite the declared
program target. One observation cannot establish a trend. Remediation ownership is
not inferred merely because a person and a metric appear in the same project.
New target conflicts appear in KPI source reconciliation, but automatic generation
of a researched clarification/decision request is not wired. The demo review
question is a seeded fixture and does not establish that general capability.

## Standing authority and execution

The implemented outbound class is **internal Slack project delivery status updates**.
An administrator delegates it for an exact project, destination, permitted source
scopes, frequency, evidence-age limit and expiry. The free-text reason documents the
grant; the fixed action purpose is enforced separately. Covered updates proceed
without another approval request. Pausing execution still permits internal collection
and reasoning. Revocation and expiry take effect before the next action.

The execution gateway checks current source versions and authority immediately
before reserving an attempt. It persists that attempt before contacting the provider.
Unchanged messages and already attempted actions are suppressed. Provider acceptance
is not confirmed reading, comprehension or task completion. Timeouts and missing
provider references are held for reconciliation and never blindly retried.

External task edits, calendar modifications, resource requests and operational
option execution still need scoped adapters. The advanced catalogue and agent
ToolRegistry block direct external mutations instead of bypassing the gateway.
This also means legacy agent outbound tools no longer execute directly. Existing
read-only tools and local work-item workflows remain available. Material scope,
launch commitments, customer promises, security/access, legal, personnel and
material spending decisions remain reserved for human authority.

The model-answer endpoint is read-only and asks for consent before sending context.
The collection loop does not make paid model calls. Source text is evidence and
cannot grant authority or directly select tools.

## Persistence

Normalized source versions enter the existing immutable journal before projection
updates. Source records retain occurrence/update time, collection time, provider,
identity, URL, project and version. Older arrivals do not roll back current state.
The source projection can be reconstructed from journal metadata after restart.

JSON artifacts retain program definitions and tasks. SQLite `browser_records`
retains source mappings, collection state, derived UI views, grants and action
receipts. Those operational records are not represented as immutable domain events.
JSON artifacts and SQLite are not one distributed transaction. Use one server
process; multi-worker coordination and artifact crash reconciliation remain open.

The local unauthenticated read surface is unchanged. See `threat-model.md`; do not
expose this reference server to a network as if it were a tenant-isolated service.

## Repeatable acceptance scenario

1. Restart the isolated demo. Four source cards identify synthetic collection.
2. Open Engineering: inspect PR #42, its diff, named roles and current-revision checks.
3. Open Meetings & briefs: inspect the automatically prepared async alternative,
   source links, collected AI notes and the already maintained brief.
4. Open KPIs: inspect reported request-match accuracy of 89%, approved gate 95% and
   the proposed 97% gate. Inspect recognition and package alignment separately. The claim does not become the committed target.
5. Inspect Settings: one simulated update has a scoped grant and receipt.
6. On Dashboard, replay the next source updates. The PR merges, the bug closes,
   the imported task becomes Done and the brief changes. The approved gate stays
   95%. The single regression case passes; the full held-out eval remains queued
   and cloud deployment remains pending. Existing KPI observations stay unchanged.
7. Refresh again: no duplicate task, evidence or communication is created. A
   changed message is held until the grant's communication interval permits it.
8. Revoke the demo grant or pause execution. Further actions stay held.
9. Inspect Connections: live credentials and enabled collection are separate
   from a saved source mapping. Disabled collectors must never read a provider.
10. Restart the demo to restore its initial state; the previous run remains archived.

Automated coverage is in `test_product_coordination.py`, `test_product_api.py`,
`test_dashboard_security.py` and the existing dashboard/runtime suites. Readers
and senders are injected: tests exercise real adapters, persistence and policy,
without claiming live provider verification or measured organizational benefit.

Baseline verification on 2026-10-29: 4,380 tests passed and one skipped. The wheel builds with
all five product modules and four frontend assets. Browser checks covered the PR
diff, collected meeting context, maintained brief, KPI source reconciliation,
source-driven task closure, communication limits, revocation and demo archiving.
Desktop (1280×800 and 1586×992) and narrow (390×844) layouts were inspected; measured
page width did not overflow at 1280 or 390 pixels. No browser console errors were
reported. Physical mobile devices and live external services were not tested.

AI visual search scenario verification on 2026-10-29: 67 focused product API,
coordination and dashboard security tests passed after replacing the Billing demo.
Browser checks confirmed all four engineering workstreams, project milestones and
dependencies, the three distinct KPI gates, and the PR's model/data/eval changes.
Replaying the source update merged the PR and passed the isolated regression while
the full held-out evaluation stayed queued and the KPI observations stayed unchanged.
The demo was then archived and restarted for a fresh user test. Projects fit both
1586×992 and 390×844 viewports without page-width overflow; no browser console
errors were reported. These checks use synthetic evidence, not a measured vision
model or live provider accounts.

The independent provider fixtures then exposed a paragraph-extraction bug:
89%, 96% and 75% were all rendered as 89% when reported on one line. Extraction
now separates sentences and named-metric clauses, preserves decimal values,
and evaluates all known project KPIs together so follow-up targets cannot leak
between metrics. Target-only statements do not become current observations.
The fix passed 113 KPI/product/security tests and the independent full fixture
replay (24 passed, zero failed, two previously documented gaps). An isolated
browser run confirmed 89%/96%/75% with committed targets 95%/98%/100%, without
console errors. The fixture measurements remain synthetic.

Connected-capture provenance follow-up: 132 relevant tests passed. Reprocessing
the 24 captured test records retained every source while producing zero observed
KPI values and no target claims from the drafts/scripts; committed gates remained
95%/98%/100%. Browser verification showed Unknown values and linked exclusion
reasons without console errors. The separate provider-observation simulation
still passed 25 checks, with zero failures and its two known gaps. This change
does not add Gmail draft or Zoom Hub collectors or resolve abbreviated KPI names
in genuinely observed conversations.

# Phase 2 Intelligence Review

The existing Intelligence page now contains Overview, Decisions, Reasoning,
Comparisons, Cohorts and Evaluation. Phase 1 distributions remain inspectable.
Reasoning cards expand into typed synthesis, bounded evidence and temporal lineage.
Comparisons distinguish compatible interpretations, ambiguity and conflict.

Select **Inspect and review** to append a lane-appropriate review. Corrections
append another event referencing the latest review; original output is unchanged.
Live review requires the existing project-write permission. Demo reviews remain
fixture records and do not enter real human-acceptance statistics.

The evaluation view separates probabilistic calibration from schema/grounding and
human review metrics. Missing human labels, usage and cost remain Unknown. Mock
latencies and usage are excluded from provider performance. Evaluation is disabled
unless explicitly configured on the server; viewing this page does not invoke a
model. See [the architecture](intelligence-phase2.md) and
[local synthetic preview](../evals/intelligence/README.md).
