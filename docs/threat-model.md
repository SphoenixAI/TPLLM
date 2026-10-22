# Threat model

What TPLLM is built to withstand, what it is not, and where the line
sits. Written so that nobody has to infer the boundary from the code.

The short version: **TPLLM is a single-organization tool for a trusted
local operator.** Its reference deployment is one SQLite file and a
dashboard bound to loopback. It is not a hardened multi-tenant service,
and the gap is not small.

---

## 1. What is supported now

### SUPPORTED — single organization, trusted local operator

- One organization's data in one SQLite file, on a machine its operator
  controls.
- The dashboard bound to `127.0.0.1`, which is what `tpllm dashboard`
  does by default.
- `tpllm runtime` and `tpllm integrations` run by that operator, with
  credentials in their environment.
- Provider reads are read-only: every reader can issue nothing but GET,
  and a test asserts no other verb is nameable in those modules.

In that configuration the controls below are the ones that matter, and
they hold.

### NOT SUPPORTED — hosted, multi-tenant, or internet-exposed

The architecture roadmap describes a hosted multi-tenant service. None
of it is production-certified, and three specific things would be
actively unsafe:

1. **The dashboard's read surface is unauthenticated.** 25 of its 28
   routes enforce no credential, including every program, milestone,
   RAID, decision, delegation, task and document read.
   `dashboard.UNAUTHENTICATED_READ_ROUTES` enumerates them and a test
   fails if the set changes. JWT and RBAC are real and guard the three
   mutating and admin routes.
2. **The shipped container binds `0.0.0.0`.** `Dockerfile` and
   `deploy/deploy.sh` both do, and `deploy/cloudrun.yaml` deploys that.
   Running those as-is publishes the unauthenticated read surface to
   whatever can reach the container. `create_app` logs a warning at
   startup saying so, because the container bypasses the CLI that would
   otherwise have bound loopback.
3. **There is no tenant isolation of any kind.** Not partial — none.
   See §5.

---

## 2. Credential handling

**Threats.** A provider token reaching durable history, a log line, a
fixture, an export, or an exception message.

**Controls.** Each escape route is closed and tested separately in
`tests/test_provider_security.py`, because the one that leaks is always
the one nobody enumerated:

| Route | Control |
|---|---|
| Durable journal | `make_envelope` redacts credential shapes in content and title; `assert_no_secret_material` refuses secret-looking payload keys at the model level |
| Log lines | the formatter redacts at format time, by field name *and* by substring |
| Fixtures | `tpllm integrations record` refuses to write a fixture that still discloses something; a test scans every committed fixture for token prefixes |
| Exports | tested directly: no credential shape survives `export_journal` |
| Exception messages | tested: no verification failure message contains the secret or the expected digest |

**Found during this review.** The log layer was *weaker* than the
evidence layer — it only redacted a value that *was* a credential, not
one embedded in a longer string, so `reason="Authorization: ghp_…"`
from a failed read would have leaked where the same string in a pull
request body would not. The weaker rule was in the likelier place.
Also, neither layer recognised Atlassian `ATATT` tokens until Jira made
that a realistic paste.

**Residual risk.** Redaction is pattern-based. A credential in a format
nobody has a pattern for reaches the journal, and the journal is
append-only. The mitigation is that evidence content is an excerpt
rather than a full copy, and that `scripts/scan_history.py` can be run
over history to find what got through.

---

## 3. Webhook trust

Three independent properties. Conflating any two produces a hole, so
each is attacked on its own.

| Property | Threat | Control |
|---|---|---|
| **Authenticity** | forged body; wrong secret; captured delivery re-sent later | HMAC-SHA256 over the **raw** body, `hmac.compare_digest`, verified before any parsing. Slack's scheme signs the timestamp, so a stale delivery is rejected by verification itself |
| **Idempotency** | a valid delivery replayed twice becoming two facts | the key is the provider's own event id, never a clock or a payload hash. A replayed Slack delivery produces one fact |
| **Authorization** | a correctly signed delivery from a workspace nobody bound | bindings are declared; an unbound workspace or Jira project is reported, never filed under a plausible project |

**A signature says who sent it, never how many times.** A fresh event
id says this is new, never that it is genuine. Neither says the sender
is entitled to write to a project.

**Not implemented.** Nothing hosts a webhook receiver. The verification
functions are a tested seam for a future one, and they have never run
against a real provider delivery — see §7.

**Jira webhook lifecycle.** Jira Cloud dynamic webhooks expire after 30
days and need refreshing in batches of at most 100 ids. Refresh is a
write, which these read-only ports cannot make.
`JIRA_WEBHOOK_LIFECYCLE` records this so a future receiver cannot
quietly let registrations lapse; a gap must be reconciled by a JQL
search over `updated`.

---

## 4. Durable history

**Threats.** Silent mutation of history; truncation; a rebuilt replay
that disagrees with what was published.

**Controls.** Append-only hash-chained journal; corruption reported and
never repaired; replay runs the same orchestrator behind a side-effect
fence and counts what it suppressed.

**The limit, stated plainly.** A hash chain detects an *in-place* edit.
It cannot detect a history rewritten wholesale, because whoever rewrites
the file recomputes every link after the record they changed, and the
chain then verifies cleanly. This is why `tpllm runtime anchor` exists:
the operator retains the head hash somewhere the database is not, and
`verify --anchor` compares against it. Without an anchor, verification
says so in its own output rather than implying more than it checked.

**Residual risk.** An anchor stored beside the database it anchors
protects against accident, not against rewriting. A true
Haber–Stornetta anchor is published where the holder cannot retract it;
TPLLM's is operator-retained, which is the strongest honest mechanism
for a local single-file deployment.

---

## 5. Multi-tenancy

**Status: not supported, and not partially supported.**

There is no tenant concept in the storage layer. `organization_id`
exists on journal records and runtime state, but nothing enforces that
a reader may only see one organization's records: `read_from` returns
every record, and filtering is a caller's courtesy rather than a
boundary. A hosted deployment that mapped tenants onto
`organization_id` would be one missing filter away from a cross-tenant
read.

A hosted mode would need, at minimum:

- tenant-scoped storage, enforced below the query layer rather than in
  callers;
- tenant-scoped encryption context, so a leaked file is not a leak of
  everything;
- tenant-scoped provider credentials, with no shared installation
  tokens;
- authorization checked before a query or an export runs, not after;
- tenant context carried into every background job, including replay;
- cache keys that cannot collide across tenants;
- an audit trail that records which tenant a read was served to.

None of these is implemented. Saying so is the point of this section:
the roadmap's hosted mode is a design intention, and an operator should
not read it as a capability.

---

## 6. The dashboard

**Threats.** Cross-site scripting through program-controlled strings;
clickjacking; MIME sniffing; an unauthenticated read surface reachable
beyond the host.

**Controls.** Every value interpolated into the page is escaped at the
render site, and `priority`/`status` are whitelisted because they land
in a `class` attribute. `tests/test_dashboard_security.py` executes the
escaping in Node against live payloads rather than grepping for it, and
a structural test catches a newly unescaped field.

Response headers: `Content-Security-Policy`, `X-Content-Type-Options`,
`Referrer-Policy`, `X-Frame-Options`, `Permissions-Policy`.

**What the CSP does not do.** The page carries its script inline, so
`script-src` must allow `'unsafe-inline'`, and the policy therefore does
**not** mitigate XSS. The escaping is the control. What the policy does
buy is real: no external script, style, frame or object origin is
reachable, `base-uri 'none'` stops a `<base>` tag redirecting relative
URLs, and `frame-ancestors 'none'` blocks framing. `unsafe-eval` is
asserted absent.

**Named debt.** Serving the script as a separate file would allow
`script-src 'self'` and drop `'unsafe-inline'`. Not done.

**HSTS is deliberately not set.** The reference deployment is local
HTTP, where the header is ignored; served once over HTTPS it would pin
a developer's localhost to HTTPS for months.

---

## 7. What this model does not cover

- **No provider has been reached live except GitHub.** Slack and Jira
  webhook verification, pagination and payload shapes are implemented
  against published documentation and have never run against a real
  delivery. `tpllm integrations sources` reports this per provider.
- **No penetration test.** The adversarial tests are written by the
  same author as the code they attack, which bounds what they can find.
- **No fuzzing** of provider payloads beyond the hostile cases in
  `tests/test_provider_security.py`.
- **No review of the agent tool surface** for prompt injection via
  provider content. Evidence text from Slack, Jira and GitHub reaches a
  model, and a hostile message could attempt to steer it. Nothing in
  this release treats provider content as trusted *instructions*, but
  no control enforces that either.
- **No supply-chain attestation.** The SBOM is generated; nothing signs
  it, and releases are not signed.
- **Dependency vulnerabilities are checked at release time**, not
  continuously. See `docs/security-audit.md`.

---

## Reporting

`SECURITY.md` has the contact route. For anything in §1's unsupported
list, a report is welcome but the honest answer is likely to be "yes,
and that configuration is not supported" — which is why this document
enumerates them rather than waiting to be asked.
