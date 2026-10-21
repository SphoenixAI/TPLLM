"""Tests for Commit 37 — recorded provider contracts.

Two failure modes bound this design, and a test suite has to sit between
them. Testing an integration against the live API needs credentials and
network and breaks for reasons unrelated to the code; testing it against
a hand-written mock passes forever while the real API moves. Fowler's
self-initializing fake is the resolution: record once, replay offline,
and re-check the recording against reality on its own schedule.

So what is pinned here is not "the payload looks like this". It is:

- the contract covers exactly the fields the normalizers read, so a new
  provider field cannot fail the build and a renamed one must;
- the recordings are shape-faithful but name nobody;
- a fixture that is not a recording says so, and cannot be mistaken for
  evidence that the live API still agrees.
"""

from __future__ import annotations

import copy
import json

import pytest

from lltpm.integrations.contracts import (
    GITHUB_CONTRACT_PATHS,
    GITHUB_EVENT_FIELDS,
    GITHUB_ISSUE_FIELDS,
    GITHUB_PULL_REQUEST_FIELDS,
    GITHUB_REVIEW_FIELDS,
    PayloadContract,
    check_contract,
    collect_github_logins,
    extract_contract,
    resolve_path,
    scrub_github_payload,
    type_name,
    unscrubbed_paths,
)
from tests import _github_fixtures as fx


def _contract(kind: str, payloads: list[dict]) -> PayloadContract:
    return extract_contract(
        source_system="github", payload_kind=kind, payloads=payloads,
        paths=GITHUB_CONTRACT_PATHS[kind],
    )


# ── Path resolution ──────────────────────────────────────────────────


class TestPathResolution:
    PAYLOAD = {
        "number": 8,
        "user": {"login": "someone"},
        "labels": [{"name": "blocked"}, {"name": "urgent"}],
        "base": {"ref": "main"},
        "empty_labels": [],
        "nothing": None,
    }

    def test_a_plain_field(self):
        assert resolve_path(self.PAYLOAD, "number") == [8]

    def test_a_nested_field(self):
        assert resolve_path(self.PAYLOAD, "user.login") == ["someone"]

    def test_a_list_fans_out(self):
        assert resolve_path(self.PAYLOAD, "labels[].name") == [
            "blocked", "urgent",
        ]

    def test_an_empty_list_yields_nothing(self):
        assert resolve_path(self.PAYLOAD, "empty_labels[].name") == []

    def test_an_absent_path_yields_nothing(self):
        assert resolve_path(self.PAYLOAD, "milestone.title") == []

    def test_an_explicit_null_is_a_value_not_an_absence(self):
        """A field that is present and null is a different contract from
        one that is missing, and the normalizers depend on the
        difference: ``merged_at: null`` means not merged."""
        assert resolve_path(self.PAYLOAD, "nothing") == [None]

    def test_descending_into_a_scalar_yields_nothing(self):
        assert resolve_path(self.PAYLOAD, "number.deeper") == []

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, "null"), (True, "bool"), (1, "int"), (1.5, "float"),
            ("x", "str"), ([], "list"), ({}, "object"),
        ],
    )
    def test_type_labels_are_stable(self, value, expected):
        assert type_name(value) == expected

    def test_a_bool_is_not_reported_as_an_int(self):
        """Python says bool is a subclass of int; a contract that
        conflated them would accept ``draft: 1``."""
        assert type_name(True) == "bool"


# ── Contract extraction and checking ─────────────────────────────────


class TestContractExtraction:
    def test_a_contract_is_derived_from_the_recordings(self):
        contract = _contract("pull_request", fx.load("pull_requests"))
        assert contract.recorded_from_samples == len(
            fx.load("pull_requests")
        )
        assert {f.path for f in contract.fields} == set(
            GITHUB_PULL_REQUEST_FIELDS
        )

    def test_presence_is_observed_not_declared(self):
        """One sample without the field is enough to record it as
        optional, which keeps the contract honest about what was seen."""
        contract = extract_contract(
            source_system="github", payload_kind="pull_request",
            payloads=[{"node_id": "a", "title": "t"}, {"node_id": "b"}],
            paths=("node_id", "title"),
        )
        assert contract.field_for("node_id").always_present
        assert not contract.field_for("title").always_present

    def test_every_observed_type_is_recorded(self):
        contract = extract_contract(
            source_system="github", payload_kind="pull_request",
            payloads=[{"merged_at": "t"}, {"merged_at": None}],
            paths=("merged_at",),
        )
        assert contract.field_for("merged_at").types == ["null", "str"]

    def test_the_recorded_pull_requests_pin_the_real_shape(self):
        contract = _contract("pull_request", fx.load("pull_requests"))
        node_id = contract.field_for("node_id")
        assert node_id.always_present and node_id.types == ["str"]
        number = contract.field_for("number")
        assert number.always_present and number.types == ["int"]

    @pytest.mark.parametrize("kind", sorted(GITHUB_CONTRACT_PATHS))
    def test_no_contract_is_vacuous(self, kind):
        """A contract with no typed fields would pass against anything."""
        name = {
            "pull_request": "pull_requests", "issue": "issues",
            "event": "events", "review": "reviews",
        }[kind]
        contract = _contract(kind, fx.load(name))
        typed = [f for f in contract.fields if f.types]
        assert len(typed) >= len(contract.fields) - 1, (
            f"{kind}: only {len(typed)} of {len(contract.fields)} fields "
            f"were observed at all; the recording does not exercise the "
            f"contract"
        )


class TestContractChecking:
    @pytest.fixture
    def contract(self):
        return _contract("pull_request", fx.load("pull_requests"))

    def test_the_recordings_satisfy_their_own_contract(self, contract):
        for payload in fx.load("pull_requests"):
            assert check_contract(payload=payload, contract=contract) == []

    def test_a_removed_field_is_a_breach(self, contract):
        payload = copy.deepcopy(fx.merged_pull_request())
        payload.pop("node_id")
        breaches = check_contract(payload=payload, contract=contract)
        assert [b.path for b in breaches] == ["node_id"]
        assert breaches[0].kind == "missing"
        assert "a normalizer reads it" in breaches[0].explanation

    def test_a_renamed_field_is_a_breach(self, contract):
        """The scenario this exists for: GitHub renames ``merged_at`` and
        the normalizer starts reporting every merge as a closure."""
        payload = copy.deepcopy(fx.merged_pull_request())
        payload["merged_timestamp"] = payload.pop("merged_at")
        breaches = check_contract(payload=payload, contract=contract)
        assert any(b.path == "merged_at" for b in breaches)

    def test_a_retyped_field_is_a_breach(self, contract):
        payload = copy.deepcopy(fx.merged_pull_request())
        payload["number"] = "8"
        breaches = check_contract(payload=payload, contract=contract)
        assert [b.kind for b in breaches] == ["type_changed"]
        assert breaches[0].actual == "str"
        assert "is now str" in breaches[0].explanation

    def test_an_added_field_is_not_a_breach(self, contract):
        """The whole reason to record what we consume rather than what the
        provider sends."""
        payload = copy.deepcopy(fx.merged_pull_request())
        payload["brand_new_github_field"] = {"anything": [1, 2, 3]}
        assert check_contract(payload=payload, contract=contract) == []

    def test_an_unexpected_null_is_a_breach(self):
        contract = extract_contract(
            source_system="github", payload_kind="pull_request",
            payloads=[{"number": 8}], paths=("number",),
        )
        breaches = check_contract(
            payload={"number": None}, contract=contract,
        )
        assert breaches and breaches[0].actual == "null"

    def test_an_optional_field_may_be_absent(self):
        contract = extract_contract(
            source_system="github", payload_kind="pull_request",
            payloads=[{"a": 1, "b": 2}, {"a": 1}], paths=("a", "b"),
        )
        assert check_contract(payload={"a": 1}, contract=contract) == []

    def test_one_bad_element_in_a_list_is_enough(self):
        contract = extract_contract(
            source_system="github", payload_kind="issue",
            payloads=[{"labels": [{"name": "x"}]}], paths=("labels[].name",),
        )
        breaches = check_contract(
            payload={"labels": [{"name": "ok"}, {"name": 7}]},
            contract=contract,
        )
        assert len(breaches) == 1 and breaches[0].kind == "type_changed"


# ── Scrubbing ────────────────────────────────────────────────────────


class TestScrubbing:
    REAL = {
        "node_id": "PR_kwDORU82xs7GHveN",
        "title": "Fix the thing",
        "html_url": "https://github.com/RealOrg/RealRepo/pull/8",
        "user": {
            "login": "realperson",
            "id": 39153813,
            "html_url": "https://github.com/realperson",
            "events_url": "https://api.github.com/users/realperson/events",
            "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
            "gravatar_id": "",
        },
        "base": {"repo": {"full_name": "RealOrg/RealRepo", "name": "RealRepo"}},
        "repo": {"name": "RealOrg/RealRepo"},
    }
    ALIAS = "acme/atlas-service"

    @pytest.fixture
    def scrubbed(self):
        return scrub_github_payload(
            self.REAL, repository_alias=self.ALIAS,
            real_repository="RealOrg/RealRepo",
        )

    def test_handles_are_collected_before_they_are_rewritten(self):
        assert collect_github_logins(self.REAL) == {"realperson"}

    def test_a_handle_embedded_in_a_url_is_rewritten_too(self, scrubbed):
        """The bug a single-pass scrubber has: it rewrites ``login`` and
        leaves the same name sitting in ``html_url``, at which point the
        fixture is no longer pseudonymous."""
        assert "realperson" not in json.dumps(scrubbed)

    def test_the_account_id_is_rewritten(self, scrubbed):
        assert scrubbed["user"]["id"] != 39153813
        assert isinstance(scrubbed["user"]["id"], int)

    def test_avatars_and_gravatars_are_dropped(self, scrubbed):
        assert "avatar_url" not in scrubbed["user"]
        assert "gravatar_id" not in scrubbed["user"]

    def test_the_repository_name_is_replaced_everywhere(self, scrubbed):
        assert "RealOrg" not in json.dumps(scrubbed)
        assert scrubbed["base"]["repo"]["full_name"] == self.ALIAS

    def test_the_rest_shape_keeps_a_bare_repository_name(self, scrubbed):
        """``repo.name`` is ``owner/name`` in the Events API and the bare
        name in the REST resource. A fixture whose shape differs from the
        live response is not a recording of it."""
        assert scrubbed["base"]["repo"]["name"] == "atlas-service"
        assert scrubbed["repo"]["name"] == self.ALIAS

    def test_opaque_ids_are_rewritten_but_still_present(self, scrubbed):
        assert scrubbed["node_id"] != self.REAL["node_id"]
        assert scrubbed["node_id"], (
            "the normalizers key on node_id; dropping it would exercise "
            "the rejection path instead of the real one"
        )

    def test_scrubbing_is_deterministic(self):
        first = scrub_github_payload(
            self.REAL, repository_alias=self.ALIAS,
            real_repository="RealOrg/RealRepo",
        )
        second = scrub_github_payload(
            self.REAL, repository_alias=self.ALIAS,
            real_repository="RealOrg/RealRepo",
        )
        assert first == second, (
            "re-recording the same response must produce the same fixture, "
            "so a diff shows an API change rather than churn"
        )

    def test_prose_is_left_alone(self, scrubbed):
        assert scrubbed["title"] == "Fix the thing"

    def test_the_leak_detector_finds_a_leak(self):
        half_scrubbed = {"user": {"login": "alias"}, "note": "by realperson"}
        offenders = unscrubbed_paths(
            half_scrubbed, forbidden={"realperson"},
        )
        assert offenders and "realperson" in offenders[0]

    def test_the_leak_detector_passes_a_clean_payload(self, scrubbed):
        assert unscrubbed_paths(
            scrubbed, forbidden={"realperson", "RealOrg/RealRepo", "RealOrg"},
        ) == []

    def test_scrubbing_preserves_the_contract(self):
        """The point of scrubbing values rather than trimming fields: the
        fixture must still satisfy the contract the live payload did."""
        contract = _contract("pull_request", fx.load("pull_requests"))
        scrubbed = scrub_github_payload(
            fx.merged_pull_request(), repository_alias="other/repo",
            real_repository=fx.REPOSITORY,
        )
        assert check_contract(payload=scrubbed, contract=contract) == []


# ── Fixture provenance: what is and is not evidence ──────────────────


class TestFixtureProvenance:
    """``lltpm runtime coverage`` refuses to let a derived projection
    pass for an engine. The same honesty applies to fixtures: only a
    recording is evidence about the live API."""

    def test_every_fixture_file_is_in_the_index(self):
        listed = set(fx.fixture_names())
        on_disk = {
            path.stem for path in fx.FIXTURE_DIR.glob("*.json")
            if path.stem != "index"
        }
        assert listed == on_disk, (
            f"index and directory disagree: only in index "
            f"{sorted(listed - on_disk)}, only on disk "
            f"{sorted(on_disk - listed)}"
        )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_provenance_is_a_known_value(self, name):
        assert fx.provenance(name) in fx.PROVENANCE_VALUES

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_the_sample_count_matches_the_file(self, name):
        assert fx.index()["fixtures"][name]["samples"] == len(fx.load(name))

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_anything_not_recorded_explains_itself(self, name):
        entry = fx.index()["fixtures"][name]
        if entry["provenance"] == "recorded":
            assert entry["recorded_at"], (
                "a recording must say when it was taken, or it cannot be "
                "judged stale"
            )
            assert not entry["derivation"]
        else:
            assert len(entry["derivation"]) > 80, (
                f"{name} is {entry['provenance']}, so it must say what it "
                f"was derived from and why — a reader cannot otherwise "
                f"tell what the fixture is worth"
            )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_every_fixture_declares_its_endpoint(self, name):
        entry = fx.index()["fixtures"][name]
        assert entry["endpoint"].startswith("GET /"), (
            "a fixture with no endpoint cannot be re-recorded or verified"
        )

    def test_at_least_one_contract_is_backed_by_a_real_recording(self):
        assert fx.recorded_names(), (
            "with no recordings the whole suite is testing a mock against "
            "itself"
        )

    def test_the_review_contract_is_honestly_marked_unrecorded(self):
        """The recorded repository has no submitted reviews. Saying so is
        better than a fixture that looks like evidence and is not."""
        assert fx.provenance("reviews") == "hand_written"

    def test_no_fixture_names_a_person(self):
        """Committing real contributors' handles into a repository being
        open-sourced would be a gratuitous disclosure."""
        for name in fx.fixture_names():
            payloads = fx.load(name)
            for login in collect_github_logins(payloads):
                assert login.startswith("contributor") or login == "acme", (
                    f"{name} still names '{login}'"
                )

    def test_no_fixture_carries_an_avatar_or_an_email(self):
        for name in fx.fixture_names():
            assert unscrubbed_paths(fx.load(name), forbidden=set()) == [], (
                f"{name} still carries a dropped-by-policy key"
            )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_fixtures_are_committed_sorted_and_indented(self, name):
        """A canonical encoding, so a re-recording diffs against the old
        one field by field instead of as one changed line."""
        raw = (fx.FIXTURE_DIR / f"{name}.json").read_text()
        assert raw == json.dumps(
            json.loads(raw), indent=1, sort_keys=True,
        ) + "\n"


# ── The declared read set matches the normalizers ────────────────────


class TestTheContractCoversWhatIsRead:
    """A contract covering fields nothing reads tests nothing; a contract
    missing a field something reads lets a breaking change through."""

    @pytest.mark.parametrize(
        "paths,kind",
        [
            (GITHUB_PULL_REQUEST_FIELDS, "pull_request"),
            (GITHUB_ISSUE_FIELDS, "issue"),
            (GITHUB_EVENT_FIELDS, "event"),
            (GITHUB_REVIEW_FIELDS, "review"),
        ],
    )
    def test_declared_paths_are_registered(self, paths, kind):
        assert GITHUB_CONTRACT_PATHS[kind] == paths

    def test_the_fields_the_pull_request_normalizer_reads_are_declared(self):
        """Checked against the source rather than by eye: a future field
        read without being declared would silently escape the contract."""
        import ast
        import pathlib

        source = pathlib.Path(
            "src/lltpm/integrations/normalizers.py"
        ).read_text()
        tree = ast.parse(source)
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "normalize_github_pull_request"
        )
        # Three ways the normalizer reads a provider field: a subscript,
        # a .get(), and a field name passed to the timestamp helper.
        # Missing the third would leave every date outside the check.
        read = {
            node.slice.value
            for node in ast.walk(function)
            if isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        } | {
            node.args[0].value
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        } | {
            argument.value
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_first_timestamp"
            for argument in node.args[1:]
            if isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
        }
        assert {"node_id", "merged_at", "closed_at", "created_at"} <= read, (
            "the walker is not finding the fields it should; it has "
            "stopped checking anything"
        )
        declared = {path.split(".")[0].replace("[]", "")
                    for path in GITHUB_PULL_REQUEST_FIELDS}
        # Fields read for bookkeeping rather than from the provider.
        ignorable = {
            "repository", "repo", "full_name", "name", "ref", "login",
            "merged_by", "user", "pull_request",
        }
        undeclared = sorted(read - declared - ignorable)
        assert undeclared == [], (
            f"{undeclared} are read from the payload but are not in "
            f"GITHUB_PULL_REQUEST_FIELDS, so a provider change to them "
            f"would not be caught by the contract check"
        )
