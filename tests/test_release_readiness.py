"""Release-readiness checks.

An audit done once is a snapshot; the things it checked drift back. These
are the parts of the open-source release audit that can be asserted, so
that a build which is not publishable fails rather than ships.

What is checked here, and why each one is a real risk rather than a
formality:

- **Nothing private ships.** The wheel is built and inspected. Tests,
  recorded fixtures, caches and any database file must not be in it;
  ``py.typed`` and the recorded provider contracts must be, because
  `tpllm integrations verify` has to work from an installed wheel.
- **No secret and no owner identity is committed.** Checked against the
  tracked file list, because the risk is what git has, not what the
  working tree happens to show.
- **Every version agrees.** Three places state it and two are derived;
  a mismatch means a journal records a version that was never released.
- **Every optional dependency is installable.** An extra naming a
  package that does not exist turns `pip install tpllm[voice]` into a
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
    "tpllm/py.typed": (
        "PEP 561 marker; without it a consumer's type checker ignores "
        "every annotation in the package"
    ),
    "tpllm/integrations/contract_data/github.json": (
        "`tpllm integrations verify` reads the recorded contracts, and "
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
        """`DECLARED_TPLLM_VERSION` is the fallback stamped into a
        reproducibility manifest when the package is not installed. If it
        lags, history records a version that was never released."""
        from tpllm.persistence.versioning import DECLARED_TPLLM_VERSION

        assert DECLARED_TPLLM_VERSION == pyproject["project"]["version"]

    def test_the_installed_version_is_read_from_metadata(self):
        """The convention is that the version lives in pyproject.toml
        only. This asserts nothing hardcodes it a third time."""
        import tpllm
        from tpllm.persistence.versioning import TPLLM_VERSION

        assert tpllm.__version__ == TPLLM_VERSION

    def test_no_module_hardcodes_a_release_version(self):
        """`0.0.0+unknown` is the sentinel used when the package is not
        installed, and is allowed. A real version number is not."""
        offenders = []
        pattern = re.compile(r"__version__\s*=\s*[\"'](\d+\.\d+[^\"']*)")
        for path in sorted((_ROOT / "src" / "tpllm").rglob("*.py")):
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

    The release audit inspected the wheel only. An sdist is built from a
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
        "/.tpllm/", ".secrets-triage",
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
            name.endswith("src/tpllm/__init__.py") for name in sdist_names
        )

    def test_the_sdist_carries_the_recorded_contracts(self, sdist_names):
        """`tpllm integrations verify` has to work from a source
        install too."""
        assert any(
            name.endswith("integrations/contract_data/github.json")
            for name in sdist_names
        )

    def test_the_sdist_is_not_suspiciously_large(self, sdist_names):
        assert len(sdist_names) < 900, (
            f"{len(sdist_names)} entries; check what was added"
        )


# ── Public presentation ──────────────────────────────────────────────


class TestNoInternalScaffolding:
    """This repository is public, and a reader has no commit numbering.

    The code was built over a long numbered commit arc, and the numbers
    had leaked into 268 docstrings and comments in shipped source:
    "Application requires Commit 7's single green light", "the Commit 24
    lattice". Internally those were precise. Publicly they point at
    nothing — the public history has hashes — so each one was replaced
    with the name of the thing the number stood for. These tests keep
    them from coming back one convenient reference at a time.
    """

    #: Files that must be able to quote what they forbid: this one
    #: states the rules, `test_integration_contracts.py` exercises the
    #: redaction enforcing one of them, and a changelog entry about
    #: removing a string has to name the string. Same reasoning as the
    #: credential scan excluding `tests/` — but named paths rather than
    #: a glob, because the fixtures that actually leaked are in `tests/`
    #: too and stay checked.
    SELF = "tests/test_release_readiness.py"
    QUOTES_WHAT_IT_FORBIDS = {
        SELF,
        "tests/test_integration_contracts.py",
        "CHANGELOG.md",
    }

    #: Paths where a commit reference would be about *this* repository's
    #: own history rather than internal scaffolding, and so is fine.
    SCAFFOLDING_EXEMPT = QUOTES_WHAT_IT_FORBIDS | {".github/workflows/ci.yml"}

    def test_no_commit_numbers_in_tracked_files(self, tracked_files):
        pattern = re.compile(r"Commits? \d+")
        offenders = []
        for name in tracked_files:
            if name in self.SCAFFOLDING_EXEMPT:
                continue
            path = _ROOT / name
            if path.suffix not in {".py", ".md", ".toml", ".yml", ".sh"}:
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{name}:{number}: {line.strip()}")
        assert offenders == [], (
            "internal commit numbering is meaningless to a reader of the "
            f"public repository; name the thing instead: {offenders[:5]}"
        )

    def test_the_product_name_is_spelled_one_way(self, tracked_files):
        """The product is TPLLM — Technical Program Manager, LLM.

        `LLTPM` is the transposition, and it had become the dominant
        spelling: the package, the CLI, every environment variable and
        the repository itself, with TPLLM surviving only in newer
        docstrings. Renamed wholesale before publication, while there
        was no released package and no user to keep compatible. This
        catches the transposition coming back.
        """
        offenders = [
            name for name in tracked_files
            if name not in self.QUOTES_WHAT_IT_FORBIDS
            and (_ROOT / name).is_file()
            and (_ROOT / name).suffix in {".py", ".md", ".toml", ".yml", ".sh"}
            and "LLTPM" in (_ROOT / name).read_text(errors="ignore").upper()
        ]
        assert offenders == [], f"'LLTPM' should be 'TPLLM': {offenders}"

    def test_no_assistant_session_links_are_committed(self, tracked_files):
        """A session URL is a private link into somebody's account.

        It is not a credential, which is exactly why it survives a
        secret scan — so it gets its own check.
        """
        pattern = re.compile(r"claude\.ai/(?:code/)?session[_/]")
        offenders = []
        for name in tracked_files:
            if name in self.QUOTES_WHAT_IT_FORBIDS:
                continue
            path = _ROOT / name
            if not path.is_file():
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            if pattern.search(text):
                offenders.append(name)
        assert offenders == [], (
            f"private assistant session links are committed: {offenders}"
        )

    def test_the_engineering_handbook_is_where_the_readme_says(self):
        handbook = _ROOT / "docs" / "engineering-handbook.md"
        assert handbook.exists(), (
            "the README and docs/assistant-development-guide.md both point at this file"
        )
        text = handbook.read_text()
        assert "Architectural invariants" in text
        assert "Development conventions" in text


class TestLocalStateCannotBeCommitted:
    """The documentation tells a reader to create files in the working
    directory — `tpllm.db`, `run.jsonl`, `sbom.json`. The journal is the
    organization's durable record, so an ignore rule that misses it
    turns following the README into publishing your own history."""

    @pytest.fixture(scope="class")
    @staticmethod
    def ignored():
        def check(*names: str) -> list[str]:
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", *names],
                cwd=_ROOT, capture_output=True, text=True,
            )
            if result.returncode not in (0, 1):
                pytest.skip("git check-ignore unavailable")
            matched = set(result.stdout.split())
            return [n for n in names if n not in matched]
        return check

    def test_databases_and_local_output_are_ignored(self, ignored):
        missed = ignored(
            "tpllm.db", "tpllm.sqlite", "run.jsonl", "sbom.json",
            ".env", ".env.local", ".tpllm/config.json", "history.json",
            ".DS_Store", ".coverage",
        )
        assert missed == [], f"not covered by .gitignore: {missed}"

    def test_the_env_template_still_ships(self, ignored, tracked_files):
        """`.env.*` has to be ignored without ignoring the template that
        tells a user which variables exist."""
        assert ignored(".env.example") == [".env.example"], (
            ".env.example is the committed template and must not be ignored"
        )
        assert ".env.example" in tracked_files

    def test_no_file_from_a_data_directory_is_tracked(self, tracked_files):
        """The data directory holds generated secrets, and an ignore rule
        does not protect a file that is already tracked.

        This is not hypothetical. Renaming the project moved the ignore
        rule from `.lltpm/` to `.tpllm/` in the same commit that renamed
        everything else, which un-ignored a local data directory that had
        been sitting there; `git add -A` committed the JWT signing key
        inside it. The secret scan would have caught it — it was run
        before that commit rather than after. So the invariant is checked
        here too, where it cannot depend on when somebody ran a script.
        """
        bad = [
            name for name in tracked_files
            if name.split("/")[0] in {
                ".tpllm", ".tpllm-example", ".lltpm", ".lltpm-example",
            }
        ]
        assert bad == [], (
            f"generated local state is committed: {bad}"
        )

    def test_no_generated_signing_key_is_tracked(self, tracked_files):
        """A content check as well as a path check, because the next data
        directory will have a name nobody listed here."""
        pattern = re.compile(r'"(?:secret_key|signing_key|private_key)"\s*:')
        offenders = []
        for name in tracked_files:
            if name.startswith("tests/") or name in (
                self.__class__.__dict__.get("ALLOWED", ())
            ):
                continue
            path = _ROOT / name
            if not path.is_file() or path.suffix in {".png", ".ico"}:
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            if pattern.search(text):
                offenders.append(name)
        assert offenders == [], (
            f"a generated key looks committed: {offenders}"
        )

    def test_no_database_or_env_file_is_tracked(self, tracked_files):
        bad = [
            name for name in tracked_files
            if name == ".env"
            or name.startswith(".env.") and name != ".env.example"
            or name.endswith((".db", ".sqlite", ".sqlite3"))
        ]
        assert bad == [], f"local state is committed: {bad}"
