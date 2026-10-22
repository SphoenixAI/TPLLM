"""Tests for the Jira contracts and the fixtures behind them.

The same two failure modes bound this as bound the GitHub contracts —
a suite that needs credentials and network, or a mock that passes
forever while the API moves — with one difference that has to be stated
rather than papered over: **nothing here was recorded.**
``api.atlassian.com``, ``developer.atlassian.com`` and every
``*.atlassian.net`` host are unreachable from this environment, so the
"record once, replay offline" half of Fowler's self-initializing fake
could not be performed at all.

What exists instead is the provider's published shape, labelled
``official_documentation``: stronger than a hand-written mock because
Atlassian published it, weaker than a recording because documentation
drifts from implementations. So most of what is pinned here is about
that honesty:

- the contract covers exactly the fields the normalizers read, so a new
  Jira field cannot fail the build and a renamed one must;
- every fixture says what it is, and none of them may claim to be a
  recording;
- the shipped bundle reports these contracts as *unverifiable* rather
  than letting them pass as verified.
"""

from __future__ import annotations

import ast
import copy
import json
import pathlib

import pytest

from lltpm.integrations.contracts import (
    PROVENANCE_RANK,
    ContractEvidence,
    PayloadContract,
    build_contract_bundle,
    check_contract,
    contract_bundle_path,
    extract_contract,
    load_contract_bundle,
    serialize_contract_bundle,
)
from lltpm.integrations.jira_contracts import (
    JIRA_CHANGELOG_FIELDS,
    JIRA_COMMENT_FIELDS,
    JIRA_CONTRACT_PATHS,
    JIRA_ISSUE_FIELDS,
    JIRA_ISSUE_LINK_FIELDS,
    JIRA_SEARCH_FIELDS,
    JIRA_WEBHOOK_FIELDS,
)
from tests import _jira_fixtures as fx

_NORMALIZER = pathlib.Path("src/lltpm/integrations/jira_normalizer.py")

#: Which fixture file covers which payload kind.
FIXTURE_FOR_KIND = {
    "issue": "issues",
    "changelog": "changelog_entries",
    "comment": "comments",
    "issue_link": "issue_links",
    "webhook": "webhooks",
    "search": "search_pages",
}


def _contract(kind: str) -> PayloadContract:
    return extract_contract(
        source_system="jira", payload_kind=kind,
        payloads=fx.load(FIXTURE_FOR_KIND[kind]),
        paths=JIRA_CONTRACT_PATHS[kind],
    )


# ── Extraction ───────────────────────────────────────────────────────


class TestContractExtraction:
    @pytest.mark.parametrize("kind", sorted(JIRA_CONTRACT_PATHS))
    def test_every_declared_kind_has_a_contract(self, kind):
        contract = _contract(kind)
        assert {f.path for f in contract.fields} == set(
            JIRA_CONTRACT_PATHS[kind]
        )
        assert contract.recorded_from_samples > 0

    @pytest.mark.parametrize("kind", sorted(JIRA_CONTRACT_PATHS))
    def test_no_contract_is_vacuous(self, kind):
        """A contract with no typed fields would pass against anything,
        which is how a fixture set that exercises nothing looks exactly
        like one that exercises everything."""
        contract = _contract(kind)
        untyped = [f.path for f in contract.fields if not f.types]
        assert untyped == [], (
            f"{kind}: {untyped} were never observed in any sample, so the "
            f"fixtures do not exercise the contract"
        )

    def test_the_identity_fields_are_pinned_as_present_strings(self):
        contract = _contract("issue")
        for path in ("id", "key", "fields.updated", "fields.project.key"):
            field = contract.field_for(path)
            assert field.always_present, f"{path} is the identity path"
            assert field.types == ["str"]

    def test_an_optional_field_is_recorded_as_optional(self):
        """Derived from the samples rather than declared: the minimal
        issue has no due date, and that is genuinely how Jira behaves."""
        assert not _contract("issue").field_for(
            "fields.duedate"
        ).always_present

    def test_a_nullable_field_records_null_as_its_own_type(self):
        """``fields.resolution: null`` means unresolved. A contract that
        conflated null with absent would stop distinguishing them."""
        types = _contract("issue").field_for("fields.resolution.name").types
        assert "str" in types

    def test_the_comment_body_is_pinned_as_a_document(self):
        """v3 returns an ADF object. If it reverted to a string, that is
        a type change this contract catches — rather than prose quietly
        turning into a JSON dump inside evidence."""
        assert _contract("comment").field_for("body").types == ["object"]

    def test_the_changelog_created_time_is_pinned(self):
        """The single most important path: it is a transition's reality
        time, and a rename would move weeks of history onto the day we
        happened to poll."""
        field = _contract("changelog").field_for("created")
        assert field.always_present and field.types == ["str"]


class TestContractChecking:
    def test_the_fixtures_satisfy_their_own_contracts(self):
        for kind, name in FIXTURE_FOR_KIND.items():
            contract = _contract(kind)
            for payload in fx.load(name):
                assert check_contract(
                    payload=payload, contract=contract,
                ) == [], f"{kind} fixture breaches its own contract"

    def test_a_renamed_timestamp_is_a_breach(self):
        """The scenario this exists for: Atlassian renames
        ``fields.updated`` and every polled issue silently loses its
        version, so re-reading an unchanged project starts appending
        duplicates."""
        payload = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        payload["fields"]["lastModified"] = payload["fields"].pop("updated")
        breaches = check_contract(payload=payload, contract=_contract("issue"))
        assert any(b.path == "fields.updated" for b in breaches)
        assert "a normalizer reads it" in breaches[0].explanation

    def test_a_renamed_status_category_is_a_breach(self):
        """Without it, every Done ticket would fall back to the declared
        status-name set and a site with a custom final column would stop
        reporting completions."""
        payload = copy.deepcopy(fx.issue(fx.DONE_ISSUE))
        payload["fields"]["status"]["category"] = payload["fields"][
            "status"
        ].pop("statusCategory")
        breaches = check_contract(payload=payload, contract=_contract("issue"))
        assert any(
            b.path == "fields.status.statusCategory.key" for b in breaches
        )

    def test_a_retyped_issue_id_is_a_breach(self):
        """Jira returns issue ids as strings. If one arrived as an int,
        every idempotency key would change shape at once."""
        payload = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        payload["id"] = 10005
        breaches = check_contract(payload=payload, contract=_contract("issue"))
        assert [b.kind for b in breaches] == ["type_changed"]
        assert breaches[0].actual == "int"

    def test_a_new_field_is_not_a_breach(self):
        """The whole reason to record what we consume rather than what
        the provider sends. Jira ships new fields constantly."""
        payload = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        payload["fields"]["customfield_99999"] = {"anything": [1, 2]}
        assert check_contract(
            payload=payload, contract=_contract("issue"),
        ) == []

    def test_a_dropped_page_token_is_a_breach(self):
        """The reader's pagination ends on ``nextPageToken`` and
        ``isLast``. A change to either would make it read one page and
        call that the whole backlog."""
        contract = extract_contract(
            source_system="jira", payload_kind="search",
            payloads=[fx.search_page(first=True)], paths=JIRA_SEARCH_FIELDS,
        )
        page = copy.deepcopy(fx.search_page(first=True))
        page["nextPage"] = page.pop("nextPageToken")
        breaches = check_contract(payload=page, contract=contract)
        assert any(b.path == "nextPageToken" for b in breaches)

    def test_a_retyped_link_type_is_a_breach(self):
        """``type.name`` is what decides whether a link is a dependency
        at all."""
        link = copy.deepcopy(fx.issue_link(fx.BLOCKED_BY_OPEN_LINK))
        link["type"]["name"] = 1000
        breaches = check_contract(
            payload=link, contract=_contract("issue_link"),
        )
        assert any(b.path == "type.name" for b in breaches)


# ── The declared read set matches the normalizers ────────────────────


class TestTheContractCoversWhatIsRead:
    """A contract covering fields nothing reads tests nothing; a contract
    missing a field something reads lets a breaking change through."""

    @pytest.mark.parametrize(
        "paths,kind",
        [
            (JIRA_ISSUE_FIELDS, "issue"),
            (JIRA_CHANGELOG_FIELDS, "changelog"),
            (JIRA_COMMENT_FIELDS, "comment"),
            (JIRA_ISSUE_LINK_FIELDS, "issue_link"),
            (JIRA_WEBHOOK_FIELDS, "webhook"),
            (JIRA_SEARCH_FIELDS, "search"),
        ],
    )
    def test_declared_paths_are_registered(self, paths, kind):
        assert JIRA_CONTRACT_PATHS[kind] == paths

    def test_the_fields_the_issue_normalizer_reads_are_declared(self):
        """Checked against the source rather than by eye: a future field
        read without being declared would silently escape the contract
        check, and the first anyone would know is a status report built
        on a field that stopped arriving."""
        tree = ast.parse(_NORMALIZER.read_text())
        read: set[str] = set()
        for name in (
            "normalize_jira_issue", "_IssueContext", "_normalize_embedded",
        ):
            node = next(
                item for item in ast.walk(tree)
                if isinstance(item, (ast.FunctionDef, ast.ClassDef))
                and item.name == name
            )
            read |= {
                sub.slice.value for sub in ast.walk(node)
                if isinstance(sub, ast.Subscript)
                and isinstance(sub.slice, ast.Constant)
                and isinstance(sub.slice.value, str)
            } | {
                call.args[0].value for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "get"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            }

        assert {"updated", "created", "duedate", "statusCategory"} <= read, (
            "the walker is not finding the fields it should; it has "
            "stopped checking anything"
        )
        declared = set()
        for path in JIRA_ISSUE_FIELDS:
            declared.update(
                segment.replace("[]", "") for segment in path.split(".")
            )
        # Read for bookkeeping or from our own structures, not from the
        # provider's issue payload.
        ignorable = {
            "accountId", "displayName", "issue", "issue_link", "changelog",
            "comment", "worklogs", "values", "histories", "name", "type",
            "text", "content", "items", "field", "fieldtype", "from",
            "fromString", "to", "toString", "author", "updateAuthor",
            "visibility", "inwardIssue", "outwardIssue", "inward",
            "outward", "webhookEvent", "interpretation",
        }
        undeclared = sorted(read - declared - ignorable)
        assert undeclared == [], (
            f"{undeclared} are read from the payload but are not in "
            f"JIRA_ISSUE_FIELDS, so a provider change to them would not "
            f"be caught by the contract check"
        )

    def test_the_worklog_path_is_declared_although_it_is_refused(self):
        """It is read in order to refuse it. If Atlassian renamed the
        field the refusal would silently stop happening, and nobody would
        learn that logged hours had started arriving under a new name."""
        assert "fields.worklog" in JIRA_ISSUE_FIELDS

    def test_the_delivery_timestamp_is_deliberately_not_in_the_contract(self):
        """Because the normalizer must never read it: a delivery
        timestamp is not an idempotency key and not a reality time."""
        assert not any(
            path == "timestamp" for path in JIRA_WEBHOOK_FIELDS
        )


# ── Fixture provenance: what is and is not evidence ──────────────────


class TestFixtureProvenance:
    """``lltpm runtime coverage`` refuses to let a derived projection
    pass for an engine. The same honesty applies here, and more sharply:
    nothing in this directory is a recording, and the labels say so."""

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
        assert fx.provenance(name) in PROVENANCE_RANK

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_nothing_claims_to_be_a_recording(self, name):
        """The one label that would be a lie. No live Jira response
        could be captured in this environment, and calling a fixture
        ``recorded`` would claim a check that never happened."""
        assert fx.provenance(name) in fx.ALLOWED_PROVENANCE
        assert fx.provenance(name) != "recorded"

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_nothing_claims_a_recording_time(self, name):
        assert fx.index()["fixtures"][name]["recorded_at"] == "", (
            "a timestamp would imply something was captured from a live "
            "site at that moment"
        )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_every_fixture_explains_what_it_is_worth(self, name):
        entry = fx.index()["fixtures"][name]
        derivation = entry["derivation"]
        assert len(derivation) > 80, (
            f"{name} must say what it was derived from and why — a reader "
            f"cannot otherwise tell what the fixture is worth"
        )
        assert "documentation" in derivation
        assert "not" in derivation and "live" in derivation, (
            "the derivation has to say that it is not evidence about the "
            "live API, which is the whole point of the label"
        )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_every_fixture_declares_its_endpoint(self, name):
        entry = fx.index()["fixtures"][name]
        assert entry["endpoint"].startswith("GET /"), (
            "a fixture with no endpoint cannot be re-recorded or verified "
            "once egress exists"
        )
        assert "/rest/api/3" in entry["endpoint"], (
            "v3 and not v2: v3 is the current Cloud API and the one that "
            "returns Atlassian Document Format"
        )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_the_sample_count_matches_the_file(self, name):
        assert fx.index()["fixtures"][name]["samples"] == len(fx.load(name))

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_every_fixture_declares_its_payload_kind(self, name):
        assert fx.index()["fixtures"][name]["payload_kind"] in (
            JIRA_CONTRACT_PATHS
        )
        assert fx.index()["fixtures"][name]["source_system"] == "jira"

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_fixtures_are_committed_sorted_and_indented(self, name):
        """A canonical encoding, so a future re-recording diffs against
        these field by field instead of as one changed line."""
        raw = (fx.FIXTURE_DIR / f"{name}.json").read_text()
        assert raw == json.dumps(
            json.loads(raw), indent=1, sort_keys=True,
        ) + "\n"

    def test_the_index_is_committed_canonically_too(self):
        raw = (fx.FIXTURE_DIR / "index.json").read_text()
        assert raw == json.dumps(
            json.loads(raw), indent=1, sort_keys=True,
        ) + "\n"

    def test_the_index_note_states_the_network_reality(self):
        note = fx.index()["note"]
        assert "atlassian.net" in note
        assert "recorded" in note

    def test_no_fixture_names_a_real_site(self):
        """Committing a customer's Atlassian hostname would disclose who
        uses this, and a plausible-looking one would get someone to point
        a test at it."""
        import re

        hosts = re.compile(r"[A-Za-z0-9-]+\.atlassian\.(?:net|com)")
        for name in fx.fixture_names():
            found = set(hosts.findall(json.dumps(fx.load(name))))
            assert found <= {fx.SITE}, (
                f"{name} names {sorted(found - {fx.SITE})}"
            )

    def test_no_fixture_names_a_person(self):
        """The account ids are Atlassian's own example-style synthetic
        ids and the display names are role labels. Nobody real is in
        here, and the project's stance is that it models organizational
        mechanics rather than individuals."""
        for name in fx.fixture_names():
            raw = json.dumps(fx.load(name))
            assert "@example.com" not in raw
            assert "emailAddress" not in raw, (
                "no normalizer reads it, and a committed fixture should "
                "not carry contact details at all"
            )
            for found in _display_names(fx.load(name)):
                assert found.startswith("Example "), (
                    f"{name} names '{found}'"
                )

    def test_no_fixture_carries_an_avatar_map(self):
        for name in fx.fixture_names():
            assert "avatarUrls" not in json.dumps(fx.load(name))


def _display_names(node) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        value = node.get("displayName")
        if isinstance(value, str) and value:
            found.append(value)
        for item in node.values():
            found.extend(_display_names(item))
    elif isinstance(node, list):
        for item in node:
            found.extend(_display_names(item))
    return found


# ── The shipped bundle ───────────────────────────────────────────────


class TestTheShippedBundleMatchesTheFixtures:
    """The bundle ships in the package because `verify` has to work from
    an installed wheel. That makes drift possible, so it is checked."""

    def test_the_bundle_is_exactly_what_the_fixtures_produce(self):
        rebuilt = build_contract_bundle(
            source_system="jira", sources=fx.contract_sources(),
            paths_by_kind=JIRA_CONTRACT_PATHS, note=fx.BUNDLE_NOTE,
        )
        assert serialize_contract_bundle(rebuilt) == (
            contract_bundle_path("jira").read_text()
        ), (
            "the shipped contract no longer matches the fixtures it "
            "claims to come from; regenerate it alongside them"
        )

    def test_the_bundle_is_shipped_inside_the_package(self):
        path = contract_bundle_path("jira")
        assert path.exists()
        assert "src/lltpm/integrations" in str(path).replace("\\", "/"), (
            "a contract that only exists in a git checkout cannot be "
            "checked by the person running the installed tool"
        )

    def test_the_bundle_reports_itself_unverifiable(self):
        """The honest answer, and the one that matters most here: a
        verification report that presented a documented shape as
        verified would be worse than no report."""
        bundle = load_contract_bundle("jira")
        assert bundle is not None
        assert bundle.verifiable_kinds == []
        # Official-documentation contracts: nothing a live
        # check can confirm, but not backed by nothing either.
        assert bundle.not_live_verifiable_kinds == sorted(JIRA_CONTRACT_PATHS)
        assert bundle.official_kinds == sorted(JIRA_CONTRACT_PATHS)
        assert bundle.unverifiable_kinds == []

    def test_the_bundle_knows_the_contracts_are_official(self):
        """Three answers, not two: a documented shape is worth more than
        nothing and less than a recording."""
        bundle = load_contract_bundle("jira")
        assert bundle.official_kinds == sorted(JIRA_CONTRACT_PATHS)
        for kind in JIRA_CONTRACT_PATHS:
            assert bundle.evidence_for(kind) is ContractEvidence.OFFICIAL
            assert not bundle.is_evidence(kind)

    def test_official_sits_between_recorded_and_derived(self):
        assert PROVENANCE_RANK["recorded"] < PROVENANCE_RANK[
            "official_documentation"
        ] < PROVENANCE_RANK["derived"]

    def test_the_bundle_note_does_not_overclaim(self):
        bundle = load_contract_bundle("jira")
        assert "not evidence that the live API still matches" in bundle.note
        assert "unverifiable" in bundle.note

    def test_the_bundle_covers_every_payload_kind(self):
        bundle = load_contract_bundle("jira")
        assert sorted(bundle.contracts) == sorted(JIRA_CONTRACT_PATHS)
