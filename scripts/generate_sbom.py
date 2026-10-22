#!/usr/bin/env python3
"""Generate a CycloneDX SBOM for the release artifact.

Built from the resolved dependency closure rather than from whatever is
installed in the current environment, for the same reason the
vulnerability audit is: a development container holds packages the
release does not ship, and an SBOM that listed them would describe
something nobody will ever install.

Generated, never hand-maintained. A static SBOM is wrong the moment a
floor moves, and being confidently wrong about what you shipped is
worse than having no document.

Usage:

    python scripts/generate_sbom.py                      # base install
    python scripts/generate_sbom.py --extras integrations dashboard
    python scripts/generate_sbom.py -o sbom.json

Needs network access to PyPI to resolve the closure.
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


def resolve_closure(target: str) -> list[str]:
    """Pinned closure for ``target``, resolved without installing."""
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
            raise SystemExit(
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


def build_sbom_from_environment(pinned: list[str]) -> dict:
    """Install the closure into a throwaway venv and describe that.

    Slower, and the only way to get licence metadata: a requirements
    file carries names and versions, and a package's licence lives in
    its distribution metadata, which only exists once it is installed.
    An SBOM with no licence column is a poor answer to the question
    people generate SBOMs to ask.
    """
    with tempfile.TemporaryDirectory() as directory:
        venv = pathlib.Path(directory) / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            check=True, capture_output=True,
        )
        python = venv / "bin" / "python"
        requirements = pathlib.Path(directory) / "requirements.txt"
        requirements.write_text("\n".join(pinned) + "\n")
        install = subprocess.run(
            [
                str(python), "-m", "pip", "install", "--quiet",
                "--no-deps", "-r", str(requirements),
            ],
            capture_output=True, text=True,
        )
        if install.returncode != 0:
            raise SystemExit(
                f"could not install the closure: "
                f"{install.stderr.strip()[-300:]}"
            )
        result = subprocess.run(
            [
                sys.executable, "-m", "cyclonedx_py", "environment",
                str(python), "--output-format", "JSON",
                "--spec-version", "1.6",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"cyclonedx-py failed: {result.stderr.strip()[-300:]}"
            )
        sbom = json.loads(result.stdout)
        _enrich_licences(sbom, python)
        return sbom


#: Read licences out of the installed distributions. cyclonedx-py 7.5
#: leaves the licence field empty for packages that declare
#: `License-Expression` (packaging metadata 2.4), which by now is most
#: of them — so every component came back unlicensed. The data is in the
#: distribution metadata either way, so it is read directly rather than
#: shipping an SBOM whose licence column is uniformly blank.
_LICENCE_PROBE = """
import json
from importlib.metadata import distributions

found = {}
for dist in distributions():
    name = dist.metadata["Name"]
    if not name:
        continue
    licence = (
        dist.metadata.get("License-Expression")
        or dist.metadata.get("License")
        or ""
    ).strip()
    if not licence or len(licence) > 64 or "\n" in licence:
        classifiers = [
            c.rsplit("::", 1)[-1].strip()
            for c in dist.metadata.get_all("Classifier") or []
            if c.startswith("License ::")
        ]
        licence = "; ".join(classifiers)
    if licence:
        found[name.lower()] = licence
print(json.dumps(found))
"""


def _enrich_licences(sbom: dict, python: pathlib.Path) -> None:
    probe = subprocess.run(
        [str(python), "-c", _LICENCE_PROBE],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return
    licences = json.loads(probe.stdout or "{}")
    for component in sbom.get("components", []):
        if component.get("licenses"):
            continue
        licence = licences.get(str(component.get("name", "")).lower())
        if licence:
            component["licenses"] = [{"license": {"name": licence}}]


def build_sbom(pinned: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        requirements = pathlib.Path(directory) / "requirements.txt"
        requirements.write_text("\n".join(pinned) + "\n")
        result = subprocess.run(
            [
                sys.executable, "-m", "cyclonedx_py", "requirements",
                str(requirements), "--output-format", "JSON",
                "--spec-version", "1.6",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"cyclonedx-py failed: {result.stderr.strip()[-300:]}"
            )
        return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extras", nargs="*", default=[])
    parser.add_argument("-o", "--output", default="")
    parser.add_argument(
        "--fast", action="store_true",
        help=(
            "resolve only, without installing. Faster, and the "
            "components carry no licence metadata."
        ),
    )
    args = parser.parse_args()

    project = tomllib.loads(
        (_ROOT / "pyproject.toml").read_text()
    )["project"]
    declared = set(project["optional-dependencies"])
    unknown = sorted(set(args.extras) - declared)
    if unknown:
        raise SystemExit(f"no such extra: {unknown}")

    target = "." if not args.extras else f".[{','.join(sorted(args.extras))}]"
    pinned = resolve_closure(target)
    sbom = (
        build_sbom(pinned) if args.fast
        else build_sbom_from_environment(pinned)
    )

    # Name what this SBOM is *of*. A CycloneDX document whose metadata
    # describes a requirements file tells a consumer nothing about which
    # release it belongs to.
    sbom.setdefault("metadata", {})["component"] = {
        "type": "application",
        "name": project["name"],
        "version": project["version"],
        "licenses": [{"license": {"id": project["license"]}}],
        "description": project["summary"] if "summary" in project else (
            project.get("description", "")
        ),
        "properties": [
            {"name": "tpllm:closure", "value": target},
            {
                "name": "tpllm:extras",
                "value": ",".join(sorted(args.extras)) or "none",
            },
            {"name": "tpllm:packages", "value": str(len(pinned))},
            {
                "name": "tpllm:licence-metadata",
                "value": "absent (--fast)" if args.fast else "present",
            },
        ],
    }

    text = json.dumps(sbom, indent=2, sort_keys=True) + "\n"
    if args.output:
        pathlib.Path(args.output).write_text(text)
        print(
            f"SBOM for {target}: {len(pinned)} package(s) -> {args.output}"
        )
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
