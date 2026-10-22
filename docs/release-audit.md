# Open-source release audit

What was checked before the first public release, what was found, and
what a maintainer should re-run before each release after it.

Everything here that *can* be asserted is asserted in
`tests/test_release_readiness.py`, so the audit does not decay into a
document nobody reruns. This file records the parts that need judgement
and the results of the checks that need network access.

Audited at version `0.41.0`.

---

## 1. Nothing private ships

**Checked:** the built wheel's contents, the tracked file list, and
`.env.example`.

| Check | Result |
|---|---|
| Tests, fixtures, caches, `.pyc` in the wheel | none |
| Database or `.env` files tracked in git | none |
| Credential-shaped strings outside the test suite | none |
| Owner's email address or GitHub handle in any tracked file | none |
| `py.typed` ships | yes |
| `integrations/contract_data/github.json` ships | yes |
| `LICENSE` in the wheel's `dist-info` | yes |

The wheel is 289 entries and contains only `src/lltpm`. The only
`test`-matching path is `structural/stress_test.py`, which is a source
module about structural stress tests, not a test file.

**Credential scanning excludes `tests/`, deliberately.** A test that
asserts credential redaction must contain the thing being redacted, and
no scanner can distinguish a synthetic `ghp_ABCDEF…` in a redaction test
from a real token. The check is therefore strict everywhere it can be
meaningful — shipped code, documentation, CI, deployment files — rather
than disabled everywhere to accommodate the one place it cannot be.

**Recorded fixtures name nobody.** The GitHub fixtures were recorded
from a real repository and scrubbed before being committed: handles,
account ids and repository names pseudonymized, avatars and emails
dropped, opaque ids rewritten deterministically.
`lltpm integrations record` refuses to write a fixture that still
discloses something, and `tests/test_integration_contracts.py` asserts
every login in every committed fixture matches the alias pattern.

---

## 2. Licensing

**Checked:** the declared licence, the `LICENSE` file, and every runtime
dependency's licence.

| Dependency | Version seen | Licence |
|---|---|---|
| anthropic | 1.15.0 | MIT |
| pydantic | 2.14.1 | MIT |
| rich | 15.0.0 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| python-dateutil | 2.9.0 | Dual (Apache-2.0 / BSD-3-Clause) |
| PyJWT | 2.15.1 | MIT |
| networkx | 3.6.1 | BSD-3-Clause |

All permissive and all compatible with distributing this project under
MIT. No copyleft dependency in the required set.

**Optional extras were not audited for licence**, because they are not
distributed with the package and the user installs them knowingly. A
maintainer adding a *required* dependency should check its licence here;
the test suite cannot, since it would need the dependency installed to
read its metadata.

---

## 3. Every optional extra is installable

**Checked** against PyPI at audit time. An extra naming a package that
does not exist turns `pip install lltpm[voice]` into a resolution error
for a user who did nothing wrong.

`chatterbox-tts`, `kokoro`, `piper-tts`, `soundfile`, `weasyprint`,
`sendgrid`, `PyGithub`, `jira`, `slack-sdk`, `slack-bolt`,
`google-auth`, `google-api-python-client`, `fastapi`, `uvicorn`,
`openai` — all resolve, all at or above the declared floor.

This check is **not** in the test suite: a suite that fails when PyPI is
unreachable is a suite people learn to ignore. Re-run it by hand before
a release:

```bash
for pkg in chatterbox-tts kokoro piper-tts soundfile weasyprint \
           sendgrid pygithub jira slack-sdk slack-bolt; do
    python -m pip index versions "$pkg" | head -1
done
```

---

## 4. Public claims

**Found and fixed:** the README and the engineering handbook both advertised "2850+
tests" against a suite of 3,462. A stated figure the suite has outgrown
is harmless; one it has never reached is not. The test suite now asserts
no documented test count exceeds the real one.

**Found and fixed:** three install extras — `email`, `openai`, `pdf` —
existed in `pyproject.toml` and were mentioned nowhere a user would
look. An extra nobody documents is an extra nobody installs. The README
now lists every one, and a test asserts that stays true.

**Checked:** every relative link in the README and in
`docs/research-alignment.md` resolves.

**Checked:** no `TODO`, `FIXME`, `HACK` or `XXX` debt markers in
`src/`. The matches that appear are a task-status enum value named
`TODO`.

---

## 5. Packaging metadata

**Found and fixed:** the package had no author, no keywords, no
classifiers and no project URLs. A PyPI page with no repository link and
no Python classifiers is a worse first impression than no page.

Now declared: author, keywords, `Development Status :: 4 - Beta`,
intended audiences, a classifier for each of Python 3.11, 3.12 and 3.13,
topics, `Typing :: Typed`, and Homepage / Repository / Issues /
Changelog URLs. Tests assert that the Python classifiers match
`requires-python` and that the typing marker is advertised, since the
package ships `py.typed`.

**Version consistency.** Three places refer to the version and two are
derived from the first: `pyproject.toml` is authoritative,
`lltpm.__version__` reads installed metadata, and
`persistence.versioning.DECLARED_LLTPM_VERSION` is the fallback stamped
into a reproducibility manifest when the package is not installed. The
fallback had drifted to `0.31.0`; a mismatch means durable history
records a version that was never released, so a test now asserts all
three agree.

---

## 6. Deployment artefacts

**Checked:** `Dockerfile`, `docker-compose.yml`, `deploy/cloudrun.yaml`
and `deploy/deploy.sh` for embedded credentials, internal hostnames and
private IP addresses. None. `cloudrun.yaml` references secrets through
`secretKeyRef`, which is a reference rather than a value.

`0.0.0.0` appears as a bind address in the container entrypoint, which
is correct inside a container and is not an internal host.

---

## 7. What this audit does not cover

Stated so it is not mistaken for having been done.

- **Git history has not been scanned** for credentials committed and
  later removed. The working tree is clean; history is a separate pass,
  and it is the next step in the release sequence.
- **No dependency vulnerability scan.** `pip-audit` or equivalent should
  run against a resolved environment, which CI does not currently do.
- **No SBOM is published.**
- **The dashboard's HTML has not been reviewed for XSS** against
  program-supplied strings. It renders program names and task titles
  into a template; the inputs are operator-supplied rather than public,
  but that is a trust assumption rather than a control.
- **No threat model for a multi-tenant deployment.** The reference
  deployment is single-tenant and local; `SECURITY.md` says so.
- **Optional extras' licences**, as above.

---

## Re-running this before a release

```bash
python -m pytest tests/test_release_readiness.py -q   # the asserted part
python -m pytest tests/ -q                            # everything
python -m build --wheel                               # inspect by hand
python -m pyflakes src/lltpm/                          # known debt; see
                                                       # research-alignment §11
```

Then the two network checks in §3 and §7, by hand, and a read of this
file's §7 to confirm nothing in it has quietly become false.
