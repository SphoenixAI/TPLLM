# Phase 2 evaluation

Run from the existing TPLLM checkout. No command below deploys, commits, changes
authority, or sends connector data to a model. The scenario corpus is authored,
sanitized and synthetic. `--provider mock` replays authored outputs, including
intentional errors; it is an evaluator test, not a model benchmark.

```sh
.venv/bin/python evals/intelligence/run.py --lane decision --output /tmp/decisions.json
.venv/bin/python evals/intelligence/run.py --lane reasoning --output /tmp/reasoning.json
.venv/bin/python evals/intelligence/run.py --lane dual --output /tmp/dual.json
.venv/bin/python evals/intelligence/run.py --cohort software.deployments --task reasoning.root_cause_analysis.v1 --limit 3 --output /tmp/deployments.json
.venv/bin/python evals/intelligence/run.py --fixture release-three-causes --output /tmp/multicausal.json
```

Full output includes definition snapshots, packets, typed results, reviews,
comparisons, the hash-chained journal export, per-lane metrics and source hashes.
`--compact` omits the full journal and deduplicates schemas for a smaller review
artifact. Compact examples cannot restore the journal. Results retain mock/live
provenance. Costs and usage are null when unavailable. Mock timings are never
reported as provider performance.

For controlled real-provider smoke runs, configure credentials in the server
environment, choose an explicit supported `TPLLM_MODEL` and `TPLLM_PROVIDER`,
and enable `TYPESAFE_ENABLED=true JEV_MODE=shadow` when testing Jev. Then run:

```sh
.venv/bin/python evals/intelligence/run.py --provider live --fixture schema-break --limit 1 --output /tmp/phase2-live.json
.venv/bin/python evals/intelligence/run.py --provider typesafe --lane decision --limit 1 --output /tmp/jev-smoke.json
.venv/bin/python evals/intelligence/run.py --provider anthropic --lane reasoning --task reasoning.root_cause_analysis.v1 --limit 1 --output /tmp/anthropic-smoke.json
.venv/bin/python evals/intelligence/run.py --provider openai --lane reasoning --task reasoning.root_cause_analysis.v1 --limit 1 --output /tmp/openai-smoke.json
```

Explicit Anthropic/OpenAI selection uses the existing configured model; ensure it
belongs to the selected provider. Unsupported native Structured Outputs fail
closed. Missing credentials produce coverage gaps, not fake results. No vendor
price table is assumed. The fixture runner does not load production source records.

The corpus contains 24 scenarios, three in each of eight cohorts. It includes
hard negatives, contradictory evidence, ambiguity, insufficient evidence,
recovery and multiple interacting causes. Three decision tasks and five reasoning
tasks run in the default dual mode. Other Phase 1 definitions remain covered by
their original fixtures and tests. Root-cause references use semantic cause tags,
evidence linkage, acceptable alternatives and forbidden claims. Other synthesis
tasks currently have schema/grounding checks; their semantic quality needs richer
human rubrics.

Deliberate controls include a confidently wrong risk judgment, incorrect family,
near-tied family distribution, omitted causes, invented reference, missing required
field and provider timeout. The report must expose these errors rather than score
the fixture author and mock response as independent agreement.

For a local browser preview using a separate temporary database:

```sh
.venv/bin/python evals/intelligence/demo.py --data-dir /tmp/tpllm-phase2-demo --port 8769
```

Open `http://127.0.0.1:8769/#intelligence`, choose **Explore demo workspace**, and
inspect the six seeded paired cases. Review writes remain local fixture events.
The demo deliberately loads no production configuration or API credentials.

The checked-in compact mock example and availability report document this run.
Neither establishes real Jev or reasoning-model performance. Phase 3 routing
requires real workload measurements and independent outcome/human evaluation.
