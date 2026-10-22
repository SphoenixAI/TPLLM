# Security audit

The release audit listed what it had *not* covered. This closes
those gaps, re-run against the tree that includes the Slack and Jira
integrations. `docs/threat-model.md` states the boundary; this records
what was tested, what it found, and what is still uncovered.

Audited at `0.41.0`, with the Slack and Jira integrations in the tree.

Nothing here uses the word "secure" as a state. Each row says what was
checked and what the result means.

---

## Summary

| Area | Result |
|---|---|
| Git history secret scan | **passed** — 83 commits, 1010 blobs, no credential outside the test suite; no rewrite needed |
| Dependency vulnerabilities | **passed** — base install and all seven extras clean |
| SBOM | **generated** — CycloneDX 1.6, 28 components, 27 with licence metadata |
| Wheel contents | **passed** |
| Sdist contents | **passed after a fix** — excluded development-only files |
| Dashboard XSS | **found and fixed** — a real DOM XSS in every rendered field |
| Security headers | **added**, with the CSP's limits stated rather than implied |
| Authentication | **two findings, both fixed** — see below |
| Provider credential boundaries | **two findings, both fixed** |
| Webhook threat model | **tested** — authenticity, idempotency and authorization separately |
| Multi-tenancy | **not supported**, now documented as such |
| Anchor limitations | **documented** |

---

## 1. Findings

### 1.1 A refresh token was accepted as an access token — fixed

`dashboard.py` validated bearer credentials with `validate_jwt` and
never checked `token_type`. A refresh token is a valid JWT with a
seven-day lifetime, so it satisfied every endpoint that a thirty-minute
access token was meant to — a leaked refresh token granted a week of
API access instead of one rotation-revoked exchange.

`validate_jwt` now takes `expect_type`, **defaulting to `"access"`**.
The restrictive answer is the default so a future caller is safe without
knowing this history; a second method named `validate_access_token`
would not have achieved that, because nothing would stop the next
caller reaching for the old name. The refresh path passes
`expect_type="refresh"` explicitly.

### 1.2 25 of 28 dashboard routes enforce no authentication — documented

The JWT and RBAC machinery is real, well built and guards three routes:
`/api/auth/me`, `/api/security/audit`, `/api/security/keys`. Everything
else — every program, milestone, RAID, decision, delegation, task,
sprint and document read — requires no credential, while the project
advertised "FastAPI with JWT authentication". True of the machinery,
false of the surface.

This was **not** closed by adding `require_auth` everywhere, because the
shipped dashboard UI fetches those endpoints with no token: requiring
auth would break the page without a login flow, which is a feature
rather than a security fix. Instead:

- the open set is enumerated in
  `dashboard.UNAUTHENTICATED_READ_ROUTES` and a test fails if a route
  joins or leaves it, so the boundary is a decision somebody makes;
- a test asserts every *mutating* route is guarded, excluding the two
  authentication endpoints that necessarily cannot be;
- `create_app` warns at startup, because `Dockerfile` and
  `deploy/deploy.sh` call uvicorn directly with `--host 0.0.0.0` and
  bypass the CLI that defaults to loopback;
- `docs/threat-model.md` §1 lists internet-exposed deployment as
  unsupported.

**The dangerous configuration is the shipped container**, not the local
CLI. That is stated in the threat model rather than left to be
discovered.

*A first attempt at measuring this reported "6 of 28 guarded" by
scanning a fixed number of characters after each handler, which
truncated the longer bodies. The AST count is 3. A heuristic that
undercounts an open surface is worse than no count, and the test now
parses.*

### 1.3 A real DOM XSS in the dashboard — fixed

The page renders fetched JSON client-side through `innerHTML` and
template literals. Task titles, assignees, sprint names, workload keys
and RAID counts all interpolated unescaped, and `priority`/`status`
went into a `class` attribute. A task titled
`<img src=x onerror=alert(1)>` executed.

Escaped at every render site; `priority`/`status` whitelisted against a
closed enum rather than escaped, because an attribute context is one
quoting mistake from breakout. The tests **execute** the escaping in
Node against live payloads — a helper that returned its input unchanged
would satisfy any source-level check for `esc(`. A structural test
catches a newly unescaped field; unescaping one fails the suite.

### 1.4 The log layer was weaker than the evidence layer — fixed

`observability/redaction.py` only redacted a value that *was* a
credential, not one embedded in a longer string. So
`reason="Authorization: ghp_…"` — exactly what a failed provider read
produces — passed through a log line intact, while the same string in a
pull request body was redacted. The weaker rule was in the likelier
place. Log redaction is now substring-based, replacing the credential
and keeping the surrounding message.

### 1.5 Atlassian tokens were unrecognised — fixed

Neither layer matched `ATATT…`. Adding Jira made a token pasted into a
ticket description a realistic route into append-only history. Pattern
added to both layers, with a test that every provider's token shape is
redacted in both.

### 1.6 Development files shipped in the sdist — fixed

The release audit inspected the wheel only. The sdist is built from a different
file set and carried `.secrets-triage.json`, CI config and the
deployment artefacts. Excluded. `.env.example` still ships, on purpose:
it is a template, and the test now looks for a real `.env` rather than
matching the prefix.

---

## 2. Git history

```bash
python scripts/scan_history.py          # every blob, every ref
python scripts/scan_history.py --tree   # working tree only
```

**Result: no credential outside the test suite, so no history rewrite
is needed.** 83 commits, 1010 reachable blobs.

Three design points make the result worth believing:

- **It self-tests.** The first version reported a confident zero while
  running only its own regexes, because `detect-secrets`' `scan_line`
  returns nothing outside a `default_settings()` context. It now plants
  known synthetic credentials and refuses to report on a history its
  detectors cannot catch them in.
- **It tiers findings.** With the plugins loaded, entropy fired on 565
  lines including `# LLTPM` and `from __future__ import annotations`.
  Entropy at line granularity cannot distinguish a long identifier from
  a key, so those are reported for review and never block; only
  provider-shaped and keyword findings can.
- **It records triage.** A history scan cannot be annotated in place —
  an inline pragma fixes the working tree while the historical blob
  keeps the old line. `.secrets-triage.json` holds the decisions, each
  with a reason, and an entry whose reason is too thin suppresses
  nothing.

---

## 3. Dependencies and SBOM

```bash
python scripts/audit_dependencies.py        # per-extra closures
python scripts/generate_sbom.py -o sbom.json
```

**Result: base and all seven extras clean.**

`pip-audit` with no arguments reported 89 vulnerabilities across 8
packages in this container, and **not one was an LLTPM dependency** —
they were the container's own `pip`, `setuptools` and `wheel` plus
transitive packages of extras installed for other reasons. Publishing
that as "LLTPM's vulnerabilities" would be false in both directions:
overstating what the project ships and saying nothing about extras
nobody installed. The script resolves each extra's closure separately,
without installing it, so a finding is attributable to the extra that
pulls it. A finding in the base install blocks; a finding in an extra is
reported with the extra named.

The SBOM is generated, never maintained, and names the release it
belongs to. Licence metadata took three attempts: a requirements-based
SBOM has none, the environment mode left it empty because cyclonedx-py
7.5 does not read `License-Expression`, so licences are enriched from
the installed distributions directly.

Both scripts initially included `lltpm` itself in its own closure, which
is not on PyPI. The project is the subject of an audit, not one of its
own dependencies.

---

## 4. What is still not covered

Carried forward from the release audit, plus what this review added.

- **No provider reached live except GitHub.** Slack and Jira webhook
  verification has never run against a real delivery.
- **No penetration test.** The adversarial tests are written by the
  author of the code they attack, which bounds what they can find.
- **No prompt-injection review of the agent surface.** Provider
  evidence text reaches a model; nothing treats it as trusted
  instructions, and nothing enforces that either.
- **No release signing or SBOM attestation.**
- **Dependency scanning is release-time, not continuous.**
- **No fuzzing** beyond the hostile cases in
  `tests/test_provider_security.py`.
- **Multi-tenancy is absent**, not partial. `docs/threat-model.md` §5.
- **The CSP cannot mitigate XSS** while the dashboard script is inline.

---

## Re-running this

```bash
python -m pytest tests/test_auth_adversarial.py \
                tests/test_provider_security.py \
                tests/test_dashboard_security.py \
                tests/test_release_readiness.py -q
python scripts/scan_history.py
python scripts/audit_dependencies.py
python scripts/generate_sbom.py -o sbom.json
```

The first command runs in CI on every push. The three scripts need
network access and run in the release job, so a release cannot be cut
without them.
