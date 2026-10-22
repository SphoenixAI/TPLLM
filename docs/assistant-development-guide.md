# docs/assistant-development-guide.md

The engineering documentation for this repository lives in
[`docs/engineering-handbook.md`](docs/engineering-handbook.md):
the layering rules, the architectural invariants and which test file
defends each one, the development conventions, and the current wiring
state of the reasoning stack.

Read it before moving an import between packages or relaxing a
guarantee — most of this project's opinions are enforced by tests rather
than left to review, and the handbook says which test is which.

Also worth reading first:

- [`README.md`](README.md) — what LLTPM is, and a five-minute offline
  quickstart that runs a real organizational scenario.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to set up, what to run
  before opening a pull request.
- [`docs/research-alignment.md`](docs/research-alignment.md) — where the
  design overlaps published work, and §11's open limitations. Read it
  before relaxing an invariant: several were arrived at independently
  and then found to match prior art.
- [`docs/threat-model.md`](docs/threat-model.md) — the deployment
  boundary, including the configurations that are explicitly
  unsupported.
