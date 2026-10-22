"""Tests for the Slack contracts and the fixtures they come from.

Two failure modes bound this, as they do on the GitHub side: testing an
integration against the live API needs credentials and network and
breaks for reasons unrelated to the code, while testing it against a
hand-written mock passes forever as the real API moves. Fowler's
self-initializing fake resolves it — record once, replay offline,
re-check the recording on its own schedule.

Slack is the case where the first half of that was not available.
slack.com, api.slack.com and www.slack.com are unreachable from the
environment this integration was built in, so no live response could be
captured. Every fixture is therefore ``official_documentation``: a
shape reproduced from Slack's own published examples, which is worth
more than a hand-written mock and strictly less than a recording.

So the thing these tests mostly pin is that the weaker provenance is
*stated* everywhere it could matter — in the index, in the shipped
bundle, and in what ``tpllm integrations verify`` would report. A
fixture labelled ``recorded`` would claim a verification that never
happened, and that is the one failure the provenance vocabulary exists
to prevent. ``test_no_slack_fixture_claims_to_be_a_recording`` is the
load-bearing test in this file.
"""

from __future__ import annotations

import ast
import copy
import json
import pathlib

import pytest

from tpllm.integrations.contracts import (
    PROVENANCE_RANK,
    ContractEvidence,
    PayloadContract,
    build_contract_bundle,
    check_contract,
    contract_bundle_path,
    extract_contract,
    load_contract_bundle,
    resolve_path,
    serialize_contract_bundle,
)
from tpllm.integrations.slack_contracts import (
    SLACK_BUNDLE_NOTE,
    SLACK_CHANNEL_SCOPE_FIELDS,
    SLACK_CONTRACT_PATHS,
    SLACK_EVENT_FIELDS,
    SLACK_HISTORY_FIELDS,
    SLACK_MESSAGE_CHANGED_FIELDS,
    SLACK_MESSAGE_DELETED_FIELDS,
    SLACK_MESSAGE_FIELDS,
)
from tests import _slack_fixtures as fx

_NORMALIZER = pathlib.Path("src/tpllm/integrations/slack_normalizer.py")

#: Which fixture files feed which contract kind, so a test can extract a
#: contract for a kind without re-deriving the grouping.
_FILES_BY_KIND: dict[str, tuple[str, ...]] = {
    "message": ("messages", "declined_messages"),
    "message_changed": ("message_changed",),
    "message_deleted": ("message_deleted",),
    "channel_scope": ("channel_scope",),
    "event": ("events",),
    "conversations_history": ("conversations_history",),
}


def _contract(kind: str) -> PayloadContract:
    payloads: list[dict] = []
    for name in _FILES_BY_KIND[kind]:
        payloads.extend(fx.load(name))
    return extract_contract(
        source_system="slack", payload_kind=kind, payloads=payloads,
        paths=SLACK_CONTRACT_PATHS[kind],
    )


# ── The contract covers what the normalizers read ────────────────────


class TestTheContractCoversWhatIsRead:
    """A contract covering fields nothing reads tests nothing; a
    contract missing a field something reads lets a breaking change
    through."""

    @pytest.mark.parametrize(
        "paths,kind",
        [
            (SLACK_MESSAGE_FIELDS, "message"),
            (SLACK_MESSAGE_CHANGED_FIELDS, "message_changed"),
            (SLACK_MESSAGE_DELETED_FIELDS, "message_deleted"),
            (SLACK_CHANNEL_SCOPE_FIELDS, "channel_scope"),
            (SLACK_EVENT_FIELDS, "event"),
            (SLACK_HISTORY_FIELDS, "conversations_history"),
        ],
    )
    def test_declared_paths_are_registered(self, paths, kind):
        assert SLACK_CONTRACT_PATHS[kind] == paths

    def test_every_registered_kind_has_a_fixture(self):
        assert set(SLACK_CONTRACT_PATHS) == set(_FILES_BY_KIND)

    @pytest.mark.parametrize("kind", sorted(SLACK_CONTRACT_PATHS))
    def test_no_contract_is_vacuous(self, kind):
        """A contract with no typed fields would pass against
        anything."""
        contract = _contract(kind)
        untyped = [field.path for field in contract.fields if not field.types]
        assert untyped == [], (
            f"{kind}: {untyped} were never observed in any fixture, so the "
            f"contract does not actually constrain them"
        )

    def test_the_deprecated_verification_token_is_not_in_any_contract(self):
        """A credential has no business reaching a normalizer, so it is
        not read and therefore not declared. Declaring it would make a
        contract check demand that Slack keep sending it."""
        for paths in SLACK_CONTRACT_PATHS.values():
            assert not any("token" in path for path in paths)

    @pytest.mark.parametrize(
        "function,paths,ignorable",
        [
            (
                "normalize_slack_message", SLACK_MESSAGE_FIELDS,
                # Read for bookkeeping or from our own structures, not
                # from the provider payload.
                {
                    "team_id", "topic", "purpose", "user_profile",
                    "display_name", "real_name",
                },
            ),
            (
                "normalize_slack_message_changed",
                SLACK_MESSAGE_CHANGED_FIELDS,
                {"team_id", "user", "text", "ts", "thread_ts", "edited"},
            ),
            (
                "normalize_slack_message_deleted",
                SLACK_MESSAGE_DELETED_FIELDS,
                {"team_id", "user", "ts"},
            ),
            (
                "normalize_slack_channel_scope", SLACK_CHANNEL_SCOPE_FIELDS,
                {"team_id"},
            ),
        ],
    )
    def test_the_fields_a_normalizer_reads_are_declared(
        self, function, paths, ignorable,
    ):
        """Checked against the source rather than by eye: a future field
        read without being declared would silently escape the contract
        check, which is exactly the renamed-``merged_at`` scenario the
        GitHub side guards."""
        tree = ast.parse(_NORMALIZER.read_text())
        node = next(
            candidate for candidate in ast.walk(tree)
            if isinstance(candidate, ast.FunctionDef)
            and candidate.name == function
        )
        read = _payload_reads(node)
        assert read, "the walker found no payload reads; it checks nothing"
        declared = {
            path.split(".")[0].replace("[]", "") for path in paths
        } | {path.split(".")[-1] for path in paths}
        undeclared = sorted(read - declared - ignorable)
        assert undeclared == [], (
            f"{undeclared} are read in {function} but are not in its "
            f"declared contract paths, so a Slack change to them would not "
            f"be caught"
        )

    def test_the_walker_finds_the_reads_it_should(self):
        """Guards the guardrail: a walker that found nothing would make
        every assertion above vacuous."""
        tree = ast.parse(_NORMALIZER.read_text())
        node = next(
            candidate for candidate in ast.walk(tree)
            if isinstance(candidate, ast.FunctionDef)
            and candidate.name == "normalize_slack_message"
        )
        assert {"ts", "thread_ts", "text", "subtype", "edited"} <= (
            _payload_reads(node)
        )


def _payload_reads(node: ast.FunctionDef) -> set[str]:
    """Field names a function reads from a provider payload.

    Three ways it happens in this module: a subscript, a ``.get()``, and
    a key read from a nested dict that was itself pulled out first.
    Missing any of them would leave a whole shape outside the check.
    """
    return {
        child.slice.value
        for child in ast.walk(node)
        if isinstance(child, ast.Subscript)
        and isinstance(child.slice, ast.Constant)
        and isinstance(child.slice.value, str)
    } | {
        child.args[0].value
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "get"
        and child.args
        and isinstance(child.args[0], ast.Constant)
        and isinstance(child.args[0].value, str)
    }


# ── Contract checking against the documented shapes ──────────────────


class TestContractChecking:
    @pytest.mark.parametrize("kind", sorted(SLACK_CONTRACT_PATHS))
    def test_the_fixtures_satisfy_their_own_contract(self, kind):
        contract = _contract(kind)
        for name in _FILES_BY_KIND[kind]:
            for payload in fx.load(name):
                assert check_contract(
                    payload=payload, contract=contract,
                ) == [], f"{name} violates the {kind} contract"

    def test_a_removed_field_is_a_breach(self):
        contract = _contract("message")
        payload = copy.deepcopy(fx.thread_root())
        payload.pop("ts")
        breaches = check_contract(payload=payload, contract=contract)
        assert [b.path for b in breaches] == ["ts"]
        assert "a normalizer reads it" in breaches[0].explanation

    def test_a_renamed_field_is_a_breach(self):
        """The scenario this exists for: Slack renames ``ts`` and every
        reality time in the journal becomes a rejection."""
        contract = _contract("message")
        payload = copy.deepcopy(fx.thread_root())
        payload["timestamp"] = payload.pop("ts")
        breaches = check_contract(payload=payload, contract=contract)
        assert any(b.path == "ts" for b in breaches)

    def test_a_renamed_thread_field_is_a_breach_where_it_is_required(self):
        """Renaming ``thread_ts`` would silently turn every thread into
        a set of unrelated messages, which is the worst Slack-specific
        failure available. It is caught on ``message_changed``, where
        the field is always present."""
        contract = _contract("message_changed")
        payload = copy.deepcopy(fx.message_changed())
        payload["message"]["thread_timestamp"] = (
            payload["message"].pop("thread_ts")
        )
        breaches = check_contract(payload=payload, contract=contract)
        assert any(b.path == "message.thread_ts" for b in breaches)

    def test_an_optional_fields_rename_is_a_limitation_not_a_breach(self):
        """Stated rather than hidden. ``thread_ts`` is absent from a
        non-threaded history message, so the contract records it as
        optional and cannot distinguish "renamed" from "this message is
        not in a thread". Narrowing it would mean demanding a field
        Slack legitimately omits, which would fail the build on ordinary
        data — a worse trade than this gap."""
        contract = _contract("message")
        assert contract.field_for("thread_ts").always_present is False
        payload = copy.deepcopy(fx.thread_reply())
        payload["thread_timestamp"] = payload.pop("thread_ts")
        assert check_contract(payload=payload, contract=contract) == []

    def test_a_retyped_ts_is_a_breach(self):
        """If Slack ever returned a float ``ts`` the parser would still
        work and the microsecond suffix would quietly round, so the type
        is pinned."""
        contract = _contract("message")
        payload = copy.deepcopy(fx.thread_root())
        payload["ts"] = 1772010000.0001
        breaches = check_contract(payload=payload, contract=contract)
        assert [b.kind for b in breaches] == ["type_changed"]
        assert breaches[0].actual == "float"

    def test_a_nested_rename_is_a_breach(self):
        contract = _contract("message_changed")
        payload = copy.deepcopy(fx.message_changed())
        payload["message"]["edited"]["edited_at"] = (
            payload["message"]["edited"].pop("ts")
        )
        breaches = check_contract(payload=payload, contract=contract)
        assert any(b.path == "message.edited.ts" for b in breaches)

    def test_an_added_field_is_not_a_breach(self):
        """The whole reason to record what we consume rather than what
        the provider sends. Slack ships new message fields routinely."""
        contract = _contract("message")
        payload = copy.deepcopy(fx.thread_root())
        payload["blocks"] = [{"type": "rich_text", "elements": []}]
        payload["something_new_in_2027"] = {"anything": [1, 2]}
        assert check_contract(payload=payload, contract=contract) == []

    def test_an_optional_field_may_be_absent(self):
        """``channel`` is genuinely absent from a polled history item,
        and the contract must not demand it."""
        contract = _contract("message")
        assert contract.field_for("channel").always_present is False
        assert check_contract(
            payload=fx.thread_root(), contract=contract,
        ) == []

    def test_the_cursor_path_resolves_in_the_documented_envelope(self):
        """The reader's whole pagination story rests on this one path."""
        assert resolve_path(
            fx.history_response(), "response_metadata.next_cursor",
        ) == [fx.history_response()["response_metadata"]["next_cursor"]]


# ── Fixture provenance: what these files are and are not ─────────────


class TestFixtureProvenance:
    """``tpllm runtime coverage`` refuses to let a derived projection
    pass for an engine. The same honesty applies to fixtures."""

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
        assert fx.provenance(name) in PROVENANCE_RANK, (
            "the fixture index and the contract vocabulary must agree, or "
            "a bundle silently ranks an unknown provenance last"
        )

    def test_no_slack_fixture_claims_to_be_a_recording(self):
        """The load-bearing test in this file. slack.com was unreachable
        from the environment this integration was built in, so there was
        nothing to record. Labelling one ``recorded`` would claim a live
        check that never happened — the single failure this vocabulary
        exists to prevent."""
        assert fx.recorded_names() == []
        assert sorted(fx.documented_names()) == fx.fixture_names()

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_each_fixture_explains_what_it_is(self, name):
        entry = fx.index()["fixtures"][name]
        assert entry["provenance"] != "recorded"
        assert not entry["recorded_at"], (
            "a recorded_at on something that is not a recording would "
            "make it look judgeable for staleness"
        )
        assert len(entry["derivation"]) > 80, (
            f"{name} is {entry['provenance']}, so it must say what it was "
            f"reproduced from and why — a reader cannot otherwise tell "
            f"what the fixture is worth"
        )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_each_derivation_says_it_is_not_a_live_recording(self, name):
        """Not left to the provenance field alone: whoever reads the
        file needs the caveat beside the data."""
        derivation = fx.index()["fixtures"][name]["derivation"]
        assert "NOT a recording" in derivation
        assert "unreachable" in derivation
        assert "no evidence that the live API still matches" in derivation

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_each_derivation_declares_what_was_substituted(self, name):
        """Synthetic ids and February timestamps are modifications, and
        a fixture that called itself documentation-faithful without
        saying so would overstate itself."""
        derivation = fx.index()["fixtures"][name]["derivation"]
        assert "synthetic" in derivation
        assert "token" in derivation

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_the_sample_count_matches_the_file(self, name):
        assert fx.index()["fixtures"][name]["samples"] == len(fx.load(name))

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_each_fixture_declares_its_endpoint(self, name):
        entry = fx.index()["fixtures"][name]
        assert entry["endpoint"].startswith("GET /"), (
            "a fixture with no endpoint cannot be re-recorded or verified"
        )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_each_fixture_declares_its_source_system(self, name):
        assert fx.index()["fixtures"][name]["source_system"] == "slack"

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_each_payload_kind_is_one_the_contracts_cover(self, name):
        kind = fx.index()["fixtures"][name]["payload_kind"]
        assert kind in SLACK_CONTRACT_PATHS, (
            f"{name} declares kind '{kind}', which no contract covers, so "
            f"build_contract_bundle would silently skip the file"
        )

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_no_fixture_names_anybody_real(self, name):
        """Committing a real workspace, channel or person into a
        repository being open-sourced would be a gratuitous
        disclosure — and here there is no excuse, because nothing was
        recorded in the first place."""
        found = fx.slack_ids_in(fx.load(name))
        unexpected = sorted(found - fx.SYNTHETIC_IDS)
        assert unexpected == [], f"{name} names {unexpected}"

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_no_fixture_carries_a_credential_shaped_value(self, name):
        from tpllm.integrations.envelopes import redact_secret_text

        raw = (fx.FIXTURE_DIR / f"{name}.json").read_text()
        _, found = redact_secret_text(raw)
        assert not found, (
            f"{name} contains something the credential redactor "
            f"recognizes; a committed fixture carries no secret-shaped "
            f"value even a fake one"
        )
        assert '"token"' not in raw

    @pytest.mark.parametrize("name", fx.fixture_names())
    def test_fixtures_are_committed_sorted_and_indented(self, name):
        """A canonical encoding, so a future re-recording diffs against
        this one field by field instead of as one changed line."""
        raw = (fx.FIXTURE_DIR / f"{name}.json").read_text()
        assert raw == json.dumps(
            json.loads(raw), indent=1, sort_keys=True,
        ) + "\n"

    def test_the_index_is_committed_sorted_and_indented(self):
        raw = (fx.FIXTURE_DIR / "index.json").read_text()
        assert raw == json.dumps(
            json.loads(raw), indent=1, sort_keys=True,
        ) + "\n"

    def test_the_index_note_states_the_limitation_once_for_the_set(self):
        note = fx.index()["note"]
        assert "unreachable" in note
        assert "not a recording" in note
        assert "unverifiable" in note


# ── The shipped bundle ───────────────────────────────────────────────


class TestTheShippedBundleMatchesTheFixtures:
    """The bundle ships in the package because ``verify`` has to work
    from an installed wheel. That makes drift possible, so it is
    checked."""

    def test_the_bundle_is_exactly_what_the_fixtures_produce(self):
        rebuilt = build_contract_bundle(
            source_system="slack", sources=fx.contract_sources(),
            paths_by_kind=SLACK_CONTRACT_PATHS, note=SLACK_BUNDLE_NOTE,
        )
        assert serialize_contract_bundle(rebuilt) == (
            contract_bundle_path("slack").read_text()
        ), (
            "the shipped contract no longer matches the fixtures it claims "
            "to come from; regenerate it alongside them"
        )

    def test_the_bundle_is_shipped_inside_the_package(self):
        path = contract_bundle_path("slack")
        assert path.exists()
        assert "src/tpllm/integrations" in str(path).replace("\\", "/"), (
            "a contract that only exists in a git checkout cannot be "
            "checked by the person running the installed tool"
        )

    def test_the_bundle_covers_every_payload_kind(self):
        bundle = load_contract_bundle("slack")
        assert bundle is not None
        assert sorted(bundle.contracts) == sorted(SLACK_CONTRACT_PATHS)

    def test_the_bundle_claims_no_live_verifiability(self):
        """What a verify run will say, and it should say it for every
        kind: documented, therefore not re-checkable against anything."""
        bundle = load_contract_bundle("slack")
        assert bundle.verifiable_kinds == []
        # Official-documentation contracts: nothing a live
        # check can confirm, but not backed by nothing either.
        assert bundle.not_live_verifiable_kinds == sorted(SLACK_CONTRACT_PATHS)
        assert bundle.official_kinds == sorted(SLACK_CONTRACT_PATHS)
        assert bundle.unverifiable_kinds == []
        assert bundle.official_kinds == sorted(SLACK_CONTRACT_PATHS)

    @pytest.mark.parametrize("kind", sorted(SLACK_CONTRACT_PATHS))
    def test_each_kind_reports_the_middle_tier_of_evidence(self, kind):
        """Three answers, not two: a recording can be re-checked,
        documentation is evidence the shape is the documented one, and
        anything derived is evidence about neither."""
        bundle = load_contract_bundle("slack")
        assert bundle.evidence_for(kind) is ContractEvidence.OFFICIAL
        assert not bundle.is_evidence(kind)

    def test_the_bundle_note_states_the_limitation(self):
        bundle = load_contract_bundle("slack")
        assert bundle.note == SLACK_BUNDLE_NOTE
        assert "not a recording" in bundle.note
        assert "unverifiable" in bundle.note

    def test_the_bundle_records_no_recording_time(self):
        bundle = load_contract_bundle("slack")
        assert all(not value for value in bundle.recorded_at.values())

    def test_documentation_outranks_hand_written_and_loses_to_recorded(self):
        """The tier this integration sits in, asserted so a future
        re-labelling has to be deliberate."""
        assert PROVENANCE_RANK["recorded"] < (
            PROVENANCE_RANK["official_documentation"]
        ) < PROVENANCE_RANK["derived"] < PROVENANCE_RANK["hand_written"]

    def test_a_real_recording_would_supersede_these_contracts(self):
        """The migration path, proven rather than promised: dropping a
        recording in beside a documented fixture upgrades the kind
        without touching the code that builds the bundle."""
        from tpllm.integrations.contracts import ContractSource

        sources = fx.contract_sources() + [ContractSource(
            payload_kind="message", payloads=[fx.thread_root()],
            provenance="recorded", recorded_at="2027-01-01T00:00:00Z",
        )]
        upgraded = build_contract_bundle(
            source_system="slack", sources=sources,
            paths_by_kind=SLACK_CONTRACT_PATHS, note=SLACK_BUNDLE_NOTE,
        )
        assert upgraded.verifiable_kinds == ["message"]
        assert upgraded.recorded_at["message"] == "2027-01-01T00:00:00Z"
