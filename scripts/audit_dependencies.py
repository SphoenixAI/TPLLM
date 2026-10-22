#!/usr/bin/env python3
"""Audit TPLLM's dependency closure for known vulnerabilities, per extra.

`pip-audit` with no arguments audits whatever happens to be installed in
the current environment. In a development container that is the wrong
question: it reported 89 vulnerabilities across 8 packages here, and not
one of them was an TPLLM dependency — they were the container's own
`pip`, `setuptools`, `wheel`, and transitive packages of extras that had
been installed for other reasons. Publishing that number as "TPLLM's
vulnerabilities" would be false in both directions: it overstates what
the project ships and says nothing about the extras nobody installed.

So this resolves the closure for each extra *separately*, without
installing anything, and audits each one. A finding is then attributable:
"installing `tpllm[integrations]` pulls a vulnerable `oauthlib`" is
actionable, and "the container's `pip` is old" is not a release concern.

Base is audited as a release blocker. An extra is reported with the
extra named, because a vulnerability in something a user may never
install should not block a release — but it must not be invisible
either.

Usage:

    python scripts/audit_dependencies.py
    python scripts/audit_dependencies.py --extras integrations dashboard
    python scripts/audit_dependencies.py --json

Needs network access to PyPI for resolution and to the advisory
database. Exit status is 1 when the base closure has a vulnerability.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Extras audited by default. `all` is skipped because it is a union of
#: the others and would double every finding.
DEFAULT_EXTRAS = (
    "integrations", "dashboard", "email", "openai", "pdf", "voice", "dev",
)


def resolve_closure(target: str) -> list[str]:
    """Every package pip would install for ``target``, without installing.

    Uses pip's resolver report so the answer is the real closure rather
    than the top-level list in pyproject.toml. Transitive packages are
    where the vulnerabilities actually live.
    """
    with tempfile.TemporaryDirectory() as directory:
        report = pathlib.Path(directory) / "report.json"
        result = subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--dry-run",
                "--ignore-installed", "--quiet",
                "--report", str(report), target,
            ],
            capture_output=True, text=True, cwd=_ROOT,
        )
        if result.returncode != 0 or not report.exists():
            raise RuntimeError(
                f"could not resolve {target}: "
                f"{result.stderr.strip()[-300:]}"
            )
        data = json.loads(report.read_text())

    pinned = []
    for item in data.get("install", []):
        metadata = item.get("metadata", {})
        name, version = metadata.get("name"), metadata.get("version")
        if not name or not version:
            continue
        # The project itself is the subject of the audit, not one of its
        # dependencies. Leaving it in makes the closure unresolvable
        # against PyPI, because this version is not published there.
        if name.lower().replace("_", "-") == "tpllm":
            continue
        pinned.append(f"{name}=={version}")
    return sorted(pinned)


def audit(pinned: list[str]) -> list[dict]:
    """Audit a pinned closure, returning only the vulnerable packages."""
    if not pinned:
        return []
    with tempfile.TemporaryDirectory() as directory:
        requirements = pathlib.Path(directory) / "requirements.txt"
        requirements.write_text("\n".join(pinned) + "\n")
        output = pathlib.Path(directory) / "audit.json"
        subprocess.run(
            [
                sys.executable, "-m", "pip_audit",
                "--requirement", str(requirements),
                "--format", "json", "--progress-spinner", "off",
                "--no-deps", "-o", str(output),
            ],
            capture_output=True, text=True,
        )
        if not output.exists():
            return []
        data = json.loads(output.read_text())

    packages = data.get("dependencies", data)
    return [
        {
            "name": package["name"],
            "version": package["version"],
            "advisories": sorted({v["id"] for v in package["vulns"]}),
            "fix_versions": sorted({
                version
                for v in package["vulns"]
                for version in (v.get("fix_versions") or [])
            }),
        }
        for package in packages if package.get("vulns")
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extras", nargs="*", default=list(DEFAULT_EXTRAS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    declared = set(
        tomllib.loads((_ROOT / "pyproject.toml").read_text())
        ["project"]["optional-dependencies"]
    )
    unknown = sorted(set(args.extras) - declared)
    if unknown:
        raise SystemExit(f"no such extra: {unknown}; declared: {sorted(declared)}")

    results: dict[str, dict] = {}
    for target, label in [(".", "base")] + [
        (f".[{extra}]", extra) for extra in args.extras
    ]:
        try:
            pinned = resolve_closure(target)
        except RuntimeError as error:
            results[label] = {"error": str(error)}
            continue
        results[label] = {
            "packages_resolved": len(pinned),
            "vulnerable": audit(pinned),
        }

    blocking = results.get("base", {}).get("vulnerable") or []

    if args.json:
        print(json.dumps({
            "blocking": bool(blocking),
            "closures": results,
        }, indent=2))
        return 1 if blocking else 0

    for label, result in results.items():
        if "error" in result:
            print(f"{label:<14} could not resolve: {result['error'][:90]}")
            continue
        vulnerable = result["vulnerable"]
        count = result["packages_resolved"]
        if not vulnerable:
            print(f"{label:<14} {count:>3} package(s), clean")
            continue
        print(f"{label:<14} {count:>3} package(s), {len(vulnerable)} vulnerable:")
        for package in vulnerable:
            fixes = ", ".join(package["fix_versions"][:3]) or "none published"
            print(
                f"    {package['name']} {package['version']}: "
                f"{len(package['advisories'])} advisory(ies), fixed in {fixes}"
            )

    print()
    if blocking:
        print(
            "BLOCKING: the base install has a known vulnerability. Every "
            "user gets this one."
        )
        return 1
    print(
        "Base install is clean. Findings in an extra are reported with the "
        "extra named rather than blocking: a user who never installs it is "
        "not exposed, and one who does needs to know which."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
