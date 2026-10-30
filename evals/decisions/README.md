# Bounded-decision evaluation

See [the integration guide](../../docs/decision-intelligence.md) for the repository
map, architecture, trust boundary, schema contract, calibration and configuration.

- `run.py`: five realistic authored cases, ten questions each; provider-neutral
  state/definition/outcome comparison with the existing KPI health baseline.
- `results.mock.json`: synthetic answer/label metrics, including errors and ambiguity.
- `demo.py`: actual product ingestion -> observations -> judgment -> validator ->
  review recommendation -> authority boundary -> hash-chain export.
- `demo.mock.json`: complete synthetic example with state, definitions, distributions,
  validation and journal hashes. Timestamps/IDs describe this example run.

These artifacts verify plumbing and evaluator behavior. They do not establish
Jev quality, independently reviewed human labels or benefit against a competent
production baseline. Fixture labels never qualify a production calibration segment.
