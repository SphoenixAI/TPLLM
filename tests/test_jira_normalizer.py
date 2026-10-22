"""Tests for the Jira Cloud anti-corruption layer.

These run against payloads shaped the way Atlassian publishes them for
REST v3 (``tests/fixtures/jira``, provenance
``official_documentation`` — see ``_jira_fixtures``), so what they pin
is behaviour on the documented shapes rather than on a mock written to
agree with the code.

Five properties matter more than the individual mappings, and each one
exists because the alternative is a specific kind of false statement a
TPM would then repeat to a room full of people:

- **A ticket in Done is not a feature that works.** The single most
  tempting lie in any ticket integration. A board column maps to
  ``ticket_completed`` and the refresh-scope policy keeps that class
  away from launch readiness, so no drag-and-drop can declare a launch
  ready. Asserted here against the real policy, not against a list of
  strings.
- **History comes from the changelog, with its own time.** A transition
  recorded six weeks ago occurred six weeks ago. A snapshot never
  implies a transition, because a snapshot has no idea when the ticket
  moved.
- **A key never moves with a clock — ours or Atlassian's.** Identity is
  the immutable issue id plus a provider version, so re-reading an
  unchanged project appends nothing and a project rename does not turn
  one ticket into two.
- **Nothing is guessed.** An unbound project, an unparseable timestamp,
  a custom field, a worklog and a ``relates to`` link each produce a
  report with a reason, never a plausible-looking envelope.
- **Nothing leaks.** Not a token pasted into a ticket description, and
  not anything that could be used to count or rate a person's work.
"""

from __future__ import annotations

import ast
import copy
import pathlib
from datetime import datetime

import pytest

from tpllm.integrations.jira_normalizer import (
    BLOCKING_LINK_TYPE_NAMES,
    DEFAULT_DONE_STATUS_NAMES,
    JIRA,
    JIRA_EMITTED_EVENT_CLASSES,
    adf_to_text,
    jira_changelog_key,
    jira_comment_key,
    jira_issue_key,
    jira_project_bindings,
    jira_scope,
    normalize_jira_changelog_entry,
    normalize_jira_comment,
    normalize_jira_issue,
    normalize_jira_issue_link,
    normalize_jira_payloads,
    normalize_jira_webhook,
    normalize_payload,
    site_from_self_url,
)
from tpllm.integrations.normalizers import (
    CONTENT_EXCERPT_LIMIT,
    RECOGNIZED_EVENT_CLASSES,
    NormalizationOutcome,
)
from tpllm.integrations.runtime import RefreshScope, determine_refresh_scope
from tests import _jira_fixtures as fx

RECEIVED = datetime(2026, 10, 22, 4, 40)
LATER = datetime(2026, 10, 25, 9, 15)

_SOURCE = pathlib.Path("src/tpllm/integrations/jira_normalizer.py")


@pytest.fixture
def bindings():
    return fx.bindings()


def _only(outcome: NormalizationOutcome):
    assert len(outcome.envelopes) == 1, (
        f"expected one envelope, got {len(outcome.envelopes)}; "
        f"skipped={outcome.skipped_count}"
    )
    return outcome.envelopes[0]


def _snapshot(issue, *, bindings, received_at=RECEIVED):
    """The snapshot envelope alone, with embedded payloads left out."""
    return _only(normalize_jira_issue(
        issue, bindings=bindings, received_at=received_at,
        include_embedded=False,
    ))


# ── Binding: a project is declared, never guessed ────────────────────


class TestBinding:
    def test_the_scope_is_site_and_project_key(self):
        """A project key is only unique within one Atlassian tenant, so
        two sites both having a PLATFORM project is ordinary. Binding on
        the bare key would file one tenant's evidence under the other's
        project."""
        assert jira_scope(site="acme.atlassian.net", project_key="ABC") == (
            "acme.atlassian.net/ABC"
        )

    def test_the_typed_constructor_builds_the_same_scope(self, bindings):
        assert bindings.projects_for(
            source_system=JIRA, external_scope="acme.atlassian.net/ABC",
        ) == [fx.PROJECT]

    def test_the_site_matches_case_insensitively(self, bindings):
        """Atlassian hostnames are case-insensitive, and a near-match is
        not the same thing as a different site."""
        assert bindings.projects_for(
            source_system=JIRA, external_scope="ACME.atlassian.net/ABC",
        ) == [fx.PROJECT]

    def test_an_unbound_project_is_reported(self, bindings):
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["fields"]["project"]["key"] = "OTHER"
        issue["key"] = "OTHER-1"
        outcome = normalize_jira_issue(
            issue, bindings=bindings, received_at=RECEIVED,
        )
        assert not outcome.envelopes
        assert len(outcome.unbound) == 1
        assert outcome.needs_attention
        assert "declaration, not a guess" in outcome.unbound[0].reason

    def test_a_project_name_never_resolves(self):
        """The project's display name is in every payload and is exactly
        what a fuzzy matcher would reach for. 'Example Platform' and
        'Example Platform (legacy)' are not the same project and no
        normalizer can know which was meant."""
        name = fx.issue(fx.IN_PROGRESS_ISSUE)["fields"]["project"]["name"]
        bound = jira_project_bindings(mapping={(fx.SITE, name): ["x"]})
        outcome = normalize_jira_issue(
            fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bound,
            received_at=RECEIVED,
        )
        assert not outcome.envelopes and outcome.unbound

    def test_a_payload_with_no_site_is_rejected(self, bindings):
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue.pop("self")
        outcome = normalize_jira_issue(
            issue, bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.rejected and "site" in outcome.rejected[0].reason

    def test_the_site_can_be_recovered_from_the_self_url(self):
        """There is no site or cloud-id field on an issue; the self URL is
        the only place a payload states which tenant it came from."""
        assert site_from_self_url(
            fx.issue(fx.IN_PROGRESS_ISSUE)["self"]
        ) == fx.SITE

    def test_an_explicit_site_wins_over_the_self_url(self, bindings):
        """A caller that knows which site it read from says so; the URL
        is only the fallback. A reverse proxy or a site rename can leave
        a stale host in ``self``, and the caller's own knowledge of which
        tenant it authenticated to is better evidence than a string in
        the payload."""
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["self"] = "https://stale.atlassian.net/rest/api/3/issue/10005"
        envelope = _only(normalize_jira_issue(
            issue, bindings=bindings, received_at=RECEIVED,
            site=fx.SITE, include_embedded=False,
        ))
        assert envelope.metadata["jira_site"] == fx.SITE

    def test_an_unknown_site_is_unbound_even_with_a_known_key(
        self, bindings,
    ):
        """Project ABC on another tenant is another organization's work."""
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["self"] = "https://other.atlassian.net/rest/api/3/issue/10005"
        outcome = normalize_jira_issue(
            issue, bindings=bindings, received_at=RECEIVED,
            include_embedded=False,
        )
        assert not outcome.envelopes and outcome.unbound


# ── Atlassian Document Format ────────────────────────────────────────


class TestAtlassianDocumentFormat:
    def test_a_v3_document_becomes_its_text(self):
        """v3 returns descriptions as nested JSON. ``str(value)`` would
        put a JSON dump into evidence content, which a reader would then
        quote in a status report."""
        body = fx.comment(fx.PLAIN_COMMENT)["body"]
        assert adf_to_text(body).startswith("Approved from my side")
        assert "{" not in adf_to_text(body)

    def test_a_plain_string_passes_through(self):
        """The v2 shape, and the shape of some v3 fields. Crashing on it
        would make one legacy field take out a whole ingest pass."""
        assert adf_to_text("just text") == "just text"

    def test_blocks_are_separated(self):
        document = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "first"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "second"}]},
            ],
        }
        assert adf_to_text(document) == "first\n\nsecond"

    def test_an_unknown_node_is_descended_into(self):
        """ADF grows node types. Losing a paragraph because it sat inside
        one we had not heard of is worse than rendering it plainly."""
        document = {"type": "doc", "content": [
            {"type": "someFutureBlock", "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "still readable"}]}]}]}
        assert adf_to_text(document) == "still readable"

    def test_a_mention_contributes_no_identity(self):
        """A mention's attrs carry an account id and a rendered display
        name. A display name is not identity here, and the rendered form
        is chrome rather than prose."""
        document = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "ping "},
                {"type": "mention", "attrs": {
                    "id": "5b10a2844c20165700ede21g",
                    "text": "@Example Assignee"}},
            ]}]}
        rendered = adf_to_text(document)
        assert rendered == "ping"
        assert "Example Assignee" not in rendered

    def test_a_hard_break_is_a_newline(self):
        document = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "a"},
                {"type": "hardBreak"},
                {"type": "text", "text": "b"},
            ]}]}
        assert adf_to_text(document) == "a\nb"

    @pytest.mark.parametrize("value", [None, {}, [], 7, {"type": "doc"}])
    def test_nothing_crashes_on_a_degenerate_document(self, value):
        assert isinstance(adf_to_text(value), str)

    def test_a_long_description_is_truncated_and_says_so(self, bindings):
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["fields"]["description"] = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "x" * (CONTENT_EXCERPT_LIMIT + 500)}
            ]}],
        }
        envelope = _snapshot(issue, bindings=bindings)
        assert len(envelope.content) == CONTENT_EXCERPT_LIMIT
        assert envelope.metadata["content_truncated"] is True, (
            "an excerpt presented as the whole text could support a "
            "conclusion the full body contradicts"
        )


# ── The guardrail: a ticket is not a launch ──────────────────────────


class TestATicketIsEvidenceNotTruth:
    """The invariant this module exists to hold.

    A ticket reaching Done means somebody moved a card. Feature
    completeness, validation and launch readiness are conclusions drawn
    from many sources with their own evidence thresholds.
    """

    @pytest.fixture
    def done_envelopes(self, bindings):
        outcome = normalize_jira_issue(
            fx.issue(fx.DONE_ISSUE), bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.envelopes
        return outcome.envelopes

    def test_a_done_issue_maps_to_ticket_completed(self, done_envelopes):
        assert [e.event_type for e in done_envelopes] == ["ticket_completed"]

    def test_a_done_issue_bears_no_launch_readiness(self, done_envelopes):
        """Asserted against the real refresh-scope policy rather than a
        transcription of it, so a future widening of the policy fails
        here instead of silently letting a board column move a readiness
        assessment."""
        for envelope in done_envelopes:
            scopes = determine_refresh_scope(envelope)
            assert RefreshScope.LAUNCH_READINESS not in scopes, (
                f"'{envelope.event_type}' can refresh launch readiness; a "
                f"ticket moving to Done must never be able to"
            )
            assert RefreshScope.COORDINATION not in scopes, (
                "a ticket status must not move a decision either"
            )

    def test_no_class_this_module_emits_can_reach_launch_readiness(self):
        for event_class in JIRA_EMITTED_EVENT_CLASSES:
            scopes = determine_refresh_scope(
                _FakeEnvelope(event_type=event_class)
            )
            assert RefreshScope.LAUNCH_READINESS not in scopes

    def test_ticket_completed_still_moves_the_workstream(self, done_envelopes):
        """The mapping is narrow, not useless: a closed ticket is real
        information about what the workstream now contains."""
        assert RefreshScope.WORKSTREAM in determine_refresh_scope(
            done_envelopes[0]
        )

    def test_the_interpretation_is_carried_with_the_evidence(
        self, done_envelopes,
    ):
        assert "not that the work is validated" in (
            done_envelopes[0].metadata["interpretation"]
        )

    def test_the_status_category_decides_rather_than_the_name(
        self, bindings,
    ):
        """Status names are per-workflow prose; ``statusCategory`` is
        Jira's fixed three-value vocabulary, so it wins when present. A
        site whose final column is called 'Shipped' is still done."""
        issue = copy.deepcopy(fx.issue(fx.DONE_ISSUE))
        issue["fields"]["status"]["name"] = "Shipped To Customers"
        assert _snapshot(issue, bindings=bindings).event_type == (
            "ticket_completed"
        )

    def test_a_status_outside_the_declared_set_is_ordinary_movement(
        self, bindings,
    ):
        """No completion is guessed from a name nobody declared."""
        issue = copy.deepcopy(fx.issue(fx.DONE_ISSUE))
        issue["fields"]["status"] = {
            "id": "10099", "name": "Pending Customer Sign-off",
        }
        assert _snapshot(issue, bindings=bindings).event_type == (
            "ticket_updated"
        )

    def test_a_deployment_specific_done_vocabulary_is_honoured(
        self, bindings,
    ):
        issue = copy.deepcopy(fx.issue(fx.DONE_ISSUE))
        issue["fields"]["status"] = {"id": "10099", "name": "Shipped"}
        envelope = _only(normalize_jira_issue(
            issue, bindings=bindings, received_at=RECEIVED,
            include_embedded=False,
            done_status_names=frozenset({"shipped"}),
        ))
        assert envelope.event_type == "ticket_completed"


class _FakeEnvelope:
    """The two attributes ``determine_refresh_scope`` reads.

    Cheaper than building a real envelope for a policy lookup, and it
    keeps the assertion about the policy rather than about envelopes.
    """

    def __init__(self, *, event_type: str):
        self.event_type = event_type


# ── The emitted vocabulary is closed ─────────────────────────────────


class TestTheEventVocabularyIsClosed:
    def test_every_emitted_class_is_one_the_runtime_maps(self):
        """A mapping target outside the recognized set would reach the
        temporal layer as an unrecognized string and silently degrade to
        generic observed evidence."""
        assert JIRA_EMITTED_EVENT_CLASSES <= RECOGNIZED_EVENT_CLASSES

    def test_every_emitted_class_appears_in_a_runtime_vocabulary(self):
        """Each class must be one the runtime actually names, in either
        the refresh-scope policy or the temporal mapping. A class in
        neither would be a string nothing downstream has an opinion
        about."""
        from tpllm.integrations.runtime import _EVENT_SCOPE_MAP
        from tpllm.runtime.sources import EVENT_CLASS_TO_TEMPORAL_TYPE

        known = set(_EVENT_SCOPE_MAP) | set(EVENT_CLASS_TO_TEMPORAL_TYPE)
        assert JIRA_EMITTED_EVENT_CLASSES <= known

    def test_the_ticket_classes_are_the_ones_with_a_refresh_scope(self):
        """And deliberately *not* in the temporal mapping, which is the
        honest result rather than an omission: a card in a column is not
        a state transition the bitemporal layer should assert, so a
        ticket lands as observed evidence while a blocker — which is a
        real organizational transition — gets its own temporal type."""
        from tpllm.integrations.runtime import _EVENT_SCOPE_MAP
        from tpllm.runtime.sources import EVENT_CLASS_TO_TEMPORAL_TYPE

        assert {"ticket_updated", "ticket_completed"} <= set(
            _EVENT_SCOPE_MAP
        )
        assert {"ticket_updated", "ticket_completed"}.isdisjoint(
            EVENT_CLASS_TO_TEMPORAL_TYPE
        )
        assert {"blocker_opened", "blocker_resolved"} <= set(
            EVENT_CLASS_TO_TEMPORAL_TYPE
        )

    def test_the_declared_set_matches_what_the_source_can_emit(self):
        """Checked against the source rather than by eye: a future
        mapping added without updating the declaration would escape every
        assertion above."""
        tree = ast.parse(_SOURCE.read_text())
        in_source = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in RECOGNIZED_EVENT_CLASSES
        }
        assert in_source == set(JIRA_EMITTED_EVENT_CLASSES), (
            f"declared {sorted(JIRA_EMITTED_EVENT_CLASSES)} but the module "
            f"names {sorted(in_source)}"
        )


# ── The snapshot: the present tense, and only that ───────────────────


class TestTheIssueSnapshot:
    def test_an_open_issue_is_ticket_movement(self, bindings):
        envelope = _snapshot(fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings)
        assert envelope.event_type == "ticket_updated"

    def test_reality_time_is_the_issue_update_not_the_poll(self, bindings):
        envelope = _snapshot(
            fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=LATER,
        )
        assert envelope.observed_at == datetime(2026, 10, 6, 7, 20)
        assert envelope.received_at == LATER

    def test_identity_is_the_immutable_id_not_the_key(self, bindings):
        """An issue key changes when a project is renamed or an issue is
        moved between projects. Keying on it would make one ticket's
        history look like two tickets the day someone reorganizes the
        board."""
        moved = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        moved["key"] = "XYZ-91"
        original = _snapshot(fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings)
        renamed = _snapshot(moved, bindings=bindings)
        assert original.idempotency_key == renamed.idempotency_key
        assert original.source_id == renamed.source_id

    def test_the_human_key_is_still_preserved_as_metadata(self, bindings):
        envelope = _snapshot(fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings)
        assert envelope.metadata["jira_issue_key"] == fx.IN_PROGRESS_ISSUE
        assert envelope.metadata["jira_issue_id"] == "10005"
        assert envelope.metadata["jira_project_key"] == fx.PROJECT_KEY
        assert envelope.metadata["jira_site"] == fx.SITE
        assert envelope.metadata["jira_issue_version"] == (
            "2026-10-06T07:20:00.000+0000"
        )

    def test_an_issue_with_no_id_is_rejected(self, bindings):
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue.pop("id")
        outcome = normalize_jira_issue(
            issue, bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.rejected
        assert "not a substitute" in outcome.rejected[0].reason

    def test_a_missing_timestamp_is_rejected_not_stamped_now(self, bindings):
        """CloudEvents permits substituting the current time when the
        occurrence time is unknown. Here that substitution would record
        today's reading as today's change."""
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["fields"].pop("updated")
        issue["fields"].pop("created")
        outcome = normalize_jira_issue(
            issue, bindings=bindings, received_at=RECEIVED,
            include_embedded=False,
        )
        assert not outcome.envelopes and outcome.rejected

    def test_created_is_the_fallback_when_nothing_was_edited(self, bindings):
        issue = copy.deepcopy(fx.issue(fx.MINIMAL_ISSUE))
        issue["fields"].pop("updated")
        envelope = _snapshot(issue, bindings=bindings)
        assert envelope.observed_at == datetime(2026, 10, 20, 6, 0)

    def test_the_project_key_falls_back_to_the_issue_key_prefix(
        self, bindings,
    ):
        """A search can be asked for a field set that omits the project
        object. Decomposing a provider-supplied identifier is not the
        same as guessing which project someone meant."""
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["fields"].pop("project")
        assert _snapshot(issue, bindings=bindings).metadata[
            "jira_project_key"
        ] == "ABC"

    def test_a_minimal_issue_still_normalizes(self, bindings):
        """No description, no priority, no assignee, no due date. A
        normalizer that needed them would drop the newest tickets, which
        are the ones a TPM cares about."""
        envelope = _snapshot(fx.issue(fx.MINIMAL_ISSUE), bindings=bindings)
        assert envelope.event_type == "ticket_updated"
        assert envelope.actor_refs

    def test_a_newly_raised_ticket_says_so(self, bindings):
        """An issue whose updated time still equals its created time has
        not been touched since it was raised. Carried as metadata rather
        than as an event class of its own: there is no
        ``ticket_created`` in the recognized vocabulary, and inventing
        one to capture a distinction the refresh policy does not act on
        would be vocabulary growth for its own sake."""
        envelope = _snapshot(fx.issue(fx.MINIMAL_ISSUE), bindings=bindings)
        assert envelope.metadata["jira_first_observation"] is True
        assert envelope.metadata["jira_created_at"] == (
            "2026-10-20T06:00:00.000+0000"
        )
        assert envelope.event_type == "ticket_updated"

    def test_an_edited_ticket_is_not_flagged_as_newly_raised(
        self, bindings,
    ):
        envelope = _snapshot(fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings)
        assert "jira_first_observation" not in envelope.metadata

    def test_the_pointer_is_a_page_a_person_can_open(self, bindings):
        envelope = _snapshot(fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings)
        assert envelope.source_pointer == (
            f"https://{fx.SITE}/browse/{fx.IN_PROGRESS_ISSUE}"
        )

    def test_the_snapshot_never_implies_a_transition(self, bindings):
        """The whole reason the changelog path exists. An issue now In
        Progress says nothing about when it moved there, and inventing
        the moment would fabricate history."""
        outcome = normalize_jira_issue(
            fx.issue(fx.MINIMAL_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert [e.event_type for e in outcome.envelopes] == ["ticket_updated"]
        assert all(
            "reality_time_basis" not in e.metadata
            or e.metadata["reality_time_basis"] != "changelog_entry_created"
            for e in outcome.envelopes
        )


# ── Idempotency ──────────────────────────────────────────────────────


class TestIdempotency:
    def test_the_key_does_not_move_with_our_clock(self, bindings):
        first = _snapshot(
            fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        second = _snapshot(
            fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=LATER,
        )
        assert first.idempotency_key == second.idempotency_key

    def test_an_edit_is_a_new_fact(self, bindings):
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["fields"]["updated"] = "2026-10-07T09:00:00.000+0000"
        assert _snapshot(issue, bindings=bindings).idempotency_key != (
            _snapshot(
                fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            ).idempotency_key
        )

    def test_no_key_contains_a_timestamp_of_ours(self, bindings):
        outcome = normalize_jira_issue(
            fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        stamp = RECEIVED.isoformat()
        for envelope in outcome.envelopes:
            assert stamp not in envelope.idempotency_key

    def test_two_items_in_one_entry_do_not_collide(self, bindings):
        """A priority change and a due-date change saved together both
        map to ticket_updated. A key without the field would collapse two
        real changes into one."""
        outcome = normalize_jira_changelog_entry(
            fx.changelog_entry(fx.PRIORITY_AND_DUEDATE),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        keys = [e.idempotency_key for e in outcome.envelopes]
        assert len(keys) == 2 and len(set(keys)) == 2

    def test_a_changelog_key_needs_no_version(self):
        """A changelog entry is immutable once written, so the entry's own
        id is the provider's strongest identity for it."""
        assert jira_changelog_key(
            issue_id="10005", history_id="10300", field="status",
            event_class="ticket_updated",
        ) == "jira:changelog:10005:10300:status:ticket_updated"

    def test_a_comment_key_carries_its_version(self):
        """Comments are editable, so the id alone would make an edit look
        like a redelivery of the original."""
        assert jira_comment_key(
            comment_id="10100", version="v2",
        ) == "jira:comment:10100:v2:chat_message"

    def test_an_unversioned_issue_is_still_keyable(self):
        assert "unversioned" in jira_issue_key(
            issue_id="10005", updated="", event_class="ticket_updated",
        )

    def test_a_delivery_id_wins_when_there_is_one(self):
        """A webhook delivery id is the provider's own event identity and
        stays the same across a redelivery, which is what the key has to
        mean."""
        assert jira_issue_key(
            issue_id="10005", updated="x", event_class="ticket_updated",
            delivery_id="d-1",
        ) == "jira:delivery:d-1:ticket_updated"


# ── The changelog: real transitions, with their own time ─────────────


class TestTheChangelog:
    def test_a_status_transition_keeps_its_own_reality_time(self, bindings):
        """The property the whole changelog path exists for. This
        transition was recorded on 14 September and is being read on 22
        October; stamping it with the poll time would move five weeks of
        history onto today and corrupt every scenario rooted in it."""
        envelope = _only(normalize_jira_changelog_entry(
            fx.changelog_entry(fx.STATUS_TRANSITION),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert envelope.observed_at == datetime(2026, 9, 14, 9, 12, 30)
        assert envelope.observed_at.month == 9
        assert envelope.received_at == RECEIVED
        assert envelope.metadata["reality_time_basis"] == (
            "changelog_entry_created"
        )

    def test_the_transition_is_legible(self, bindings):
        envelope = _only(normalize_jira_changelog_entry(
            fx.changelog_entry(fx.STATUS_TRANSITION),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert envelope.content == "status: To Do → In Progress"
        assert envelope.metadata["jira_from"] == "To Do"
        assert envelope.metadata["jira_to"] == "In Progress"
        assert envelope.metadata["jira_changed_field"] == "status"

    def test_a_transition_into_a_done_status_completes_the_ticket(
        self, bindings,
    ):
        outcome = normalize_jira_changelog_entry(
            fx.changelog_entry(fx.RESOLUTION_CHANGE),
            issue=fx.issue(fx.DONE_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        classes = {e.event_type for e in outcome.envelopes}
        assert classes == {"ticket_completed"}
        for envelope in outcome.envelopes:
            assert RefreshScope.LAUNCH_READINESS not in (
                determine_refresh_scope(envelope)
            )

    def test_a_resolution_is_not_read_as_success(self, bindings):
        """'Won't Do' closes a ticket without anything being delivered.
        The resolution is carried and deliberately not interpreted."""
        entry = copy.deepcopy(fx.changelog_entry(fx.RESOLUTION_CHANGE))
        entry["items"] = [{
            "field": "resolution", "fieldtype": "jira",
            "from": None, "fromString": None,
            "to": "10001", "toString": "Won't Do",
        }]
        envelope = _only(normalize_jira_changelog_entry(
            entry, issue=fx.issue(fx.DONE_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert envelope.event_type == "ticket_completed"
        assert envelope.metadata["jira_to"] == "Won't Do"
        assert "not read as success" in envelope.metadata["interpretation"]

    def test_clearing_a_resolution_is_a_reopening(self, bindings):
        entry = copy.deepcopy(fx.changelog_entry(fx.RESOLUTION_CHANGE))
        entry["items"] = [{
            "field": "resolution", "fieldtype": "jira",
            "from": "10000", "fromString": "Done",
            "to": None, "toString": None,
        }]
        envelope = _only(normalize_jira_changelog_entry(
            entry, issue=fx.issue(fx.DONE_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert envelope.event_type == "ticket_updated"
        assert "reopening" in envelope.metadata["interpretation"]

    def test_a_priority_change_is_not_a_blocker(self, bindings):
        """Nothing is prevented from proceeding by a priority change, and
        reporting one as a blocker would manufacture congestion."""
        envelopes = normalize_jira_changelog_entry(
            fx.changelog_entry(fx.PRIORITY_AND_DUEDATE),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ).envelopes
        assert {e.event_type for e in envelopes} == {"ticket_updated"}
        by_field = {e.metadata["jira_changed_field"]: e for e in envelopes}
        assert set(by_field) == {"priority", "duedate"}
        assert by_field["duedate"].metadata["jira_to"] == "2026-10-22"

    def test_a_due_date_change_is_not_a_commitment(self, bindings):
        envelope = [
            e for e in normalize_jira_changelog_entry(
                fx.changelog_entry(fx.PRIORITY_AND_DUEDATE),
                issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
                received_at=RECEIVED,
            ).envelopes
            if e.metadata["jira_changed_field"] == "duedate"
        ][0]
        assert envelope.event_type == "ticket_updated"
        assert "not a commitment the organization made" in (
            envelope.metadata["interpretation"]
        )

    def test_an_entry_with_no_id_is_rejected(self, bindings):
        entry = copy.deepcopy(fx.changelog_entry(fx.STATUS_TRANSITION))
        entry.pop("id")
        outcome = normalize_jira_changelog_entry(
            entry, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert outcome.rejected and "deduplicated" in (
            outcome.rejected[0].reason
        )

    def test_an_entry_with_no_created_time_is_rejected(self, bindings):
        """The entry's created time is the only honest reality time for
        the transition; the issue's updated time is a later, different
        moment."""
        entry = copy.deepcopy(fx.changelog_entry(fx.STATUS_TRANSITION))
        entry.pop("created")
        outcome = normalize_jira_changelog_entry(
            entry, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert not outcome.envelopes and outcome.rejected

    def test_an_empty_entry_is_reported_not_dropped(self, bindings):
        entry = copy.deepcopy(fx.changelog_entry(fx.STATUS_TRANSITION))
        entry["items"] = []
        outcome = normalize_jira_changelog_entry(
            entry, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert outcome.uninterpreted and not outcome.envelopes

    @pytest.mark.parametrize(
        "entry_id,needle",
        [
            (fx.LINK_CHANGE, "Parsing English"),
            (fx.WORKLOG_CHANGE, "never an individual's hours"),
            (fx.SPRINT_CHANGE, "guess about a configuration"),
        ],
    )
    def test_what_is_declined_says_why(self, bindings, entry_id, needle):
        outcome = normalize_jira_changelog_entry(
            fx.changelog_entry(entry_id),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert not outcome.envelopes
        assert outcome.uninterpreted
        assert any(needle in s.reason for s in outcome.uninterpreted)
        assert not outcome.needs_attention, (
            "declining to read a worklog is the designed behaviour, not a "
            "gap someone has to fix"
        )

    def test_an_unknown_custom_field_is_declined_by_name(self, bindings):
        entry = copy.deepcopy(fx.changelog_entry(fx.SPRINT_CHANGE))
        entry["items"][0]["field"] = "Story Points"
        outcome = normalize_jira_changelog_entry(
            entry, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert "Story Points" in outcome.uninterpreted[0].reason
        assert "site's configuration" in outcome.uninterpreted[0].reason

    def test_an_unknown_system_field_is_declined(self, bindings):
        entry = copy.deepcopy(fx.changelog_entry(fx.STATUS_TRANSITION))
        entry["items"][0]["field"] = "environment"
        entry["items"][0]["fieldtype"] = "jira"
        outcome = normalize_jira_changelog_entry(
            entry, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert not outcome.envelopes
        assert "never invented" in outcome.uninterpreted[0].reason


# ── Assignment: ownership, never measurement ─────────────────────────


class TestAssignment:
    def test_the_account_id_is_the_identity(self, bindings):
        """displayName is user-editable, repeats across people and is
        explicitly not identity. The accountId is the only stable
        identifier Atlassian exposes."""
        envelope = _only(normalize_jira_changelog_entry(
            fx.changelog_entry(fx.ASSIGNEE_CHANGE),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        ids = {ref.actor_id for ref in envelope.actor_refs}
        assert "5b10a2844c20165700ede21g" in ids
        assert "Example Assignee" not in ids

    def test_identity_is_proposed_never_asserted(self, bindings):
        """Deciding that an accountId and a Slack member id are one
        person is the identity layer's call, under a policy that refuses
        a mere name resemblance."""
        outcome = normalize_jira_issue(
            fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        for envelope in outcome.envelopes:
            for ref in envelope.actor_refs:
                assert ref.canonical_entity_id is None

    def test_assignment_is_ticket_movement(self, bindings):
        envelope = _only(normalize_jira_changelog_entry(
            fx.changelog_entry(fx.ASSIGNEE_CHANGE),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert envelope.event_type == "ticket_updated"
        assert "no count, rate or backlog size" in (
            envelope.metadata["interpretation"]
        )

    def test_nothing_counts_or_rates_a_person(self, bindings):
        """The hard, test-enforced invariant of this repository: the
        system measures dependency congestion, queues and capacity and
        never what an individual did. A ticket integration is the easiest
        place for that line to erode — 'issues overdue by Alice' is two
        lines of code away — so it is asserted on the output."""
        outcome = normalize_jira_payloads(
            issues=fx.load("issues"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        # Whole segments, not substrings: ``account`` contains ``count``
        # and an account id is an identifier rather than a measurement.
        # The same narrowing the repository's own detector had to make.
        forbidden = {
            "score", "scores", "grade", "grades", "rate", "rates",
            "rating", "hours", "velocity", "productivity",
            "responsiveness", "efficiency", "count", "counts", "overdue",
            "ranking", "utilization", "metrics", "missed", "late",
        }
        for envelope in outcome.envelopes:
            for key in envelope.metadata:
                offending = set(key.lower().split("_")) & forbidden
                assert not offending, (
                    f"metadata key '{key}' measures somebody: {offending}"
                )

    def test_a_worklog_never_reaches_an_envelope(self, bindings):
        """Jira hands us an individual's logged hours whether we want
        them or not. Refusing at the boundary is cheaper than removing
        them downstream, and this asserts they do not slip through in
        content either."""
        outcome = normalize_jira_payloads(
            issues=fx.load("issues"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        for envelope in outcome.envelopes:
            blob = envelope.model_dump_json()
            assert "timeSpentSeconds" not in blob
            assert "14400" not in blob
        assert any(
            s.payload_kind == "worklog" for s in outcome.uninterpreted
        ), "the refusal should be visible, not silent"


# ── Comments: communication, and no more ─────────────────────────────


class TestComments:
    def test_a_comment_is_a_chat_message(self, bindings):
        """'Approved from my side' is a sentence someone typed, not an
        approval the organization recorded. Whether the content expresses
        a decision is a judgement for the engines that weigh evidence."""
        envelope = _only(normalize_jira_comment(
            fx.comment(fx.PLAIN_COMMENT), issue=fx.issue(fx.IN_PROGRESS_ISSUE),
            bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.event_type == "chat_message"
        assert "Approved" in envelope.content

    def test_a_comment_cannot_move_a_decision(self, bindings):
        envelope = _only(normalize_jira_comment(
            fx.comment(fx.PLAIN_COMMENT), issue=fx.issue(fx.IN_PROGRESS_ISSUE),
            bindings=bindings, received_at=RECEIVED,
        ))
        assert determine_refresh_scope(envelope) == {
            RefreshScope.EVIDENCE_ONLY
        }

    def test_the_comment_keeps_its_own_reality_time(self, bindings):
        envelope = _only(normalize_jira_comment(
            fx.comment(fx.PLAIN_COMMENT), issue=fx.issue(fx.IN_PROGRESS_ISSUE),
            bindings=bindings, received_at=LATER,
        ))
        assert envelope.observed_at.date() == datetime(2026, 10, 2).date()

    def test_an_edit_is_its_own_fact_at_its_own_time(self, bindings):
        """An edited comment is a second fact about the same comment. The
        version in the key keeps both rather than letting one silently
        replace the other."""
        envelope = _only(normalize_jira_comment(
            fx.comment(fx.EDITED_COMMENT),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert envelope.observed_at == datetime(2026, 10, 3, 9, 41, 27, 118000)
        assert envelope.metadata["jira_comment_edited"] is True
        assert "2026-10-03T09:41" in envelope.idempotency_key

    def test_the_author_and_issue_context_are_preserved(self, bindings):
        envelope = _only(normalize_jira_comment(
            fx.comment(fx.PLAIN_COMMENT), issue=fx.issue(fx.IN_PROGRESS_ISSUE),
            bindings=bindings, received_at=RECEIVED,
        ))
        assert [r.actor_id for r in envelope.actor_refs] == [
            "5b10a0effa615349cb016cd8"
        ]
        assert envelope.metadata["jira_issue_key"] == fx.IN_PROGRESS_ISSUE
        assert envelope.metadata["jira_comment_id"] == fx.PLAIN_COMMENT

    def test_a_restricted_comment_is_flagged(self, bindings):
        """Visibility is not a secret, but a reader should know the
        excerpt came from a comment most of the team cannot see."""
        envelope = _only(normalize_jira_comment(
            fx.comment(fx.RESTRICTED_COMMENT),
            issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert envelope.metadata["jira_comment_restricted"] is True

    def test_a_comment_with_no_id_is_rejected(self, bindings):
        comment = copy.deepcopy(fx.comment(fx.PLAIN_COMMENT))
        comment.pop("id")
        outcome = normalize_jira_comment(
            comment, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert outcome.rejected

    def test_a_v2_shaped_body_does_not_crash(self, bindings):
        comment = copy.deepcopy(fx.comment(fx.PLAIN_COMMENT))
        comment["body"] = "a plain string, as v2 returned"
        envelope = _only(normalize_jira_comment(
            comment, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert envelope.content == "a plain string, as v2 returned"


# ── Dependencies: conservatively, or not at all ──────────────────────


class TestIssueLinks:
    def _link(self, link_id, *, bindings, issue=None):
        return normalize_jira_issue_link(
            fx.issue_link(link_id),
            issue=issue or fx.issue(fx.IN_PROGRESS_ISSUE),
            bindings=bindings, received_at=RECEIVED,
        )

    def test_only_blocks_is_a_dependency_type(self):
        assert BLOCKING_LINK_TYPE_NAMES == frozenset({"blocks"})

    def test_a_blocking_link_with_an_open_blocker_opens_a_blocker(
        self, bindings,
    ):
        envelope = _only(self._link(fx.BLOCKED_BY_OPEN_LINK,
                                    bindings=bindings))
        assert envelope.event_type == "blocker_opened"
        assert envelope.metadata["jira_blocking_issue_key"] == "ABC-43"
        assert envelope.metadata["jira_link_type"] == "Blocks"

    def test_a_blocking_link_whose_blocker_is_done_is_resolved(
        self, bindings,
    ):
        envelope = _only(self._link(fx.BLOCKED_BY_DONE_LINK,
                                    bindings=bindings))
        assert envelope.event_type == "blocker_resolved"
        assert envelope.metadata["jira_blocking_issue_status_category"] == (
            "done"
        )

    def test_relates_to_is_never_a_dependency(self, bindings):
        """The mapping this test exists for. 'relates to' says two
        tickets are worth reading together and nothing about whether
        either can proceed; turning it into a dependency would invent
        congestion, and congestion is what this stack reports on."""
        outcome = self._link(fx.RELATES_LINK, bindings=bindings)
        assert outcome.envelopes == []
        assert outcome.uninterpreted
        assert "says nothing about whether either can proceed" in (
            outcome.uninterpreted[0].reason
        )

    def test_a_duplicate_is_not_a_dependency(self, bindings):
        outcome = self._link(fx.DUPLICATE_LINK, bindings=bindings)
        assert outcome.envelopes == []
        assert "recorded twice" in outcome.uninterpreted[0].reason

    def test_the_blocking_side_is_not_counted_twice(self, bindings):
        """The same dependency edge is normalized from the blocked side.
        Emitting from both would double the apparent congestion the
        moment both issues are polled."""
        outcome = self._link(fx.BLOCKS_OUTWARD_LINK, bindings=bindings)
        assert outcome.envelopes == []
        assert "two organizational facts" in outcome.uninterpreted[0].reason

    def test_an_unknown_link_type_is_declined_with_its_name(self, bindings):
        link = copy.deepcopy(fx.issue_link(fx.BLOCKED_BY_OPEN_LINK))
        link["type"]["name"] = "Causes"
        outcome = normalize_jira_issue_link(
            link, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert outcome.envelopes == []
        assert "'Causes'" in outcome.uninterpreted[0].reason

    def test_an_unknown_blocker_status_is_reported_not_assumed(
        self, bindings,
    ):
        """An unresolved blocker is a stronger claim than no claim, so a
        link whose counterpart status is absent produces a report."""
        link = copy.deepcopy(fx.issue_link(fx.BLOCKED_BY_OPEN_LINK))
        link["inwardIssue"].pop("fields")
        outcome = normalize_jira_issue_link(
            link, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        )
        assert outcome.envelopes == []
        assert "Reported rather than assumed open" in (
            outcome.uninterpreted[0].reason
        )

    def test_the_reality_time_basis_is_stated(self, bindings):
        """A link carries no timestamp of its own. The issue's updated
        time is an upper bound on when the link appeared, not the moment
        it was created, and the metadata says which it is rather than
        implying a precision the payload does not have."""
        envelope = _only(self._link(fx.BLOCKED_BY_OPEN_LINK,
                                    bindings=bindings))
        assert envelope.metadata["reality_time_basis"] == "issue_updated"
        assert envelope.observed_at == datetime(2026, 10, 6, 7, 20)

    def test_the_providers_vocabulary_is_preserved(self, bindings):
        """So a future policy can revisit a link type without re-reading
        Jira."""
        envelope = _only(self._link(fx.BLOCKED_BY_OPEN_LINK,
                                    bindings=bindings))
        assert envelope.metadata["jira_link_inward_description"] == (
            "is blocked by"
        )
        assert envelope.metadata["jira_link_outward_description"] == "blocks"

    def test_a_link_with_no_timestamp_anywhere_is_rejected(self, bindings):
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["fields"].pop("updated")
        issue["fields"].pop("created")
        outcome = normalize_jira_issue_link(
            fx.issue_link(fx.BLOCKED_BY_OPEN_LINK), issue=issue,
            bindings=bindings, received_at=RECEIVED,
        )
        assert not outcome.envelopes and outcome.rejected


# ── Webhooks ─────────────────────────────────────────────────────────


class TestWebhooks:
    def test_an_issue_update_yields_the_state_and_the_transition(
        self, bindings,
    ):
        """The delivery carries both, and they are two different facts:
        the snapshot is the present, the changelog entry is the moment it
        changed."""
        outcome = normalize_jira_webhook(
            event_name="jira:issue_updated",
            payload=fx.webhook("jira:issue_updated"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        kinds = {e.source_type for e in outcome.envelopes}
        assert kinds == {"ticket", "ticket_transition"}

    def test_the_delivery_timestamp_is_never_the_key(self, bindings):
        """Not our clock and not the provider's. The same delivery can
        legitimately arrive carrying a different timestamp, and
        redelivery is the case the key exists for."""
        payload = fx.webhook("jira:issue_updated")
        outcome = normalize_jira_webhook(
            event_name="jira:issue_updated", payload=payload,
            bindings=bindings, received_at=RECEIVED, site=fx.SITE,
        )
        assert outcome.envelopes
        for envelope in outcome.envelopes:
            assert str(payload["timestamp"]) not in envelope.idempotency_key

    def test_a_redelivery_produces_the_same_keys(self, bindings):
        payload = copy.deepcopy(fx.webhook("jira:issue_updated"))
        first = normalize_jira_webhook(
            event_name="jira:issue_updated", payload=payload,
            bindings=bindings, received_at=RECEIVED, site=fx.SITE,
        )
        payload["timestamp"] = payload["timestamp"] + 5000
        second = normalize_jira_webhook(
            event_name="jira:issue_updated", payload=payload,
            bindings=bindings, received_at=LATER, site=fx.SITE,
        )
        assert [e.idempotency_key for e in first.envelopes] == [
            e.idempotency_key for e in second.envelopes
        ]

    def test_a_delivery_id_is_used_when_a_receiver_supplies_one(
        self, bindings,
    ):
        outcome = normalize_jira_webhook(
            event_name="comment_created",
            payload=fx.webhook("comment_created"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE, delivery_id="abc-123",
        )
        assert _only(outcome).idempotency_key == (
            "jira:delivery:abc-123:chat_message"
        )

    def test_a_comment_delivery_is_a_chat_message(self, bindings):
        outcome = normalize_jira_webhook(
            event_name="comment_created",
            payload=fx.webhook("comment_created"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        assert _only(outcome).event_type == "chat_message"

    def test_a_worklog_delivery_is_declined(self, bindings):
        outcome = normalize_jira_webhook(
            event_name="worklog_updated",
            payload=fx.webhook("worklog_updated"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        assert outcome.envelopes == []
        assert "never invented" in outcome.uninterpreted[0].reason

    def test_a_deletion_is_not_a_transition(self, bindings):
        """Nothing was completed, resolved or decided, and the journal is
        append-only: there is nothing to unsay."""
        outcome = normalize_jira_webhook(
            event_name="jira:issue_deleted",
            payload=fx.webhook("jira:issue_updated"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        assert outcome.envelopes == []
        assert "nothing to unsay" in outcome.uninterpreted[0].reason

    def test_a_delivery_with_no_issue_is_rejected(self, bindings):
        outcome = normalize_jira_webhook(
            event_name="jira:issue_updated", payload={}, bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        assert outcome.rejected

    def test_the_webhook_event_name_can_come_from_the_body(self, bindings):
        outcome = normalize_payload(
            payload_kind="webhook",
            payload=fx.webhook("comment_created"), bindings=bindings,
            received_at=RECEIVED, scope=fx.SITE,
        )
        assert _only(outcome).event_type == "chat_message"


# ── Secrets ──────────────────────────────────────────────────────────


class TestNoCredentialSurvives:
    """A ticket description is prose a person wrote in someone else's
    system, so it can contain anything — including the token they were
    debugging with. The journal is append-only, which makes a credential
    that reaches it permanent."""

    SECRETS = {
        "github": "ghp_aAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpP12",
        "bearer": "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz0123",
    }

    @pytest.mark.parametrize("name", sorted(SECRETS))
    def test_a_secret_in_a_description_is_redacted(self, bindings, name):
        secret = self.SECRETS[name]
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["fields"]["description"] = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": f"reproduce with {secret} please"}
            ]}],
        }
        envelope = _snapshot(issue, bindings=bindings)
        assert secret not in (envelope.content or "")
        assert secret not in envelope.model_dump_json()
        assert envelope.metadata["redaction_applied"] is True

    @pytest.mark.parametrize("name", sorted(SECRETS))
    def test_a_secret_in_a_comment_is_redacted(self, bindings, name):
        secret = self.SECRETS[name]
        comment = copy.deepcopy(fx.comment(fx.PLAIN_COMMENT))
        comment["body"] = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": f"token is {secret}"}
            ]}],
        }
        envelope = _only(normalize_jira_comment(
            comment, issue=fx.issue(fx.IN_PROGRESS_ISSUE), bindings=bindings,
            received_at=RECEIVED,
        ))
        assert secret not in envelope.model_dump_json()

    def test_the_sentence_around_the_secret_survives(self, bindings):
        issue = copy.deepcopy(fx.issue(fx.IN_PROGRESS_ISSUE))
        issue["fields"]["description"] = (
            f"reproduce with {self.SECRETS['github']} on staging"
        )
        envelope = _snapshot(issue, bindings=bindings)
        assert "on staging" in envelope.content, (
            "only the credential is removed; the sentence is the evidence"
        )

    def test_no_fixture_carries_a_credential_shape(self):
        """A committed fixture must not carry a secret even a synthetic
        one, because the next person to read it cannot tell."""
        from tpllm.integrations.envelopes import redact_secret_text
        import json

        for name in fx.fixture_names():
            raw = json.dumps(fx.load(name))
            _, found = redact_secret_text(raw)
            assert not found, f"{name} contains credential-shaped text"


# ── The uniform entry point ──────────────────────────────────────────


class TestTheRegistryEntryPoint:
    def test_an_issue_dispatches(self, bindings):
        outcome = normalize_payload(
            payload_kind="issue", payload=fx.issue(fx.MINIMAL_ISSUE),
            bindings=bindings, received_at=RECEIVED, scope=fx.SITE,
        )
        assert _only(outcome).source_type == "ticket"

    def test_a_wrapped_changelog_entry_dispatches(self, bindings):
        outcome = normalize_payload(
            payload_kind="changelog",
            payload={
                "issue": fx.issue(fx.IN_PROGRESS_ISSUE),
                "changelog": fx.changelog_entry(fx.STATUS_TRANSITION),
            },
            bindings=bindings, received_at=RECEIVED, scope=fx.SITE,
        )
        assert _only(outcome).source_type == "ticket_transition"

    def test_a_top_level_entry_dispatches_too(self, bindings):
        """Both shapes occur, depending on which endpoint produced the
        payload."""
        payload = dict(fx.comment(fx.PLAIN_COMMENT))
        payload["issue"] = fx.issue(fx.IN_PROGRESS_ISSUE)
        outcome = normalize_payload(
            payload_kind="comment", payload=payload, bindings=bindings,
            received_at=RECEIVED, scope=fx.SITE,
        )
        assert _only(outcome).event_type == "chat_message"

    def test_an_issue_link_dispatches(self, bindings):
        outcome = normalize_payload(
            payload_kind="issue_link",
            payload={
                "issue": fx.issue(fx.IN_PROGRESS_ISSUE),
                "issue_link": fx.issue_link(fx.BLOCKED_BY_OPEN_LINK),
            },
            bindings=bindings, received_at=RECEIVED, scope=fx.SITE,
        )
        assert _only(outcome).event_type == "blocker_opened"

    def test_an_unknown_kind_is_reported_not_guessed(self, bindings):
        """A dispatcher that fell back to 'probably an issue' would
        invent a fact."""
        outcome = normalize_payload(
            payload_kind="sprint", payload={}, bindings=bindings,
            received_at=RECEIVED, scope=fx.SITE,
        )
        assert not outcome.envelopes
        assert outcome.rejected
        assert "would invent a fact" in outcome.rejected[0].reason
        assert outcome.needs_attention

    def test_the_signature_matches_every_other_provider(self):
        """The seam that lets the CLI and the cross-provider tests treat
        providers interchangeably instead of carrying a conditional per
        provider."""
        import inspect

        from tpllm.integrations import normalizers as github

        assert inspect.signature(normalize_payload) == inspect.signature(
            github.normalize_payload
        )


# ── Batches ──────────────────────────────────────────────────────────


class TestBatches:
    def test_a_batch_reports_everything_it_skipped(self, bindings):
        outcome = normalize_jira_payloads(
            issues=fx.load("issues"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        assert outcome.envelopes
        assert outcome.uninterpreted, (
            "a normalizer that silently discarded what it does not "
            "understand would make 'no evidence' and 'evidence we could "
            "not read' indistinguishable"
        )
        assert not outcome.needs_attention

    def test_every_envelope_is_bound_to_the_declared_project(
        self, bindings,
    ):
        outcome = normalize_jira_payloads(
            issues=fx.load("issues"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        for envelope in outcome.envelopes:
            assert envelope.project_ids == [fx.PROJECT]
            assert envelope.source_system == JIRA

    def test_the_whole_batch_is_deterministic(self, bindings):
        first = normalize_jira_payloads(
            issues=fx.load("issues"), bindings=bindings,
            received_at=RECEIVED, site=fx.SITE,
        )
        second = normalize_jira_payloads(
            issues=fx.load("issues"), bindings=bindings,
            received_at=LATER, site=fx.SITE,
        )
        assert [e.idempotency_key for e in first.envelopes] == [
            e.idempotency_key for e in second.envelopes
        ]

    def test_the_declared_done_vocabulary_is_short_on_purpose(self):
        """A longer default would be a guess about somebody's workflow."""
        assert "in review" not in DEFAULT_DONE_STATUS_NAMES
        assert "done" in DEFAULT_DONE_STATUS_NAMES
