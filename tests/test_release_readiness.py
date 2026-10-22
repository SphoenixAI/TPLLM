"""Release-readiness checks (Commit 42).

An audit done once is a snapshot; the things it checked drift back. These
are the parts of the open-source release audit that can be asserted, so
that a build which is not publishable fails rather than ships.

What is checked here, and why each one is a real risk rather than a
formality:

- **Nothing private ships.** The wheel is built and inspected. Tests,
  recorded fixtures, caches and any database file must not be in it;
  ``py.typed`` and the recorded provider contracts must be, because
  `lltpm integrations verify` has to work from an installed wheel.
- **No secret and no owner identity is committed.** Checked against the
  tracked file list, because the risk is what git has, not what the
  working tree happens to show.
- **Every version agrees.** Three places state it and two are derived;
  a mismatch means a journal records a version that was never released.
- **Every optional dependency is installable.** An extra naming a
  package that does not exist turns `pip install lltpm[voice]` into a
  resolution error for a user who did nothing wrong.
- **Public metadata exists.** A PyPI page with no repository link and no
  Python classifiers is a worse first impression than no page.

Network-dependent checks (that each extra resolves on PyPI) are left to
the audit document rather than asserted here: a suite that fails when
PyPI is unreachable is a suite people learn to ignore.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tomllib
import zipfile

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Paths that must never appear inside a built wheel.
FORBIDDEN_IN_WHEEL = (
    "tests/",
    "fixtures/",
    "__pycache__",
    ".pyc",
    ".db",
    ".sqlite",
    ".env",
)

#: Paths that must appear, each because something breaks without it.
REQUIRED_IN_WHEEL = {
    "lltpm/py.typed": (
        "PEP 561 marker; without it a consumer's type checker ignores "
        "every annotation in the package"
    ),
    "lltpm/integrations/contract_data/github.json": (
        "`lltpm integrations verify` reads the recorded contracts, and "
        "has to work from an installed wheel rather than a checkout"
    ),
}


def _expected_prefixes() -> tuple[str, ...]:
    """The history scanner's own list of locations that legitimately
    contain credential-shaped strings.

    Read by parsing rather than importing. Executing the script would
    register nothing in `sys.modules`, which the dataclass machinery in
    it needs, and a test has no business running a scanner's
    module-level code to read one constant.
    """
    import ast

    source = (_ROOT / "scripts" / "scan_history.py").read_text()
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "EXPECTED_PREFIXES"
                for target in node.targets
            )
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(
        "scripts/scan_history.py no longer declares EXPECTED_PREFIXES"
    )


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text())


@pytest.fixture(scope="module")
def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [line for line in result.stdout.splitlines() if line]


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> list[str]:
    out = tmp_path_factory.mktemp("wheel")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot build a wheel here: {result.stderr[-200:]}")
    wheels = sorted(out.glob("*.whl"))
    assert wheels, "the build produced no wheel"
    return zipfile.ZipFile(wheels[-1]).namelist()


# ── What ships ───────────────────────────────────────────────────────


class TestTheWheelContents:
    @pytest.mark.parametrize("forbidden", FORBIDDEN_IN_WHEEL)
    def test_nothing_private_ships(self, wheel_names, forbidden):
        offenders = [name for name in wheel_names if forbidden in name]
        # `stress_test.py` is a source module about structural stress
        # tests, not a test file; the substring match would catch it.
        offenders = [
            name for name in offenders if not name.endswith("stress_test.py")
        ]
        assert offenders == [], (
            f"{offenders[:5]} contain '{forbidden}' and must not be in a "
            f"published wheel"
        )

    @pytest.mark.parametrize(
        "required", sorted(REQUIRED_IN_WHEEL),
        ids=lambda path: path.split("/")[-1],
    )
    def test_what_has_to_ship_ships(self, wheel_names, required):
        assert required in wheel_names, (
            f"{required} is missing: {REQUIRED_IN_WHEEL[required]}"
        )

    def test_the_licence_ships(self, wheel_names):
        assert any("LICENSE" in name for name in wheel_names)

    def test_the_wheel_is_not_suspiciously_large(self, wheel_names):
        """A wheel that suddenly doubles usually means data got in."""
        assert len(wheel_names) < 500, (
            f"{len(wheel_names)} entries; check what was added"
        )


# ── What is committed ────────────────────────────────────────────────


class TestNothingPrivateIsCommitted:
    #: Credential shapes. Matched against tracked files only — the risk
    #: is what git has, not what is lying around locally.
    SECRET_PATTERNS = (
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{30,}"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    )

    def test_no_credential_is_committed(self, tracked_files):
        """Scanned outside the locations the history scanner already
        knows about.

        The exclusion list is imported from `scripts/scan_history.py`
        rather than restated here. Two scanners with two lists drifted
        the moment the second one existed: this test flagged the history
        scanner's own AWS canary, which the history scanner had always
        known to expect. One list, one place.

        Those locations are excluded because a test asserting credential
        redaction has to contain the thing being redacted, and a
        scanner's self-test has to contain a credential-shaped string to
        prove it still fires. No scanner can tell those from a real
        leak by content alone.
        """
        expected = _expected_prefixes()
        offenders = []
        for name in tracked_files:
            path = _ROOT / name
            if name.startswith(expected):
                continue
            if not path.is_file() or path.suffix in {".png", ".jpg", ".ico"}:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for pattern in self.SECRET_PATTERNS:
                match = pattern.search(text)
                if match:
                    # Report the location, never the value.
                    offenders.append(f"{name} ({pattern.pattern[:18]}…)")
                    break
        assert offenders == [], f"credential material in {offenders}"

    def test_no_database_or_environment_file_is_committed(
        self, tracked_files,
    ):
        offenders = [
            name for name in tracked_files
            if name.endswith((".db", ".sqlite", ".sqlite3"))
            or pathlib.Path(name).name == ".env"
        ]
        assert offenders == [], f"{offenders} should not be in git"

    def test_the_example_environment_file_holds_only_placeholders(self):
        """`.env.example` is meant to be copied, so a real value in it
        would be copied too."""
        example = _ROOT / ".env.example"
        if not example.exists():
            pytest.skip("no .env.example")
        for line in example.read_text().splitlines():
            if not line.strip() or line.strip().startswith("#"):
                continue
            _, _, value = line.partition("=")
            value = value.strip().strip("'\"")
            assert not value or value.endswith("...") or value.islower() or (
                value.startswith("<") or value in {"", "changeme"}
            ), f"'{line}' looks like a real value"


# ── Versions agree ───────────────────────────────────────────────────


class TestVersionsAgree:
    def test_pyproject_and_the_journal_declaration_match(self, pyproject):
        """`DECLARED_LLTPM_VERSION` is the fallback stamped into a
        reproducibility manifest when the package is not installed. If it
        lags, history records a version that was never released."""
        from lltpm.persistence.versioning import DECLARED_LLTPM_VERSION

        assert DECLARED_LLTPM_VERSION == pyproject["project"]["version"]

    def test_the_installed_version_is_read_from_metadata(self):
        """The convention is that the version lives in pyproject.toml
        only. This asserts nothing hardcodes it a third time."""
        import lltpm
        from lltpm.persistence.versioning import LLTPM_VERSION

        assert lltpm.__version__ == LLTPM_VERSION

    def test_no_module_hardcodes_a_release_version(self):
        """`0.0.0+unknown` is the sentinel used when the package is not
        installed, and is allowed. A real version number is not."""
        offenders = []
        pattern = re.compile(r"__version__\s*=\s*[\"'](\d+\.\d+[^\"']*)")
        for path in sorted((_ROOT / "src" / "lltpm").rglob("*.py")):
            for value in pattern.findall(path.read_text()):
                if value.startswith("0.0.0"):
                    continue
                offenders.append(f"{path.relative_to(_ROOT)}:{value}")
        assert offenders == [], f"{offenders} hardcode a release version"


# ── Public metadata ──────────────────────────────────────────────────


class TestPublicMetadata:
    def test_the_package_states_where_it_lives(self, pyproject):
        urls = pyproject["project"].get("urls", {})
        assert "Repository" in urls and "Issues" in urls, (
            "a PyPI page with no repository or issue link is a worse "
            "first impression than no page"
        )
        for name, url in urls.items():
            assert url.startswith("https://"), f"{name} is not https"

    def test_every_supported_python_is_declared(self, pyproject):
        classifiers = pyproject["project"].get("classifiers", [])
        declared = {
            c.rsplit("::", 1)[-1].strip() for c in classifiers
            if c.startswith("Programming Language :: Python :: 3.")
        }
        assert declared == {"3.11", "3.12", "3.13"}, (
            f"classifiers say {sorted(declared)}; requires-python says "
            f"{pyproject['project']['requires-python']}"
        )

    def test_the_typing_marker_is_advertised(self, pyproject):
        assert "Typing :: Typed" in pyproject["project"]["classifiers"]

    def test_the_licence_is_declared_once_and_matches_the_file(
        self, pyproject,
    ):
        assert pyproject["project"]["license"] == "MIT"
        licence = (_ROOT / "LICENSE").read_text()
        assert "MIT License" in licence
        assert "Sphoenix" in licence

    def test_every_extra_is_reachable_from_a_documented_name(
        self, pyproject,
    ):
        """An extra nobody documents is an extra nobody installs."""
        extras = set(pyproject["project"]["optional-dependencies"])
        readme = (_ROOT / "README.md").read_text()
        undocumented = sorted(
            name for name in extras
            if name not in readme and name not in {"all"}
        )
        assert undocumented == [], (
            f"extras {undocumented} are not mentioned in the README"
        )


# ── Documented claims ────────────────────────────────────────────────


class TestDocumentedClaims:
    def test_the_advertised_test_count_is_not_an_overclaim(self):
        """A stated figure that the suite has outgrown is harmless; one
        it has never reached is not."""
        readme = (_ROOT / "README.md").read_text()
        claims = re.findall(r"([\d,]+)\+?\s*(?:tests|test)", readme)
        for claim in claims:
            stated = int(claim.replace(",", ""))
            assert stated <= 3462, (
                f"the README claims {stated} tests; the suite has 3462"
            )

    def test_the_readme_links_resolve(self):
        readme = (_ROOT / "README.md").read_text()
        for target in re.findall(r"\]\((?!https?:)([^)#]+)", readme):
            assert (_ROOT / target).exists(), f"dead link: {target}"

    def test_the_research_note_links_resolve(self):
        note = _ROOT / "docs" / "research-alignment.md"
        for target in re.findall(r"\]\((?!https?:)([^)#]+)", note.read_text()):
            assert (_ROOT / target).exists(), f"dead link: {target}"

    def test_the_changelog_covers_the_current_version(self, pyproject):
        changelog = _ROOT / "CHANGELOG.md"
        assert changelog.exists(), "a public release needs a changelog"
        assert pyproject["project"]["version"] in changelog.read_text(), (
            "the changelog does not mention the version being released"
        )


@pytest.fixture(scope="module")
def sdist_names(tmp_path_factory) -> list[str]:
    """Every path inside a built source distribution.

    Commit 42 inspected the wheel only. An sdist is built from a
    different file set — it includes what the wheel excludes — so a
    database, a `.env` or a developer cache can ship in one and not the
    other, and the sdist is what a `pip install` from source uses.
    """
    import tarfile

    out = tmp_path_factory.mktemp("sdist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(out)],
        cwd=_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot build an sdist here: {result.stderr[-200:]}")
    archives = sorted(out.glob("*.tar.gz"))
    assert archives, "the build produced no sdist"
    with tarfile.open(archives[-1]) as archive:
        return archive.getnames()


class TestTheSdistContents:
    #: Paths that must never be in a published source distribution.
    FORBIDDEN = (
        "__pycache__", ".pyc", ".db", ".sqlite", ".pytest_cache",
        "/.lltpm/", ".secrets-triage",
    )

    @pytest.mark.parametrize("forbidden", FORBIDDEN)
    def test_nothing_private_ships_in_the_sdist(
        self, sdist_names, forbidden,
    ):
        offenders = [name for name in sdist_names if forbidden in name]
        assert offenders == [], (
            f"{offenders[:5]} contain '{forbidden}' and must not be in a "
            f"published sdist"
        )

    def test_a_real_environment_file_never_ships(self, sdist_names):
        """`.env.example` is a template and is useful to ship. A real
        `.env` holds the credentials it is a template for."""
        offenders = [
            name for name in sdist_names
            if pathlib.Path(name).name == ".env"
        ]
        assert offenders == [], offenders

    def test_the_sdist_carries_the_licence_and_readme(self, sdist_names):
        assert any(name.endswith("LICENSE") for name in sdist_names)
        assert any(name.endswith("README.md") for name in sdist_names)

    def test_the_sdist_carries_the_package_source(self, sdist_names):
        assert any(
            name.endswith("src/lltpm/__init__.py") for name in sdist_names
        )

    def test_the_sdist_carries_the_recorded_contracts(self, sdist_names):
        """`lltpm integrations verify` has to work from a source
        install too."""
        assert any(
            name.endswith("integrations/contract_data/github.json")
            for name in sdist_names
        )

    def test_the_sdist_is_not_suspiciously_large(self, sdist_names):
        assert len(sdist_names) < 900, (
            f"{len(sdist_names)} entries; check what was added"
        )
