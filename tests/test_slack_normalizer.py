"""Tests for the Slack anti-corruption layer.

Slack breaks the five rules in ``normalizers``' docstring in ways GitHub
cannot, so what is pinned here is mostly the Slack-specific way each one
could fail:

- **A channel name is never a project.** GitHub gives one repository per
  payload; Slack gives a channel whose name somebody typed, and
  ``#atlas-launch`` may be where the Payments team argues. Bindings are
  on the opaque ids, a rename is a non-event, and an unbound channel is
  reported.
- **An edit is a new fact.** "v2 is approved" at 09:00 edited to "v3 is
  approved" at 09:20 must leave both in history, because a question
  asked as of 09:10 has to still see v2. A chat client overwrites; an
  append-only bitemporal store cannot.
- **A deletion removes a source, not history.** Somebody deleting a
  message cannot un-say what the organization acted on.
- **A thread is an argument.** Flattening replies into independent
  messages is how a system concludes four people agreed when three were
  objecting.
- **Nothing is measured about anybody.** Reactions are declined, and
  the reason is in the decline table rather than in a comment: a
  reaction count is the start of a responsiveness metric.

Every fixture these run against has provenance
``official_documentation`` — reproduced from Slack's published
examples, because slack.com is unreachable from this environment. That
is weaker than a recording and is said out loud everywhere it matters.
"""

from __future__ import annotations

import copy
from datetime import datetime

import pytest

from tpllm.integrations.normalizers import (
    CONTENT_EXCERPT_LIMIT,
    RECOGNIZED_EVENT_CLASSES,
    NormalizationOutcome,
    ProjectBindings,
    TimestampMissingError,
)
from tpllm.integrations.slack_normalizer import (
    AVAILABILITY_EVENT_CLASS,
    CHANNEL_SCOPE_EVENT_CLASS,
    MESSAGE_EVENT_CLASS,
    SLACK,
    SLACK_PAYLOAD_KINDS,
    SOURCE_AVAILABILITY_DELETED,
    ScopeConflictError,
    normalize_payload,
    normalize_slack_channel_scope,
    normalize_slack_event,
    normalize_slack_message,
    normalize_slack_message_changed,
    normalize_slack_message_deleted,
    normalize_slack_payloads,
    slack_availability_key,
    slack_bindings,
    slack_message_key,
    slack_scope,
    slack_ts_to_naive_utc,
)
from tests import _slack_fixtures as fx

RECEIVED = datetime(2026, 10, 21, 4, 40)
#: An hour later, for the "the same payload gives the same key" tests.
LATER = datetime(2026, 10, 21, 5, 40)

#: The fixture scenario, in wall-clock terms. The thread root is said at
#: 09:00 and edited at 09:20; the deletion lands the next day.
SAID_AT = datetime(2026, 2, 25, 9, 0, 0, 100)
EDITED_AT = datetime(2026, 2, 25, 9, 20, 0, 300)
DELETED_AT = datetime(2026, 2, 26, 11, 0, 0, 400)


@pytest.fixture
def bindings():
    return fx.bindings()


def _only(outcome: NormalizationOutcome):
    assert len(outcome.envelopes) == 1, (
        f"expected one envelope, got {len(outcome.envelopes)}; "
        f"unbound={[s.reason for s in outcome.unbound]} "
        f"uninterpreted={[s.reason for s in outcome.uninterpreted]} "
        f"rejected={[s.reason for s in outcome.rejected]}"
    )
    return outcome.envelopes[0]


def _message(outcome_payload: dict, **overrides) -> dict:
    payload = copy.deepcopy(outcome_payload)
    payload.update(overrides)
    return payload


# ── Slack's timestamps ───────────────────────────────────────────────


class TestSlackTimestamps:
    """A ``ts`` is epoch seconds plus six digits Slack uses to keep
    messages unique inside one second. Reading only the seconds would
    give two different messages one reality time."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1772010000.000100", datetime(2026, 2, 25, 9, 0, 0, 100)),
            ("1772010000.123456", datetime(2026, 2, 25, 9, 0, 0, 123456)),
            # A short fraction is right-padded, not read as microseconds.
            ("1772010000.1", datetime(2026, 2, 25, 9, 0, 0, 100000)),
            ("1772010000", datetime(2026, 2, 25, 9, 0)),
        ],
    )
    def test_a_ts_becomes_the_moment_it_names(self, value, expected):
        assert slack_ts_to_naive_utc(value) == expected

    def test_the_result_is_naive_utc(self):
        """The runtime's clock is naive UTC throughout; a tz-aware value
        raises TypeError on the first comparison, inside the
        orchestrator's temporal ordering and far from the cause."""
        assert slack_ts_to_naive_utc("1772010000.000100").tzinfo is None

    def test_the_microsecond_suffix_is_not_lost_to_float_precision(self):
        """Ten digits of seconds plus six of fraction do not fit in a
        float with the last one intact, so the two integer parts are
        combined instead. Two messages a microsecond apart must stay two
        moments."""
        first = slack_ts_to_naive_utc("1772010000.000001")
        second = slack_ts_to_naive_utc("1772010000.000002")
        assert first != second
        assert second.microsecond - first.microsecond == 1

    @pytest.mark.parametrize(
        "value", ["", "   ", None, "not-a-ts", "abc.def", "-1.0", {}, []],
    )
    def test_an_unusable_ts_is_refused(self, value):
        with pytest.raises(TimestampMissingError):
            slack_ts_to_naive_utc(value)

    @pytest.mark.parametrize("value", ["0", "0.0", "0.000000"])
    def test_a_zero_ts_is_refused_rather_than_dated_to_1970(self, value):
        """The specific failure this guards: a defaulted or cleared ts
        would anchor a fact in 1970 and quietly poison every scenario
        rooted in it."""
        with pytest.raises(TimestampMissingError, match="non-positive"):
            slack_ts_to_naive_utc(value)

    def test_the_error_names_what_a_ts_is_rather_than_defaulting(self):
        with pytest.raises(TimestampMissingError, match="not a substitute"):
            slack_ts_to_naive_utc(None)


# ── Bindings: a channel name is never a project ──────────────────────


class TestBindings:
    def test_the_scope_is_both_opaque_ids(self):
        """A channel id is only unique within a workspace. A
        multi-tenant deployment keyed on the channel alone would
        eventually bind one organization's channel to another's
        project."""
        assert slack_scope(
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ) == f"{fx.WORKSPACE}/{fx.CHANNEL}"

    def test_a_binding_is_declared_on_ids_not_names(self, bindings):
        assert bindings.projects_for(
            source_system=SLACK, external_scope=fx.SCOPE,
        ) == [fx.PROJECT]

    def test_a_channel_name_resolves_to_nothing(self, bindings):
        """The guess this layer exists to refuse. ``#atlas-launch``
        looks like the Atlas project and may be where Payments argues."""
        for name in ("atlas-launch", "#atlas-launch", f"{fx.WORKSPACE}/atlas"):
            assert bindings.projects_for(
                source_system=SLACK, external_scope=name,
            ) is None

    def test_one_channel_may_serve_several_projects(self):
        many = slack_bindings(mapping={
            (fx.WORKSPACE, fx.CHANNEL): ["atlas", "payments"],
        })
        assert many.projects_for(
            source_system=SLACK, external_scope=fx.SCOPE,
        ) == ["atlas", "payments"]

    def test_one_project_may_live_in_several_channels(self):
        many = slack_bindings(mapping={
            (fx.WORKSPACE, "C0AAAAAA"): ["atlas"],
            (fx.WORKSPACE, "C0BBBBBB"): ["atlas"],
        })
        for channel in ("C0AAAAAA", "C0BBBBBB"):
            assert many.projects_for(
                source_system=SLACK,
                external_scope=slack_scope(
                    workspace_id=fx.WORKSPACE, channel_id=channel,
                ),
            ) == ["atlas"]

    def test_bound_to_no_project_is_not_the_same_as_unbound(self):
        """``None`` means nothing is declared and a person must decide;
        ``[]`` means somebody declared that this channel feeds no
        project, which is a legitimate answer."""
        declared = slack_bindings(mapping={(fx.WORKSPACE, fx.CHANNEL): []})
        assert declared.projects_for(
            source_system=SLACK, external_scope=fx.SCOPE,
        ) == []

    def test_an_unbound_channel_is_reported_not_journalled(self):
        outcome = normalize_slack_message(
            fx.thread_root(), bindings=fx.unbound_bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert len(outcome.unbound) == 1
        assert "never allowed to supply it" in outcome.unbound[0].reason
        assert outcome.needs_attention, (
            "an unbound channel is a person's problem, not a designed "
            "silence like a declined reaction"
        )

    def test_bindings_are_the_shared_machinery(self, bindings):
        """Built on ProjectBindings rather than a parallel
        implementation, so a fix there lands for Slack too."""
        assert isinstance(bindings, ProjectBindings)
        assert all(b.source_system == SLACK for b in bindings.bindings)


class TestScopeResolution:
    def test_a_polled_message_takes_its_channel_from_the_caller(
        self, bindings,
    ):
        """A ``conversations.history`` item carries no channel at all;
        the caller is the only thing that knows which one it asked
        for."""
        envelope = _only(normalize_slack_message(
            fx.thread_root(), bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert envelope.metadata["channel_id"] == fx.CHANNEL

    def test_the_uniform_scope_string_works_too(self, bindings):
        envelope = _only(normalize_payload(
            payload_kind="message", payload=fx.thread_root(),
            bindings=bindings, received_at=RECEIVED, scope=fx.SCOPE,
        ))
        assert envelope.metadata["external_scope"] == fx.SCOPE

    def test_a_disagreement_is_rejected_rather_than_resolved(
        self, bindings,
    ):
        """Preferring either side would be a guess, and the cost of
        guessing wrong is evidence filed under the wrong project."""
        payload = _message(fx.thread_root(), channel="C0WRONG1")
        outcome = normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert "does not choose between them" in outcome.rejected[0].reason

    def test_an_unidentifiable_channel_is_rejected(self, bindings):
        outcome = normalize_slack_message(
            fx.thread_root(), bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.envelopes == []
        assert "workspace id and a channel id" in outcome.rejected[0].reason

    def test_the_conflict_error_is_its_own_type(self):
        assert issubclass(ScopeConflictError, ValueError)


# ── Channel messages ─────────────────────────────────────────────────


class TestChannelMessages:
    @pytest.fixture
    def envelope(self, bindings):
        return _only(normalize_slack_message(
            fx.thread_root(), bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))

    def test_a_message_is_evidence_and_nothing_more(self, envelope):
        """``chat_message``'s refresh scope is evidence-only. Somebody
        typing "approved" is evidence about a decision and is never, on
        its own, the decision."""
        assert envelope.event_type == MESSAGE_EVENT_CLASS == "chat_message"

    def test_reality_time_is_when_it_was_said(self, envelope):
        assert envelope.observed_at == SAID_AT
        assert envelope.received_at == RECEIVED
        assert envelope.observed_at < envelope.received_at, (
            "a February message read in October occurred in February"
        )

    def test_the_project_comes_from_the_declaration(self, envelope):
        assert envelope.project_ids == [fx.PROJECT]

    def test_the_identity_fields_survive(self, envelope):
        metadata = envelope.metadata
        assert metadata["workspace_id"] == fx.WORKSPACE
        assert metadata["channel_id"] == fx.CHANNEL
        assert metadata["slack_user_id"] == "U0ABCDEF"
        assert metadata["message_ts"] == fx.thread_root()["ts"]

    def test_the_author_is_proposed_not_resolved(self, envelope):
        """The identity layer decides whether a Slack id and a GitHub
        handle are one person, under a policy that refuses a mere
        resemblance. Nothing here claims it."""
        assert [ref.actor_id for ref in envelope.actor_refs] == ["U0ABCDEF"]
        assert all(
            ref.canonical_entity_id is None for ref in envelope.actor_refs
        )

    def test_a_display_name_is_never_used_as_identity(self, bindings):
        """Slack display names are self-service, collide freely and
        change without notice."""
        payload = _message(
            fx.thread_root(), username="Dana", user_profile={
                "display_name": "Dana", "real_name": "Dana Example",
            },
        )
        envelope = _only(normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert [ref.actor_id for ref in envelope.actor_refs] == ["U0ABCDEF"]
        assert all(ref.display_name is None for ref in envelope.actor_refs)

    def test_the_text_is_carried_as_an_excerpt(self, envelope):
        assert "v2 import path" in envelope.content

    def test_a_long_message_is_truncated_and_says_so(self, bindings):
        """So nothing downstream reads an excerpt as the whole message
        and concludes something the full text contradicts."""
        payload = _message(fx.thread_root(), text="x" * (
            CONTENT_EXCERPT_LIMIT + 50
        ))
        envelope = _only(normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert len(envelope.content) == CONTENT_EXCERPT_LIMIT
        assert envelope.metadata["content_truncated"] is True

    def test_the_pointer_does_not_invent_a_workspace_domain(self, envelope):
        """The workspace subdomain is not in the payload. Building
        ``https://acme.slack.com/...`` from a team id would produce a
        link that looks authoritative and does not work."""
        assert envelope.source_pointer.startswith("slack://")
        assert "https://" not in envelope.source_pointer

    def test_the_source_id_is_the_message_not_its_text(self, envelope):
        assert envelope.source_id == (
            f"{fx.WORKSPACE}/{fx.CHANNEL}:{fx.thread_root()['ts']}"
        )

    def test_a_message_with_no_text_is_reported_not_invented(
        self, bindings,
    ):
        outcome = normalize_slack_message(
            _message(fx.thread_root(), text=""), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert "asserts nothing readable" in outcome.uninterpreted[0].reason

    def test_a_message_with_no_ts_is_rejected_not_stamped_now(
        self, bindings,
    ):
        outcome = normalize_slack_message(
            _message(fx.thread_root(), ts=""), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert "not a substitute" in outcome.rejected[0].reason


# ── Threads: the shape of an argument ────────────────────────────────


class TestThreads:
    @pytest.fixture
    def reply(self, bindings):
        return _only(normalize_slack_message(
            fx.thread_reply(), bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))

    def test_a_reply_knows_it_is_one(self, reply):
        assert reply.metadata["is_thread_reply"] is True

    def test_a_reply_retains_the_root_it_answers(self, reply):
        """What makes proposal → disagreement → clarification →
        conclusion followable. Flattening a thread is how a system
        concludes four people agreed when three were objecting to the
        first one."""
        root_ts = fx.thread_root()["ts"]
        assert reply.metadata["thread_ts"] == root_ts
        assert reply.metadata["thread_root_source_id"] == (
            f"{fx.WORKSPACE}/{fx.CHANNEL}:{root_ts}"
        )

    def test_a_reply_retains_its_own_identity_too(self, reply):
        assert reply.metadata["message_ts"] == fx.thread_reply()["ts"]
        assert reply.source_id.endswith(fx.thread_reply()["ts"])

    def test_a_reply_retains_channel_and_author(self, reply):
        assert reply.metadata["channel_id"] == fx.CHANNEL
        assert reply.metadata["slack_user_id"] == "U0ABCDE1"

    def test_the_root_author_is_carried_so_the_thread_can_be_joined(
        self, reply,
    ):
        assert reply.metadata["parent_user_id"] == "U0ABCDEF"
        assert "U0ABCDEF" in [ref.actor_id for ref in reply.actor_refs]

    def test_a_root_is_not_reported_as_a_reply_to_itself(self, bindings):
        """Slack sets ``thread_ts == ts`` on a root that has replies.
        Reading that as a reply would give every thread a phantom
        parent."""
        root = fx.thread_root()
        assert root["thread_ts"] == root["ts"]
        envelope = _only(normalize_slack_message(
            root, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert envelope.metadata["is_thread_reply"] is False
        assert envelope.metadata["thread_root_source_id"] is None

    def test_a_root_and_its_reply_are_two_facts(self, bindings):
        outcome = normalize_slack_payloads(
            messages=[fx.thread_root(), fx.thread_reply()],
            bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        )
        keys = {e.idempotency_key for e in outcome.envelopes}
        assert len(keys) == 2
        assert outcome.envelopes[0].observed_at < (
            outcome.envelopes[1].observed_at
        )


# ── Edits: new versioned evidence, never an overwrite ────────────────


class TestEdits:
    """The behaviour a chat client cannot have. A bitemporal,
    append-only store that overwrote on edit would make every
    historical answer silently change the next time somebody fixed a
    typo."""

    @pytest.fixture
    def edit(self, bindings):
        return _only(normalize_slack_message_changed(
            fx.message_changed(), bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        ))

    @pytest.fixture
    def original(self, bindings):
        return _only(normalize_slack_message(
            fx.thread_root(), bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))

    def test_the_edit_is_dated_to_the_edit(self, edit):
        """The provider's own edit timestamp. The message's ts is when
        the *first* version was said and is never a candidate."""
        assert edit.observed_at == EDITED_AT

    def test_both_versions_survive_with_distinct_keys(
        self, original, edit,
    ):
        assert original.idempotency_key != edit.idempotency_key
        assert original.observed_at != edit.observed_at

    def test_v2_at_nine_and_v3_at_twenty_past(self, original, edit):
        assert "v2 import path" in original.content
        assert "v3 import path" in edit.content

    def test_a_query_as_of_nine_ten_still_sees_v2(self, original, edit):
        """The whole reason an edit is a new fact. Overwriting would
        make this question unanswerable, and worse, answerable with the
        wrong answer."""
        as_of = datetime(2026, 2, 25, 9, 10)
        visible = [
            envelope for envelope in (original, edit)
            if envelope.observed_at <= as_of
        ]
        assert [e.content for e in visible] == [original.content]
        assert "v3" not in visible[0].content

    def test_a_query_as_of_nine_thirty_sees_v3(self, original, edit):
        as_of = datetime(2026, 2, 25, 9, 30)
        visible = sorted(
            (e for e in (original, edit) if e.observed_at <= as_of),
            key=lambda e: e.observed_at,
        )
        assert len(visible) == 2
        assert "v3 import path" in visible[-1].content

    def test_the_edit_keeps_the_message_identity(self, original, edit):
        """Same source id on purpose: the edit is a new fact about the
        same message, and a source id that moved would sever the two."""
        assert edit.source_id == original.source_id
        assert edit.metadata["message_ts"] == original.metadata["message_ts"]

    def test_the_edit_says_it_is_a_revision(self, edit):
        assert edit.metadata["is_revision"] is True
        assert edit.metadata["message_version"] == (
            fx.message_changed()["message"]["edited"]["ts"]
        )
        assert edit.metadata["previous_version_ts"] == (
            fx.message_changed()["previous_message"]["ts"]
        )

    def test_the_pre_edit_text_is_not_restated(self, edit):
        """It is already in the journal as the fact it was. Copying it
        here would duplicate evidence and invite a reader to treat one
        utterance as two."""
        assert "v2 import path" not in (edit.content or "")

    def test_the_edit_keeps_its_thread(self, edit):
        assert edit.metadata["thread_ts"] == fx.thread_root()["ts"]

    def test_an_edit_naming_no_message_is_rejected(self, bindings):
        payload = copy.deepcopy(fx.message_changed())
        payload["message"].pop("ts")
        outcome = normalize_slack_message_changed(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        )
        assert outcome.envelopes == []
        assert "names no message" in outcome.rejected[0].reason

    def test_an_edit_with_no_edit_time_falls_back_to_the_event_time(
        self, bindings,
    ):
        """Stated as an order of preference rather than a default: the
        provider's edit time, then the event's. Never ``now``."""
        payload = copy.deepcopy(fx.message_changed())
        payload["message"]["edited"].pop("ts")
        envelope = _only(normalize_slack_message_changed(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        ))
        assert envelope.observed_at == EDITED_AT

    def test_an_edit_with_no_time_at_all_is_rejected(self, bindings):
        payload = copy.deepcopy(fx.message_changed())
        payload["message"]["edited"].pop("ts")
        payload.pop("event_ts")
        payload.pop("ts")
        outcome = normalize_slack_message_changed(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        )
        assert outcome.envelopes == []
        assert "not a substitute" in outcome.rejected[0].reason

    def test_an_already_edited_polled_message_keys_on_its_edit(
        self, bindings,
    ):
        """Re-reading history returns the post-edit text under the
        original ts. Without the edit marker in the key that new text
        would deduplicate against the old fact and vanish."""
        edited = next(
            m for m in fx.load("messages") if m.get("edited")
        )
        unedited = _message(edited)
        unedited.pop("edited")
        with_edit = _only(normalize_slack_message(
            edited, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        without = _only(normalize_slack_message(
            unedited, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert with_edit.idempotency_key != without.idempotency_key
        assert with_edit.metadata["message_version"] == (
            edited["edited"]["ts"]
        )
        assert without.metadata["message_version"] == "v0"


# ── Deletions: a source went away ────────────────────────────────────


class TestDeletions:
    @pytest.fixture
    def deletion(self, bindings):
        return _only(normalize_slack_message_deleted(
            fx.message_deleted(), bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        ))

    def test_a_deletion_appends_rather_than_erases(self, deletion):
        """Somebody deleting a message in Slack cannot un-say what the
        organization already read and acted on."""
        assert deletion.event_type == AVAILABILITY_EVENT_CLASS
        assert deletion.source_type == "source_availability"

    def test_it_is_dated_to_the_deletion_not_the_message(self, deletion):
        """Confusing the two would date the deletion to when the
        message was said, which is the one thing this event is about
        not doing."""
        assert deletion.observed_at == DELETED_AT
        assert deletion.observed_at > SAID_AT

    def test_it_names_the_evidence_whose_source_is_gone(self, deletion):
        assert deletion.metadata["source_availability"] == (
            SOURCE_AVAILABILITY_DELETED
        )
        assert deletion.metadata["unavailable_source_id"] == (
            f"{fx.WORKSPACE}/{fx.CHANNEL}:{fx.thread_root()['ts']}"
        )
        assert deletion.metadata["deleted_message_ts"] == (
            fx.thread_root()["ts"]
        )

    def test_it_carries_no_text(self, deletion):
        """Slack puts the deleted message under ``previous_message``. A
        layer whose job is to respect a deletion has no business
        re-journalling the text as new content; the original fact is
        already in the journal."""
        assert deletion.content is None
        assert "import path" not in (deletion.title or "")

    def test_it_cannot_move_anything_on_its_own(self, deletion):
        """The event class is the evidence-only one deliberately. The
        organizational reading lives in the availability metadata, which
        the durability seam acts on; a class that triggered a recompute
        would let a deletion revise a decision."""
        from tpllm.integrations.runtime import (
            RefreshScope, determine_refresh_scope,
        )

        assert determine_refresh_scope(deletion) == {
            RefreshScope.EVIDENCE_ONLY
        }

    def test_the_availability_value_mirrors_the_persistence_enum(self):
        """``integrations`` is a domain and no domain may import
        ``persistence``, so the value is mirrored rather than imported.
        A test can import both, which is what keeps the mirror honest."""
        from tpllm.persistence.models import SourceAvailability

        assert SOURCE_AVAILABILITY_DELETED == SourceAvailability.DELETED.value

    def test_two_deliveries_of_one_deletion_are_one_fact(self, bindings):
        """The one key that deliberately ignores the delivery id: the
        fact is about a subject, and a message cannot be deleted twice.
        Keying on the delivery would let a retry under a new id become a
        second availability change."""
        first = _only(normalize_slack_message_deleted(
            fx.message_deleted(), bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, delivery_id="Ev0AAAAAAA",
        ))
        second = _only(normalize_slack_message_deleted(
            fx.message_deleted(), bindings=bindings, received_at=LATER,
            workspace_id=fx.WORKSPACE, delivery_id="Ev0BBBBBBB",
        ))
        assert first.idempotency_key == second.idempotency_key
        assert first.idempotency_key == slack_availability_key(
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
            deleted_ts=fx.thread_root()["ts"],
        )

    def test_a_deletion_naming_no_message_is_rejected(self, bindings):
        payload = copy.deepcopy(fx.message_deleted())
        payload.pop("deleted_ts")
        outcome = normalize_slack_message_deleted(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        )
        assert outcome.envelopes == []
        assert "names no message" in outcome.rejected[0].reason

    def test_a_deletion_with_no_time_is_rejected(self, bindings):
        payload = copy.deepcopy(fx.message_deleted())
        payload.pop("ts")
        payload.pop("event_ts")
        outcome = normalize_slack_message_deleted(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        )
        assert outcome.envelopes == []
        assert outcome.rejected


# ── Channel topic and purpose ────────────────────────────────────────


class TestChannelScope:
    """Supported, unlike most subtypes, because a topic is not an
    utterance: it is the one text the whole channel shares and that
    somebody deliberately replaced, and in practice it carries
    declarations."""

    @pytest.fixture
    def topic(self, bindings):
        return _only(normalize_slack_channel_scope(
            fx.channel_scope("channel_topic"), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
        ))

    def test_a_topic_change_is_a_shared_statement_changing(self, topic):
        assert topic.event_type == CHANNEL_SCOPE_EVENT_CLASS
        assert topic.event_type == "document_updated"

    def test_it_reaches_project_understanding_and_no_further(self, topic):
        """The right reach for a declared scope change: it should inform
        what we think the project is, and should not by itself move a
        KPI or a launch gate."""
        from tpllm.integrations.runtime import (
            RefreshScope, determine_refresh_scope,
        )

        assert determine_refresh_scope(topic) == {
            RefreshScope.EVIDENCE_ONLY, RefreshScope.PROJECT_UNDERSTANDING,
        }

    def test_the_declared_text_is_the_content_not_the_rendered_sentence(
        self, topic,
    ):
        """``text`` is Slack's rendering — "<@U0ABCDEF> set the channel
        topic: ..." — which is prose about the change. The declaration
        itself is in ``topic``."""
        assert topic.content == fx.channel_scope("channel_topic")["topic"]
        assert "set the channel topic" not in topic.content

    def test_a_purpose_change_is_the_same_shape(self, bindings):
        envelope = _only(normalize_slack_channel_scope(
            fx.channel_scope("channel_purpose"), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
        ))
        assert envelope.event_type == CHANNEL_SCOPE_EVENT_CLASS
        assert envelope.metadata["slack_subtype"] == "channel_purpose"

    def test_a_topic_and_a_purpose_are_not_one_fact(self, bindings):
        outcome = NormalizationOutcome()
        for subtype in ("channel_topic", "channel_purpose"):
            outcome = outcome.merge(normalize_slack_channel_scope(
                fx.channel_scope(subtype), bindings=bindings,
                received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            ))
        assert len({e.idempotency_key for e in outcome.envelopes}) == 2

    def test_a_cleared_topic_asserts_nothing(self, bindings):
        payload = _message(fx.channel_scope("channel_topic"), topic="")
        outcome = normalize_slack_channel_scope(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        )
        assert outcome.envelopes == []
        assert "asserts nothing" in outcome.uninterpreted[0].reason

    def test_a_channel_rename_is_still_not_read(self, bindings):
        """A topic is text someone wrote; a name is a label a project is
        never inferred from. The distinction has to hold here or the
        binding invariant is decorative."""
        payload = _message(
            fx.channel_scope("channel_topic"), subtype="channel_name",
        )
        outcome = normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        )
        assert outcome.envelopes == []
        assert "non-event" in outcome.uninterpreted[0].reason

    def test_a_topic_change_routes_from_the_message_entry_point(
        self, bindings,
    ):
        """It arrives as a message subtype, so the message normalizer
        has to hand it on rather than decline it."""
        envelope = _only(normalize_slack_message(
            fx.channel_scope("channel_topic"), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
        ))
        assert envelope.event_type == CHANNEL_SCOPE_EVENT_CLASS


# ── The Events API wrapper ───────────────────────────────────────────


class TestEventsApi:
    def test_a_channel_message_event_becomes_evidence(self, bindings):
        envelope = _only(normalize_slack_event(
            fx.event("message"), bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.event_type == MESSAGE_EVENT_CLASS
        assert envelope.metadata["channel_id"] == fx.CHANNEL

    def test_the_key_is_slacks_own_event_id(self, bindings):
        """Unchanged across a retry of the same delivery, which is
        exactly what a key has to mean."""
        envelope = _only(normalize_slack_event(
            fx.event("message"), bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.idempotency_key == (
            f"slack:event:Ev0ABCDEF1:{MESSAGE_EVENT_CLASS}"
        )

    def test_a_retried_delivery_is_one_fact(self, bindings):
        """Slack retries with the same event_id. An hour later, a
        different received_at, and the key and the envelope id must be
        byte-identical."""
        first = _only(normalize_slack_event(
            fx.event("message"), bindings=bindings, received_at=RECEIVED,
        ))
        second = _only(normalize_slack_event(
            fx.event("message"), bindings=bindings, received_at=LATER,
        ))
        assert first.idempotency_key == second.idempotency_key
        assert first.id == second.id
        assert first.observed_at == second.observed_at

    def test_a_callback_with_no_event_id_is_rejected(self, bindings):
        payload = copy.deepcopy(fx.event("message"))
        payload.pop("event_id")
        outcome = normalize_slack_event(
            payload, bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.envelopes == []
        assert "cannot be deduplicated" in outcome.rejected[0].reason

    def test_a_url_verification_is_not_evidence(self, bindings):
        outcome = normalize_slack_event(
            {
                "type": "url_verification", "event_id": "Ev0ABCDEF9",
                "challenge": "abc",
            },
            bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.envelopes == []
        assert "not an event callback" in outcome.uninterpreted[0].reason

    def test_an_edit_event_routes_to_the_edit_normalizer(self, bindings):
        callback = next(
            c for c in fx.load("events")
            if (c.get("event") or {}).get("subtype") == "message_changed"
        )
        envelope = _only(normalize_slack_event(
            callback, bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.metadata["is_revision"] is True
        assert "4.4" in envelope.content

    def test_a_thread_reply_event_keeps_its_thread(self, bindings):
        callback = next(
            c for c in fx.load("events")
            if (c.get("event") or {}).get("thread_ts")
        )
        envelope = _only(normalize_slack_event(
            callback, bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.metadata["is_thread_reply"] is True

    def test_the_verification_token_never_reaches_an_envelope(
        self, bindings,
    ):
        """The deprecated wrapper field is a shared secret. It is not
        read, not declared in the contract, and would be refused by the
        envelope model if a future edit tried to carry it."""
        payload = copy.deepcopy(fx.event("message"))
        payload["token"] = "xoxb-not-a-real-token-0123456789"
        envelope = _only(normalize_slack_event(
            payload, bindings=bindings, received_at=RECEIVED,
        ))
        serialized = envelope.model_dump_json()
        assert "xoxb-not-a-real-token" not in serialized
        assert not any("token" in key for key in envelope.metadata)

    def test_no_slack_fixture_ships_a_token_field(self):
        for name in fx.fixture_names():
            raw = (fx.FIXTURE_DIR / f"{name}.json").read_text()
            assert '"token"' not in raw, (
                f"{name} carries a token field; a committed fixture must "
                f"hold no credential-shaped value"
            )


# ── What this layer declines to interpret ────────────────────────────


class TestDeclines:
    """Every decline is reported with a reason. A normalizer that
    silently discarded what it does not understand would make "no
    evidence" and "evidence we could not read" indistinguishable."""

    def test_a_reaction_is_not_organizational_truth(self, bindings):
        """The reason matters more than the behaviour. A reaction has no
        text, so it asserts nothing checkable, and the only thing it can
        be turned into is a count per person — which is a responsiveness
        metric, and this system does not measure people."""
        outcome = normalize_slack_event(
            fx.event("reaction_added"), bindings=bindings,
            received_at=RECEIVED,
        )
        assert outcome.envelopes == []
        reason = outcome.uninterpreted[0].reason
        assert "does not measure people" in reason
        assert "manufacture a decision out of a gesture" in reason

    def test_declining_a_reaction_is_not_a_gap_needing_attention(
        self, bindings,
    ):
        outcome = normalize_slack_event(
            fx.event("reaction_added"), bindings=bindings,
            received_at=RECEIVED,
        )
        assert not outcome.needs_attention, (
            "declining to interpret a reaction is the designed behaviour, "
            "not something for a person to fix"
        )

    def test_a_bot_message_is_an_echo_of_a_real_source(self, bindings):
        outcome = normalize_slack_message(
            fx.declined("bot_message"), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert "count the same fact twice" in outcome.uninterpreted[0].reason

    def test_an_undeclared_bot_post_is_declined_too(self, bindings):
        """A post with a ``bot_id`` and no ``user`` is a bot whatever
        subtype it claims; the same double-counting applies."""
        payload = _message(fx.thread_root(), bot_id="B0ABCDEF")
        payload.pop("user")
        outcome = normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert "count the same fact twice" in outcome.uninterpreted[0].reason

    def test_a_channel_join_belongs_to_the_identity_layer(self, bindings):
        outcome = normalize_slack_message(
            fx.declined("channel_join"), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert "identity layer" in outcome.uninterpreted[0].reason

    def test_a_direct_message_is_never_read(self, bindings):
        """Private correspondence between two people. An agent that
        journalled it would be surveilling them, and no organizational
        question needs it."""
        payload = _message(fx.thread_root(), channel_type="im")
        outcome = normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert "surveilling" in outcome.uninterpreted[0].reason

    def test_a_group_direct_message_is_never_read(self, bindings):
        payload = _message(fx.thread_root(), channel_type="mpim")
        outcome = normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert outcome.uninterpreted

    def test_an_unknown_subtype_is_reported_not_coerced(self, bindings):
        payload = _message(fx.thread_root(), subtype="something_new")
        outcome = normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes == []
        assert "no declared mapping" in outcome.uninterpreted[0].reason

    def test_an_unknown_event_type_is_reported_not_coerced(self, bindings):
        payload = copy.deepcopy(fx.event("message"))
        payload["event"]["type"] = "something_slack_adds_in_2027"
        outcome = normalize_slack_event(
            payload, bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.envelopes == []
        assert "never invented" in outcome.uninterpreted[0].reason

    def test_every_decline_says_why(self, bindings):
        from tpllm.integrations.slack_normalizer import (
            _DECLINED_CHANNEL_TYPES,
            _DECLINED_EVENT_TYPES,
            _DECLINED_SUBTYPES,
        )

        for table in (
            _DECLINED_SUBTYPES, _DECLINED_CHANNEL_TYPES,
            _DECLINED_EVENT_TYPES,
        ):
            for key, reason in table.items():
                assert len(reason) > 40, (
                    f"'{key}' is declined without a reason a reader could "
                    f"evaluate; a bare refusal is indistinguishable from an "
                    f"oversight"
                )


# ── Idempotency ──────────────────────────────────────────────────────


class TestIdempotency:
    def test_the_same_polled_message_an_hour_later_is_the_same_fact(
        self, bindings,
    ):
        """The property the whole key design exists for, asserted
        directly: a key that moved with our clock would turn one message
        into an unbounded stream of messages."""
        first = _only(normalize_slack_message(
            fx.thread_root(), bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        second = _only(normalize_slack_message(
            fx.thread_root(), bindings=bindings, received_at=LATER,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert first.idempotency_key == second.idempotency_key
        assert first.id == second.id
        assert first.observed_at == second.observed_at
        assert first.received_at != second.received_at, (
            "received_at is knowledge time and is expected to move; it is "
            "the key that must not"
        )

    def test_the_key_has_no_clock_in_it(self):
        key = slack_message_key(
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
            ts=fx.thread_root()["ts"], event_class=MESSAGE_EVENT_CLASS,
        )
        assert key == (
            f"slack:message:{fx.SCOPE}:{fx.thread_root()['ts']}:v0:"
            f"chat_message"
        )
        for stamp in (
            RECEIVED.isoformat(), LATER.isoformat(), str(RECEIVED.year),
        ):
            assert stamp not in key

    def test_the_key_takes_no_received_at_parameter(self):
        """Enforced at the signature, so a future edit cannot quietly
        fold the fetch time in."""
        import inspect

        parameters = inspect.signature(slack_message_key).parameters
        assert "received_at" not in parameters
        assert "now" not in parameters

    def test_the_key_is_not_a_hash_of_the_text(self, bindings):
        """Hashing the body would make two people who said the same
        sentence one fact, and a whitespace fix a new one."""
        original = fx.thread_root()
        reworded = _message(original, text="Completely different words.")
        first = _only(normalize_slack_message(
            original, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        second = _only(normalize_slack_message(
            reworded, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert first.idempotency_key == second.idempotency_key, (
            "the same message slot is the same message; a text change "
            "without an edit marker is not a new fact"
        )

    def test_a_delivery_id_wins_over_the_polled_form(self):
        delivered = slack_message_key(
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
            ts="1772010000.000100", event_class=MESSAGE_EVENT_CLASS,
            delivery_id="Ev0ABCDEF1",
        )
        assert delivered == f"slack:event:Ev0ABCDEF1:{MESSAGE_EVENT_CLASS}"

    def test_the_event_class_is_part_of_the_key(self):
        """One message slot legitimately produces a chat message and, if
        it is a topic change, a document update. Those are two
        organizational facts rather than two versions of one."""
        assert slack_message_key(
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
            ts="1772010000.000100", event_class=MESSAGE_EVENT_CLASS,
        ) != slack_message_key(
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
            ts="1772010000.000100", event_class=CHANNEL_SCOPE_EVENT_CLASS,
        )

    def test_every_fact_in_a_batch_has_its_own_key(self, bindings):
        outcome = normalize_slack_payloads(
            messages=fx.load("messages") + fx.load("declined_messages"),
            events=fx.load("events"), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        keys = [e.idempotency_key for e in outcome.envelopes]
        assert len(keys) == len(set(keys)), (
            "two distinct facts sharing a key would silently deduplicate "
            "to one at the journal boundary"
        )


# ── Secrets ──────────────────────────────────────────────────────────


class TestSecretsNeverReachEvidence:
    """Chat is where credentials get pasted. The journal is append-only,
    so one that reaches it is permanent."""

    @pytest.mark.parametrize(
        "secret,label",
        [
            ("ghp_abcdefghijklmnopqrstuvwxyz0123", "a GitHub token"),
            ("Bearer abcdefghijklmnopqrstuvwxyz012345", "a bearer token"),
            ("xoxb-1234567890-abcdefghijklmnop", "a Slack bot token"),
            ("sk-abcdefghijklmnopqrstuvwxyz", "an API key"),
            ("AKIAIOSFODNN7EXAMPLE", "an AWS access key id"),
        ],
    )
    def test_a_credential_in_a_message_does_not_survive(
        self, bindings, secret, label,
    ):
        payload = _message(
            fx.thread_root(),
            text=f"use {secret} to reproduce the import failure",
        )
        envelope = _only(normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        serialized = envelope.model_dump_json()
        assert secret not in serialized, f"{label} reached the envelope"

    def test_the_sentence_around_the_credential_survives(self, bindings):
        """Only the credential is removed. The sentence is the
        evidence."""
        payload = _message(
            fx.thread_root(),
            text="the import fails with ghp_abcdefghijklmnopqrstuvwxyz0123",
        )
        envelope = _only(normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert "the import fails with" in envelope.content
        assert "[redacted-credential]" in envelope.content

    def test_the_redaction_is_declared(self, bindings):
        """An operator reading this evidence should know the excerpt is
        not verbatim, and why."""
        payload = _message(
            fx.thread_root(), text="token: xoxb-1234567890-abcdefghijklmnop",
        )
        envelope = _only(normalize_slack_message(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE, channel_id=fx.CHANNEL,
        ))
        assert envelope.metadata["redaction_applied"] is True

    def test_a_credential_in_a_channel_topic_does_not_survive(
        self, bindings,
    ):
        payload = _message(
            fx.channel_scope("channel_topic"),
            topic="staging creds: xoxb-1234567890-abcdefghijklmnop",
        )
        envelope = _only(normalize_slack_channel_scope(
            payload, bindings=bindings, received_at=RECEIVED,
            workspace_id=fx.WORKSPACE,
        ))
        assert "xoxb-1234567890" not in envelope.model_dump_json()


# ── The closed event-class vocabulary ────────────────────────────────


class TestEmittedEventClasses:
    """A class outside the vocabulary the stack maps would reach the
    temporal layer as an unrecognized string and degrade silently to
    generic observed evidence."""

    EMITTED = (
        MESSAGE_EVENT_CLASS, AVAILABILITY_EVENT_CLASS,
        CHANNEL_SCOPE_EVENT_CLASS,
    )

    @pytest.mark.parametrize("event_class", sorted(set(EMITTED)))
    def test_every_emitted_class_is_recognized(self, event_class):
        assert event_class in RECOGNIZED_EVENT_CLASSES

    @pytest.mark.parametrize("event_class", sorted(set(EMITTED)))
    def test_every_emitted_class_is_actually_mapped(self, event_class):
        from tpllm.integrations.runtime import _EVENT_SCOPE_MAP
        from tpllm.runtime.sources import EVENT_CLASS_TO_TEMPORAL_TYPE

        assert (
            event_class in EVENT_CLASS_TO_TEMPORAL_TYPE
            or event_class in _EVENT_SCOPE_MAP
        ), (
            f"'{event_class}' is in the recognized set but nothing maps it, "
            f"so it would land as generic observed evidence"
        )

    def test_the_whole_fixture_corpus_emits_nothing_else(self, bindings):
        outcome = normalize_slack_payloads(
            messages=(
                fx.load("messages") + fx.load("declined_messages")
                + fx.load("channel_scope") + fx.load("message_changed")
                + fx.load("message_deleted")
            ),
            events=fx.load("events"), bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        assert outcome.envelopes
        assert {e.event_type for e in outcome.envelopes} <= set(self.EMITTED)


# ── The uniform registry entry point ─────────────────────────────────


class TestNormalizePayload:
    @pytest.mark.parametrize("kind", [
        "message", "message_changed", "message_deleted", "channel_scope",
        "event", "webhook",
    ])
    def test_every_declared_kind_is_dispatched(self, kind, bindings):
        payload = {
            "message": fx.thread_root(),
            "message_changed": fx.message_changed(),
            "message_deleted": fx.message_deleted(),
            "channel_scope": fx.channel_scope("channel_topic"),
            "event": fx.event("message"),
            "webhook": fx.event("message"),
        }[kind]
        outcome = normalize_payload(
            payload_kind=kind, payload=payload, bindings=bindings,
            received_at=RECEIVED, scope=fx.SCOPE,
            delivery_id="Ev0ABCDEF1" if kind == "webhook" else None,
        )
        assert outcome.envelopes, (
            f"'{kind}' is declared in SLACK_PAYLOAD_KINDS but produced "
            f"nothing: {outcome.model_dump()}"
        )

    def test_the_declared_kinds_are_exactly_the_dispatched_ones(self):
        assert set(SLACK_PAYLOAD_KINDS) == {
            "message", "message_changed", "message_deleted",
            "channel_scope", "event", "webhook",
        }

    def test_an_unknown_kind_is_rejected_not_guessed(self, bindings):
        outcome = normalize_payload(
            payload_kind="huddle", payload={}, bindings=bindings,
            received_at=RECEIVED, scope=fx.SCOPE,
        )
        assert outcome.envelopes == []
        assert len(outcome.rejected) == 1
        assert "would invent a fact" in outcome.rejected[0].reason
        assert outcome.rejected[0].external_scope == fx.SCOPE, (
            "whoever reads the report needs to know which channel it was"
        )

    def test_a_webhook_with_no_delivery_id_is_rejected(self, bindings):
        outcome = normalize_payload(
            payload_kind="webhook", payload=fx.event("message"),
            bindings=bindings, received_at=RECEIVED, event_name="message",
        )
        assert outcome.envelopes == []
        assert "not optional" in outcome.rejected[0].reason

    def test_the_signature_matches_the_github_provider(self):
        """The registry imports providers by a dotted path and calls them
        identically; a drifted signature would break at runtime in the
        CLI rather than here."""
        import inspect

        from tpllm.integrations.normalizers import (
            normalize_payload as github_normalize_payload,
        )

        assert inspect.signature(normalize_payload) == inspect.signature(
            github_normalize_payload
        )


# ── Nothing is dropped ───────────────────────────────────────────────


class TestNothingIsSilentlyDiscarded:
    def test_every_payload_becomes_an_envelope_or_a_report(self, bindings):
        messages = (
            fx.load("messages") + fx.load("declined_messages")
            + fx.load("message_changed") + fx.load("message_deleted")
        )
        events = fx.load("events")
        outcome = normalize_slack_payloads(
            messages=messages, events=events, bindings=bindings,
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        assert len(outcome.envelopes) + outcome.skipped_count == (
            len(messages) + len(events)
        ), (
            "a payload that produced no envelope and no report would be "
            "silently discarded, making 'no evidence' and 'evidence we "
            "could not read' indistinguishable"
        )

    def test_the_batch_reports_what_needs_a_person(self, bindings):
        outcome = normalize_slack_payloads(
            messages=fx.load("messages"), bindings=fx.unbound_bindings(),
            received_at=RECEIVED, workspace_id=fx.WORKSPACE,
            channel_id=fx.CHANNEL,
        )
        assert outcome.needs_attention
        assert len(outcome.unbound) == len(fx.load("messages"))
