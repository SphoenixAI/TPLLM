# Synthetic AI visual-search environment

This local environment matches TPLLM's AI visual-search project: a shopper uploads
a photo and asks, **“Find a red backpack, not a handbag.”** The website's vision
model, catalog data, labels, held-out evaluation package, and cloud serving
manifest must agree. No Slack company, provider account, API key, or model is needed.

All people, conversations, meetings, source URLs, and model outputs here are
fictional. The 100 held-out cases contain **symbolic image descriptions**, not
image files. Their authored outputs produce reproducible simulated metrics;
they do not measure a computer-vision model's actual performance.

## Run

From the repository with its dashboard dependencies installed:

```sh
.venv/bin/python evals/visual_search/replay.py --output /tmp/tpllm-visual-search-run
```

The runner uses the production GitHub, Slack, Calendar, and Zoom collectors with
local response readers. It feeds their output into the production evidence
journal, derivation, task import, and authority gateway. Network access is denied
during replay. Provider credentials are never loaded. Choose a new output directory
for every run: the runner preserves existing evidence instead of overwriting it.

Inspect an interactive copy:

```sh
.venv/bin/python evals/visual_search/replay.py \
  --output /tmp/tpllm-visual-search-browser --stage initial --serve
```

Open <http://127.0.0.1:8766>, choose **Explore demo workspace**, then inspect
Engineering, KPIs, Meetings & briefs, and Tasks. **Replay next source updates**
advances the local provider fixtures through the three stages. A third click is
idempotent. This runner's server adapts only that button; the ordinary product
server is unchanged. Reset by starting a new run, not by restarting the standard
demo. The server binds only to localhost and background collection is disabled.

## Stages and ground truth

1. **initial** — Request match 89/100, recognition 96/100, package alignment 3/4.
   Staging has catalog-data 3.1 while the candidate uses 3.2. PR #42 and bug #73
   are open. Current checks fail; an old-revision green check is a distractor.
2. **regression_fixed** — PR #42 merges and bug #73 closes. The one backpack
   regression passes. Full evaluation task #74 remains open, full held-out checks
   and cloud canary remain queued, and the last complete metrics stay 89/96/75.
3. **full_eval_complete** — Authored synthetic held-out outputs yield request
   match 96/100 and recognition 99/100; all four bundle references align. Full
   evaluation and canary checks pass and task #74 closes. Release approval is
   still absent. A 97% proposed gate never overwrites the committed 95% target.

Slack includes a linked status request, an existing authority answer, and thread
replies. Calendar includes an explicit agenda boundary. Zoom includes owned notes
and an out-of-scope host whose details must never be read. Notes contain
commitments for Alex, Riley, Jamie, and Sam. The source time is rebased as one
timeline so the review remains upcoming when the fixtures are replayed later.

## Files and interpretation

- `scenario.json`: project contract, people, release bundle, 100 symbolic held-out
  cases, authored model outputs, expected metrics, and separate regression case.
- `provider_payloads.json`: raw provider-shaped responses for all three stages.
- `build_fixtures.py`: deterministic fixture generator; no network access.
- `replay.py`: adapter replay, explicit assertions, isolated persistence, optional UI.

Each run writes `report.json`, `adapter_calls.json`, and per-stage snapshots. A
normal run fails for broken data/adapter invariants. `--strict` also returns a
failure for unmet product expectations, currently reported as `GAP`:

- Equivalent launch questions can miss the narrow meeting-routing vocabulary.
- Newly extracted KPI conflicts do not automatically create a precise human-review
  request; the normal demo's question is seeded. This fixture does not seed it.

Regression coverage also checks that a multi-metric paragraph retains each value
and attaches the proposed 97% target only to Request match accuracy. These fixtures
exposed an extractor bug that showed 89 for all three metrics; the corrected
extractor passes the independent 89/96/75 expectation.

Simulated receipts prove gateway behavior only. Communication intervals can hold
later drafts when stages are replayed rapidly. Source claims remain claims;
historical disagreements are not silently resolved into independent measurements.

## Connected workflow scenario

`engagement_workflow.json` extends the same project into four scheduled meetings
and sixteen scripted email engagements: contract, clarification, investigation,
targeted questions, engineering replies, full evaluation, bounded canary decision,
outcomes, and unresolved follow-up. Its run marker is `VSIM-20261030`.

For the authorized connected run, the records were created in Google Calendar,
Zoom, and Gmail: four 30-minute meetings on October 30, 2026 at 09:00, 10:00,
11:00 and 12:00 America/Los_Angeles, matching private/free Calendar events with
no guests or reminders, four authored Zoom Hub scripts with a navigation index,
and sixteen unsent Gmail drafts with no recipients. The scripts are not recordings or completed meetings;
the drafts are not delivered conversations. Fictional authority never authorizes
an actual release. Provider IDs, account details and meeting links are retained
only in the private local run manifest, outside this repository.

`connected_replay.py` evaluates saved **live connector readbacks**. It verifies
the records and their content, then uses an explicit capture bridge to feed the
production evidence journal, derivation and authority checks in a fresh workspace:

```sh
.venv/bin/python evals/visual_search/connected_replay.py \
  --capture /private/path/connected-workflow-readbacks.json \
  --manifest /private/path/connected-workflow-manifest.json \
  --output /tmp/tpllm-connected-workflow-run
```

This command does not create or send anything and forbids network access during
evaluation. It does not claim unattended collection: TPLLM lacks Gmail-draft and
Zoom-Hub-document collectors, and the Calendar connector readback omits the
provider modification timestamp expected by the native Calendar adapter. The
bridge records capture time explicitly instead of fabricating that timestamp.
The resulting `report.json` separates verified behavior, product gaps, and
authored expectations. `snapshot.json` contains the actual engine output.
