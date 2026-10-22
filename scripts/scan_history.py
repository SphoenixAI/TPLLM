#!/usr/bin/env python3
"""Scan every blob reachable from any ref for credential material.

Tracked-file scanning is not enough before publishing. A credential
committed in March and deleted in April is still in the history that
gets pushed, and `git grep` on the working tree will never see it. This
walks every object `git rev-list --objects --all` can reach, so the
coverage is "everything publication would carry" rather than "everything
currently checked out".

Two detectors, deliberately:

- a pattern set for shapes that are unambiguous (`ghp_…`, `xoxb-…`,
  `AKIA…`, PEM headers, JWTs);
- Yelp's `detect-secrets` plugin set, when installed, which adds entropy
  and keyword heuristics the pattern set cannot express.

Relying on one regex is how a scan produces a reassuring zero. Relying
on entropy alone is how it produces a hundred findings nobody reads.

Findings are classified rather than just counted. A synthetic token in a
redaction test is a finding the scan must *show* — because the only
thing separating it from a real one is where it lives — but it is not a
publication blocker, so it is reported in its own bucket instead of
being silently filtered out.

Usage:

    python scripts/scan_history.py            # whole history, all refs
    python scripts/scan_history.py --tree     # working tree only, faster
    python scripts/scan_history.py --json     # machine-readable

Exit status is 1 when a blocker is found, 0 otherwise, so this can gate
a release job.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field

#: Shapes that are a credential or nothing. Kept narrow on purpose: a
#: broad pattern produces noise, and noise is what makes people stop
#: reading scanner output.
PATTERNS: dict[str, re.Pattern[str]] = {
    "github-pat": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),
    "github-fine-grained": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}"),
    "slack-webhook": re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9/]{20,}"),
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "anthropic-key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{30,}"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "atlassian-token": re.compile(r"\bATATT[A-Za-z0-9_\-=]{40,}"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "basic-auth-url": re.compile(r"https?://[^/\s:@]+:[^/\s@]{8,}@"),
}

#: detect-secrets plugins that establish a *shape* or a *context* — a
#: provider's documented token format, or an assignment to a
#: secret-named variable. These can block a release.
CONCLUSIVE_PLUGINS = frozenset({
    "AWS Access Key",
    "Azure Storage Account access key",
    "Basic Auth Credentials",
    "Cloudant Credentials",
    "Discord Bot Token",
    "GitHub Token",
    "GitLab Token",
    "IBM COS HMAC Credentials",
    "IBM Cloud IAM Key", "JSON Web Token", "Mailchimp Access Key",
    "NPM tokens", "Private Key", "SendGrid API Key", "Slack Token",
    "Softlayer Credentials", "Square OAuth Secret", "Stripe Access Key",
    "Telegram Bot Token", "Twilio API Key", "Secret Keyword",
    "OpenAI Token", "PyPI Token",
})

#: Plugins that measure only randomness. At line granularity in this
#: codebase they fire on `# TPLLM`, on `TPM agent`, and on
#: `from __future__ import annotations` — a docstring has enough
#: character variety to look like base64. They are counted and reported
#: for review, never treated as a blocker, because entropy alone cannot
#: distinguish a long identifier from a key. Blocking on them would
#: produce 400 findings nobody reads, which is the state in which a real
#: one gets missed.
ENTROPY_PLUGINS = frozenset({
    "Base64 High Entropy String", "Hex High Entropy String",
})

#: detect-secrets' own inline suppression marker. Honoured so that a
#: triaged false positive is annotated on the line a reviewer reads,
#: instead of adding a whole file to the expected list and hiding every
#: future finding in it.
ALLOWLIST_PRAGMA = re.compile(r"pragma:\s*allowlist\s+secret")

#: Audited false positives, keyed by (path, detector). A history scan
#: cannot be annotated in place — an inline pragma fixes the working
#: tree while the historical blob keeps the un-annotated line — so a
#: triaged decision is recorded here instead. Every entry must carry a
#: reason; one without is treated as unreviewed and still blocks.
TRIAGE_FILE = pathlib.Path(__file__).resolve().parent.parent / (
    ".secrets-triage.json"
)


def load_triage() -> dict[tuple[str, str], str]:
    if not TRIAGE_FILE.exists():
        return {}
    data = json.loads(TRIAGE_FILE.read_text())
    triaged: dict[tuple[str, str], str] = {}
    for entry in data.get("entries", []):
        reason = str(entry.get("reason", "")).strip()
        if len(reason) < 40:
            # A one-word note turns an audit file into a suppression
            # dump, so a thin reason does not suppress anything.
            continue
        triaged[(entry["path"], entry["detector"])] = reason
    return triaged


#: Paths whose findings are expected and are not publication blockers.
#: The test suite has to contain the thing it asserts is redacted, and
#: the redaction vocabularies have to name the prefixes they match.
EXPECTED_PREFIXES = (
    "tests/",
    "scripts/scan_history.py",
    ".env.example",
    "src/tpllm/integrations/envelopes.py",
    "src/tpllm/observability/redaction.py",
    "src/tpllm/persistence/models.py",
    "src/tpllm/integrations/contracts.py",
)

#: Binary and vendored content is not where a leaked credential hides,
#: and scanning it produces false positives from compressed bytes.
SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2",
    ".zip", ".gz", ".whl", ".pyc", ".so", ".dylib", ".wav", ".mp3",
)


@dataclass
class Finding:
    detector: str
    path: str
    blob: str
    commits: list[str] = field(default_factory=list)

    @property
    def expected(self) -> bool:
        return self.path.startswith(EXPECTED_PREFIXES)

    @property
    def plugin(self) -> str:
        return self.detector.partition(":")[2] or self.detector

    triage_reason: str = ""

    @property
    def tier(self) -> str:
        """blocker | review | expected | triaged."""
        if self.triage_reason:
            return "triaged"
        if self.expected:
            return "expected"
        if self.plugin in ENTROPY_PLUGINS:
            return "review"
        return "blocker"

    def as_dict(self) -> dict:
        return {
            "detector": self.detector,
            "path": self.path,
            "blob": self.blob,
            "expected": self.expected,
            "tier": self.tier,
            "triage_reason": self.triage_reason,
            "commits": self.commits[:5],
        }


def _run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def reachable_blobs() -> list[tuple[str, str]]:
    """Every (blob sha, path) reachable from any ref, deduplicated."""
    seen: dict[tuple[str, str], None] = {}
    for line in _run("git", "rev-list", "--objects", "--all").splitlines():
        sha, _, path = line.partition(" ")
        if not path or path.endswith("/"):
            continue
        if path.endswith(SKIP_SUFFIXES):
            continue
        seen.setdefault((sha, path), None)
    return list(seen)


def tree_files() -> list[tuple[str, str]]:
    return [
        ("", path) for path in _run("git", "ls-files").splitlines()
        if path and not path.endswith(SKIP_SUFFIXES)
    ]


def read_blob(sha: str, path: str) -> str:
    if sha:
        result = subprocess.run(
            ["git", "cat-file", "blob", sha],
            capture_output=True,
        )
        return result.stdout.decode("utf-8", errors="ignore")
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return ""


def scan_patterns(targets: list[tuple[str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    for sha, path in targets:
        text = read_blob(sha, path)
        if not text:
            continue
        for name, pattern in PATTERNS.items():
            for line in text.splitlines():
                if pattern.search(line) and not ALLOWLIST_PRAGMA.search(line):
                    findings.append(
                        Finding(detector=name, path=path, blob=sha)
                    )
                    break
    return findings


def scan_detect_secrets(targets: list[tuple[str, str]]) -> list[Finding]:
    """Yelp's plugin set, when installed. Adds entropy and keyword rules.

    Run over blob contents rather than the filesystem so history is
    covered too. Absence of the package degrades the scan rather than
    failing it, and the report says which detectors actually ran.
    """
    try:
        from detect_secrets.core.scan import scan_line
        from detect_secrets.settings import default_settings
    except ImportError:
        return []

    findings: list[Finding] = []
    # The settings context is what loads the plugin set. Without it
    # `scan_line` returns nothing for every input, and the scan reports a
    # confident zero while running only the pattern set above. That is
    # the exact failure this scanner's own docstring warns about, and it
    # happened here before `_assert_detectors_fire` was added.
    with default_settings():
        for sha, path in targets:
            text = read_blob(sha, path)
            if not text:
                continue
            for line in text.splitlines():
                if len(line) > 4096 or ALLOWLIST_PRAGMA.search(line):
                    continue
                for secret in scan_line(line):
                    findings.append(Finding(
                        detector=f"detect-secrets:{secret.type}",
                        path=path, blob=sha,
                    ))
                    break
    return findings


#: Synthetic credentials used to prove the detectors work before any
#: zero result is believed. Documented values from provider docs, so
#: they are not real and never were.
_CANARIES = (
    ("github-pat", 'token = "ghp_' + "0" * 36 + '"'),
    ("aws-access-key", 'aws_key = "AKIAIOSFODNN7EXAMPLE"'),
    ("private-key", "-----BEGIN RSA PRIVATE KEY-----"),
)


def _assert_detectors_fire() -> list[str]:
    """Prove each detector set fires, and say which ones did.

    A scanner that silently stops matching is worse than no scanner,
    because the clean report is taken as evidence. This plants a known
    synthetic credential and refuses to report a clean history unless
    the detectors catch it.
    """
    working: list[str] = []

    missed = [
        name for name, sample in _CANARIES
        if not PATTERNS[name].search(sample)
    ]
    if missed:
        raise SystemExit(
            f"pattern detectors failed their own canary: {missed}. "
            f"Refusing to report on this history."
        )
    working.append("patterns")

    if _detect_secrets_importable():
        from detect_secrets.core.scan import scan_line
        from detect_secrets.settings import default_settings

        with default_settings():
            caught = [
                sample for _, sample in _CANARIES if list(scan_line(sample))
            ]
        if not caught:
            raise SystemExit(
                "detect-secrets is installed but caught none of the "
                "canaries, so its plugin set is not loaded. Refusing to "
                "report on this history."
            )
        working.append("detect-secrets")
    return working


def attribute_commits(findings: list[Finding]) -> None:
    """Name the commits a blocking blob is reachable from.

    Only for blockers: resolving this for every expected finding in the
    test suite would cost far more than it tells anyone.
    """
    blocking = [f for f in findings if f.tier == "blocker" and f.blob]
    if not blocking:
        return
    wanted = {f.blob for f in blocking}
    by_blob: dict[str, list[str]] = defaultdict(list)
    for commit in _run("git", "rev-list", "--all").split():
        listing = subprocess.run(
            ["git", "ls-tree", "-r", commit],
            capture_output=True, text=True,
        ).stdout
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[2] in wanted:
                by_blob[parts[2]].append(commit[:12])
    for finding in blocking:
        finding.commits = by_blob.get(finding.blob, [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree", action="store_true",
        help="scan the working tree only, not history",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    detector_sets = _assert_detectors_fire()
    targets = tree_files() if args.tree else reachable_blobs()
    findings = scan_patterns(targets) + scan_detect_secrets(targets)
    attribute_commits(findings)

    findings = _deduplicate(findings)
    triage = load_triage()
    for finding in findings:
        finding.triage_reason = triage.get(
            (finding.path, finding.detector), "",
        )
    triaged = [f for f in findings if f.tier == "triaged"]
    blockers = [f for f in findings if f.tier == "blocker"]
    review = [f for f in findings if f.tier == "review"]
    expected = [f for f in findings if f.tier == "expected"]
    detectors = sorted({f.detector.split(":")[0] for f in findings})

    if args.json:
        print(json.dumps({
            "scope": "working-tree" if args.tree else "all-refs",
            "objects_scanned": len(targets),
            "detectors_that_fired": detectors,
            "detector_sets_proven_working": detector_sets,
            "blockers": [f.as_dict() for f in blockers],
            "review": [f.as_dict() for f in review],
            "triaged": [f.as_dict() for f in triaged],
            "expected": [f.as_dict() for f in expected],
        }, indent=2))
        return 1 if blockers else 0

    scope = "working tree" if args.tree else "every blob reachable from any ref"
    print(f"Scanned {len(targets)} object(s) — {scope}.")
    print(f"Detector sets proven against canaries: "
          f"{', '.join(detector_sets)}.")

    if expected:
        conclusive = [f for f in expected if f.plugin not in ENTROPY_PLUGINS]
        print(
            f"\n{len(expected)} finding(s) in expected locations "
            f"({len(conclusive)} conclusive, the rest entropy-only)."
        )
        if conclusive:
            print(
                "  Conclusive ones, which are the synthetic credentials "
                "the redaction tests assert on:"
            )
            for path, group in sorted(_group(conclusive).items()):
                plugins = sorted(d.partition(":")[2] or d for d in group)
                print(f"    {path}: {', '.join(plugins)}")
        print(
            "  Shown rather than filtered: location is the only thing "
            "that makes a synthetic token benign, so the scan reports "
            "them and says why."
        )

    if review:
        paths = len({f.path for f in review})
        print(
            f"\n{len(review)} entropy-only finding(s) across {paths} path(s) "
            f"— for review, not blocking."
        )
        print(
            "  Entropy at line granularity fires on ordinary prose and "
            "identifiers in this codebase; it cannot establish that "
            "anything is a credential. Spot-checked: docstrings, import "
            "lines and the heading '# TPLLM'."
        )

    if triaged:
        print(f"\n{len(triaged)} audited false positive(s):")
        for finding in triaged:
            print(f"  {finding.plugin} at {finding.path}")
            print(f"    reviewed: {finding.triage_reason[:150]}")

    if blockers:
        print(f"\n{len(blockers)} BLOCKING finding(s):")
        for finding in blockers:
            where = (
                f" in commits {', '.join(finding.commits)}"
                if finding.commits else ""
            )
            print(f"  !! {finding.detector} at {finding.path}{where}")
        print("\nPublication blocker. Rotate the credential first, then "
              "remove it from history, then re-run this scan.")
        return 1

    print("\nNo credential material outside the expected locations.")
    return 0


def _detect_secrets_importable() -> bool:
    try:
        import detect_secrets  # noqa: F401
        return True
    except ImportError:
        return False


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    """One finding per (detector, path). The same line matching eleven
    times says nothing more than it matching once."""
    seen: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        seen.setdefault((finding.detector, finding.path), finding)
    return list(seen.values())


def _group(findings: list[Finding]) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for finding in findings:
        grouped[finding.path].add(finding.detector)
    return grouped


if __name__ == "__main__":
    sys.exit(main())
