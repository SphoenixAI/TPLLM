"""Tests for Commit 37 — the anti-corruption layer.

These run against payloads recorded from the live GitHub API, so what
they pin is the behaviour on real shapes rather than on a mock written
to agree with the code.

Four properties matter more than the mappings themselves, and each one
exists because the alternative is a specific kind of false statement:

- **A key never moves with our clock.** Published webhook guidance is
  unanimous that the idempotency key must be the provider's own event
  id, not a timestamp or a payload hash, because the same delivery can
  arrive with a different timestamp. A key that drifted would turn one
  merge into an unbounded stream of merges.
- **Reality time is the time of the fact.** A pull request merged in
  February and polled in October occurred in February. Stamping it
  ``now`` would corrupt every scenario rooted in it.
- **Nothing is guessed.** An unbound repository, an unparseable
  timestamp and an uninterpretable event each produce a report, never a
  plausible-looking envelope.
- **Nothing leaks.** Not a credential pasted into a pull request body,
  and not anything describing what a person did with their time.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from lltpm.integrations.normalizers import (
    CONTENT_EXCERPT_LIMIT,
    GITHUB,
    RECOGNIZED_EVENT_CLASSES,
    NormalizationOutcome,
    ProjectBindings,
    SourceBinding,
    TimestampMissingError,
    github_pull_request_key,
    normalize_github_event,
    normalize_github_issue,
    normalize_github_payloads,
    normalize_github_pull_request,
    normalize_github_review,
    normalize_github_webhook,
    to_naive_utc,
)
from tests import _github_fixtures as fx

RECEIVED = datetime(2026, 10, 21, 4, 40)
LATER = datetime(2026, 10, 22, 9, 15)


@pytest.fixture
def bindings():
    return fx.bindings()


def _only(outcome: NormalizationOutcome):
    assert len(outcome.envelopes) == 1, (
        f"expected one envelope, got {len(outcome.envelopes)}; "
        f"skipped={outcome.skipped_count}"
    )
    return outcome.envelopes[0]


# ── Time at the boundary ─────────────────────────────────────────────


class TestTimestampConversion:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("2026-02-25T05:45:39Z", datetime(2026, 2, 25, 5, 45, 39)),
            ("2026-02-25T05:45:39+00:00", datetime(2026, 2, 25, 5, 45, 39)),
            ("2026-02-25T07:45:39+02:00", datetime(2026, 2, 25, 5, 45, 39)),
            ("2026-02-25T05:45:39", datetime(2026, 2, 25, 5, 45, 39)),
        ],
    )
    def test_provider_timestamps_become_naive_utc(self, text, expected):
        assert to_naive_utc(text) == expected

    def test_an_aware_datetime_is_converted_not_rejected(self):
        aware = datetime(2026, 2, 25, 5, 45, 39, tzinfo=timezone.utc)
        assert to_naive_utc(aware) == datetime(2026, 2, 25, 5, 45, 39)

    def test_the_result_is_comparable_with_the_runtime_clock(self):
        """The bug this prevents: the runtime's clock is naive UTC, every
        provider emits an offset, and mixing them raises TypeError on the
        first comparison — which happens inside temporal ordering, far
        from the cause."""
        normalized = to_naive_utc("2026-02-25T05:45:39Z")
        assert normalized < datetime.now()  # would raise if tz-aware

    @pytest.mark.parametrize("bad", [None, "", "   ", "not-a-time", 12345])
    def test_an_unusable_timestamp_is_refused(self, bad):
        with pytest.raises(TimestampMissingError):
            to_naive_utc(bad)

    def test_the_current_time_is_never_substituted(self, bindings):
        """CloudEvents permits a producer to use the current time when the
        occurrence time is unknown. Here that substitution would record a
        February merge as having happened today, so it is refused."""
        pull = copy.deepcopy(fx.merged_pull_request())
        pull["merged_at"] = None
        pull["closed_at"] = None
        pull["updated_at"] = None
        pull["created_at"] = None
        outcome = normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        )
        assert not outcome.envelopes
        assert outcome.rejected
        assert "timestamp" in outcome.rejected[0].reason


# ── Bindings ─────────────────────────────────────────────────────────


class TestBindings:
    def test_a_declared_repository_resolves_to_its_projects(self, bindings):
        assert bindings.projects_for(
            source_system=GITHUB, external_scope=fx.REPOSITORY,
        ) == [fx.PROJECT]

    def test_lookup_ignores_case_because_providers_do(self, bindings):
        assert bindings.projects_for(
            source_system=GITHUB, external_scope="ACME/Atlas-Service",
        ) == [fx.PROJECT]

    def test_an_undeclared_repository_resolves_to_nothing(self, bindings):
        assert bindings.projects_for(
            source_system=GITHUB, external_scope="acme/unknown",
        ) is None

    def test_bound_to_no_projects_differs_from_not_bound(self):
        bindings = ProjectBindings(bindings=[
            SourceBinding(
                source_system=GITHUB, external_scope="acme/quiet",
                project_ids=[],
            )
        ])
        assert bindings.projects_for(
            source_system=GITHUB, external_scope="acme/quiet",
        ) == []
        assert bindings.projects_for(
            source_system=GITHUB, external_scope="acme/other",
        ) is None

    def test_a_binding_for_another_source_system_does_not_apply(self):
        bindings = ProjectBindings.from_mapping(
            source_system="jira", mapping={fx.REPOSITORY: ["atlas"]},
        )
        assert bindings.projects_for(
            source_system=GITHUB, external_scope=fx.REPOSITORY,
        ) is None

    def test_an_unbound_payload_is_reported_not_filed_somewhere(self):
        """The identity invariant applied to scope: an ambiguous link is
        left for a person rather than guessed."""
        outcome = normalize_github_pull_request(
            fx.merged_pull_request(),
            bindings=ProjectBindings(), received_at=RECEIVED,
            repository=fx.REPOSITORY,
        )
        assert not outcome.envelopes
        assert len(outcome.unbound) == 1
        assert outcome.unbound[0].external_scope == fx.REPOSITORY
        assert outcome.needs_attention


# ── Pull requests ────────────────────────────────────────────────────


class TestPullRequests:
    def test_a_merged_pull_request_is_merged_code_evidence(self, bindings):
        pull = fx.merged_pull_request()
        envelope = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        assert envelope.event_type == "pr_merged"
        assert envelope.observed_at == to_naive_utc(pull["merged_at"])

    def test_reality_time_is_the_merge_not_the_poll(self, bindings):
        envelope = _only(normalize_github_pull_request(
            fx.merged_pull_request(), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert envelope.observed_at.year == 2026
        assert envelope.observed_at.month == 2, (
            "a February merge polled in October happened in February"
        )
        assert envelope.received_at == RECEIVED
        assert envelope.observed_at < envelope.received_at

    def test_an_open_pull_request_requests_a_review(self, bindings):
        envelope = _only(normalize_github_pull_request(
            fx.variant(fx.OPEN_PULL_REQUEST), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert envelope.event_type == "action_requested"

    def test_a_draft_requests_nothing_and_is_uninterpreted(self, bindings):
        outcome = normalize_github_pull_request(
            fx.variant(fx.DRAFT_PULL_REQUEST), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        )
        assert not outcome.envelopes
        assert outcome.uninterpreted
        assert not outcome.needs_attention, (
            "declining to interpret a draft is the design, not a gap"
        )

    def test_closed_without_merging_resolves_the_request(self, bindings):
        envelope = _only(normalize_github_pull_request(
            fx.variant(fx.CLOSED_UNMERGED_PULL_REQUEST), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert envelope.event_type == "action_resolved"

    def test_a_merged_draft_is_still_a_merge(self, bindings):
        """Draft status is about whether review is being asked for. If it
        merged, it merged."""
        pull = copy.deepcopy(fx.merged_pull_request())
        pull["draft"] = True
        envelope = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        assert envelope.event_type == "pr_merged"

    def test_the_author_is_proposed_as_an_actor_never_resolved(
        self, bindings,
    ):
        envelope = _only(normalize_github_pull_request(
            fx.merged_pull_request(), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert envelope.actor_refs
        assert all(
            ref.canonical_entity_id is None for ref in envelope.actor_refs
        ), (
            "deciding two handles are one person is the identity engine's "
            "job, under a policy that refuses a name resemblance"
        )

    def test_the_repository_is_offered_to_identity_resolution(
        self, bindings,
    ):
        envelope = _only(normalize_github_pull_request(
            fx.merged_pull_request(), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert envelope.metadata["repository"] == fx.REPOSITORY

    def test_a_pull_request_without_a_node_id_is_rejected(self, bindings):
        pull = copy.deepcopy(fx.merged_pull_request())
        pull.pop("node_id")
        outcome = normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        )
        assert outcome.rejected and not outcome.envelopes
        assert outcome.needs_attention

    def test_the_repository_is_read_from_the_payload_when_nested(
        self, bindings,
    ):
        pull = copy.deepcopy(fx.merged_pull_request())
        pull["repository"] = {"full_name": fx.REPOSITORY}
        envelope = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.metadata["repository"] == fx.REPOSITORY


# ── Reviews ──────────────────────────────────────────────────────────


class TestReviews:
    def _normalize(self, state: str, bindings):
        return normalize_github_review(
            fx.review(state), bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY, pull_request_number=8,
        )

    def test_an_approval_supports_readiness_without_deciding(
        self, bindings,
    ):
        envelope = _only(self._normalize("APPROVED", bindings))
        assert envelope.event_type == "supporting_evidence"

    def test_requested_changes_open_a_blocker(self, bindings):
        envelope = _only(self._normalize("CHANGES_REQUESTED", bindings))
        assert envelope.event_type == "blocker_opened"

    @pytest.mark.parametrize("state", ["COMMENTED", "DISMISSED"])
    def test_an_opinion_is_not_a_decision(self, bindings, state):
        """The project states this guardrail in prose in three places.
        This is where it has to actually hold."""
        outcome = self._normalize(state, bindings)
        assert not outcome.envelopes
        assert outcome.uninterpreted

    def test_the_interpretation_is_recorded_beside_the_evidence(
        self, bindings,
    ):
        envelope = _only(self._normalize("CHANGES_REQUESTED", bindings))
        assert "requested changes" in envelope.metadata["interpretation"]

    def test_a_review_is_immutable_so_its_id_is_its_version(
        self, bindings,
    ):
        first = _only(self._normalize("APPROVED", bindings))
        second = _only(normalize_github_review(
            fx.review("APPROVED"), bindings=bindings, received_at=LATER,
            repository=fx.REPOSITORY, pull_request_number=8,
        ))
        assert first.idempotency_key == second.idempotency_key


# ── Issues ───────────────────────────────────────────────────────────


class TestIssues:
    def test_an_open_declared_blocker_opens_a_blocker(self, bindings):
        envelope = _only(normalize_github_issue(
            fx.issue_with_label("blocked"), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert envelope.event_type == "blocker_opened"
        assert envelope.metadata["declared_blocker"] is True

    def test_a_closed_declared_blocker_resolves_it(self, bindings):
        envelope = _only(normalize_github_issue(
            fx.issue_with_label("blocker"), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert envelope.event_type == "blocker_resolved"

    def test_ordinary_closed_work_is_ticket_completion(self, bindings):
        envelope = _only(normalize_github_issue(
            fx.issue_with_label("enhancement"), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert envelope.event_type == "ticket_completed"
        assert envelope.metadata["declared_blocker"] is False

    def test_a_blocker_is_declared_by_label_never_inferred_from_prose(
        self, bindings,
    ):
        """'This is blocking the launch' in a title is a sentence, not a
        declaration. Reading it as one would let any phrasing create an
        organizational blocker."""
        issue = copy.deepcopy(fx.issue_with_label("enhancement"))
        issue["title"] = "This is blocked and blocking the whole launch"
        issue["labels"] = []
        envelope = _only(normalize_github_issue(
            issue, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        assert envelope.event_type == "ticket_completed"
        assert envelope.metadata["declared_blocker"] is False

    def test_the_blocking_vocabulary_is_a_parameter(self, bindings):
        issue = copy.deepcopy(fx.issue_with_label("enhancement"))
        issue["state"] = "open"
        envelope = _only(normalize_github_issue(
            issue, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
            blocking_labels=frozenset({"enhancement"}),
        ))
        assert envelope.event_type == "blocker_opened"

    def test_a_pull_request_returned_by_the_issues_endpoint_is_skipped(
        self, bindings,
    ):
        """GitHub's /issues returns pull requests too. Normalizing both
        would make one merge arrive twice as two kinds of fact."""
        issue = copy.deepcopy(fx.issue_with_label("enhancement"))
        issue["pull_request"] = {"url": "https://api.github.com/..."}
        outcome = normalize_github_issue(
            issue, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        )
        assert not outcome.envelopes
        assert "counted twice" in outcome.uninterpreted[0].reason


# ── The Events API ───────────────────────────────────────────────────


class TestEvents:
    def test_a_push_is_not_an_organizational_fact(self, bindings):
        """Recorded behaviour: every event in the recording is a push, and
        every one is declined. Restraint is the feature."""
        events = fx.load("events")
        assert events, "the recording should contain events"
        outcome = NormalizationOutcome()
        for event in events:
            outcome = outcome.merge(normalize_github_event(
                event, bindings=bindings, received_at=RECEIVED,
            ))
        assert not outcome.envelopes
        assert len(outcome.uninterpreted) == len(events)
        assert not outcome.needs_attention

    def test_an_event_with_no_id_cannot_be_journalled(self, bindings):
        outcome = normalize_github_event(
            {"type": "PullRequestEvent", "repo": {"name": fx.REPOSITORY}},
            bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.rejected
        assert "deduplicated" in outcome.rejected[0].reason

    def test_a_pull_request_event_is_read_as_a_pull_request(
        self, bindings,
    ):
        event = {
            "id": "24634148146", "type": "PullRequestEvent",
            "created_at": "2026-02-25T05:45:40Z",
            "repo": {"name": fx.REPOSITORY},
            "payload": {
                "action": "closed",
                "pull_request": fx.merged_pull_request(),
            },
        }
        envelope = _only(normalize_github_event(
            event, bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.event_type == "pr_merged"
        assert envelope.metadata["github_action"] == "closed"

    def test_an_unmapped_event_type_invents_nothing(self, bindings):
        outcome = normalize_github_event(
            {
                "id": "1", "type": "SponsorshipEvent",
                "repo": {"name": fx.REPOSITORY}, "payload": {},
            },
            bindings=bindings, received_at=RECEIVED,
        )
        assert not outcome.envelopes
        assert "never invented" in outcome.uninterpreted[0].reason

    def test_a_comment_is_evidence_and_no_more(self, bindings):
        event = {
            "id": "9", "type": "IssueCommentEvent",
            "created_at": "2026-02-25T06:00:00Z",
            "repo": {"name": fx.REPOSITORY},
            "payload": {
                "action": "created",
                "issue": {"number": 12},
                "comment": {
                    "id": 555, "body": "I think we should wait.",
                    "created_at": "2026-02-25T06:00:00Z",
                    "user": {"login": "contributor-x"},
                },
            },
        }
        envelope = _only(normalize_github_event(
            event, bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.event_type == "chat_message", (
            "chat_message's refresh scope is evidence-only, so a comment "
            "can inform understanding and never move a decision"
        )


# ── Webhooks ─────────────────────────────────────────────────────────


class TestWebhooks:
    def test_the_delivery_id_becomes_the_key(self, bindings):
        envelope = _only(normalize_github_webhook(
            event_name="pull_request", delivery_id="d-1",
            payload={
                "action": "closed",
                "repository": {"full_name": fx.REPOSITORY},
                "pull_request": fx.merged_pull_request(),
            },
            bindings=bindings, received_at=RECEIVED,
        ))
        assert envelope.idempotency_key.startswith("github:delivery:d-1")

    def test_a_delivery_without_an_id_is_refused(self, bindings):
        outcome = normalize_github_webhook(
            event_name="pull_request", delivery_id="",
            payload={
                "repository": {"full_name": fx.REPOSITORY},
                "pull_request": fx.merged_pull_request(),
            },
            bindings=bindings, received_at=RECEIVED,
        )
        assert outcome.rejected
        assert "X-GitHub-Delivery" in outcome.rejected[0].reason

    def test_an_unmapped_webhook_event_produces_nothing(self, bindings):
        outcome = normalize_github_webhook(
            event_name="star", delivery_id="d-2",
            payload={"repository": {"full_name": fx.REPOSITORY}},
            bindings=bindings, received_at=RECEIVED,
        )
        assert not outcome.envelopes
        assert outcome.uninterpreted


# ── Idempotency: the research-driven invariant ───────────────────────


class TestIdempotencyKeys:
    """Published webhook guidance: key on the provider's own event id,
    not on a payload hash and not on a timestamp, because the same
    delivery can legitimately arrive with a different timestamp."""

    def test_the_key_does_not_move_with_our_clock(self, bindings):
        pull = fx.merged_pull_request()
        first = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        second = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=LATER,
            repository=fx.REPOSITORY,
        ))
        assert first.idempotency_key == second.idempotency_key
        assert first.id == second.id, (
            "the envelope id derives from the key, so it must be stable too"
        )
        assert first.received_at != second.received_at

    def test_a_redelivery_carrying_a_new_timestamp_is_one_event(
        self, bindings,
    ):
        """The exact failure the guidance warns about."""
        payload = {
            "action": "closed",
            "repository": {"full_name": fx.REPOSITORY},
            "pull_request": fx.merged_pull_request(),
        }
        redelivered = copy.deepcopy(payload)
        redelivered["pull_request"]["updated_at"] = "2026-03-01T00:00:00Z"

        first = _only(normalize_github_webhook(
            event_name="pull_request", delivery_id="same-guid",
            payload=payload, bindings=bindings, received_at=RECEIVED,
        ))
        second = _only(normalize_github_webhook(
            event_name="pull_request", delivery_id="same-guid",
            payload=redelivered, bindings=bindings, received_at=LATER,
        ))
        assert first.idempotency_key == second.idempotency_key

    def test_a_genuine_change_is_a_new_fact(self, bindings):
        pull = fx.variant(fx.OPEN_PULL_REQUEST)
        changed = copy.deepcopy(pull)
        changed["updated_at"] = "2026-03-01T00:00:00Z"
        first = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        second = _only(normalize_github_pull_request(
            changed, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        assert first.idempotency_key != second.idempotency_key

    def test_one_pull_request_can_yield_two_distinct_facts(self):
        """A request for review and a later merge are two organizational
        facts, not two versions of one, so the event class is part of the
        key."""
        requested = github_pull_request_key(
            node_id="N", updated_at="t1", event_class="action_requested",
        )
        merged = github_pull_request_key(
            node_id="N", updated_at="t1", event_class="pr_merged",
        )
        assert requested != merged

    def test_a_delivery_id_outranks_the_resource_version(self):
        with_delivery = github_pull_request_key(
            node_id="N", updated_at="t1", event_class="pr_merged",
            delivery_id="guid",
        )
        assert with_delivery == "github:delivery:guid:pr_merged"

    def test_a_resource_with_no_version_is_marked_as_such(self):
        key = github_pull_request_key(
            node_id="N", updated_at=None, event_class="pr_merged",
        )
        assert "unversioned" in key, (
            "an unversioned resource should be visibly unversioned rather "
            "than silently keyed as though it had a version"
        )

    def test_two_repositories_never_collide(self, bindings):
        """The same pull request number in two repositories is two facts."""
        other = ProjectBindings.from_mapping(
            source_system=GITHUB,
            mapping={fx.REPOSITORY: [fx.PROJECT], "acme/other": ["beta"]},
        )
        here = _only(normalize_github_pull_request(
            fx.merged_pull_request(), bindings=other, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        elsewhere = copy.deepcopy(fx.merged_pull_request())
        elsewhere["node_id"] = "NODE-other-repo"
        there = _only(normalize_github_pull_request(
            elsewhere, bindings=other, received_at=RECEIVED,
            repository="acme/other",
        ))
        assert here.idempotency_key != there.idempotency_key
        assert here.source_id != there.source_id


# ── Privacy and secrets ──────────────────────────────────────────────


class TestNothingLeaks:
    def test_a_token_in_a_pull_request_body_does_not_become_history(
        self, bindings,
    ):
        """The journal is append-only, so a credential that reaches it is
        permanent. It is removed here, and the sentence around it — which
        is the actual evidence — survives."""
        pull = copy.deepcopy(fx.merged_pull_request())
        pull["body"] = (
            "Deployed with ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 by hand; "
            "we should automate this."
        )
        envelope = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        assert "ghp_" not in (envelope.content or "")
        assert "we should automate this" in (envelope.content or "")
        assert envelope.metadata["redaction_applied"] is True

    def test_unredacted_prose_is_not_flagged(self, bindings):
        envelope = _only(normalize_github_pull_request(
            fx.merged_pull_request(), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        ))
        assert "redaction_applied" not in envelope.metadata

    def test_the_whole_payload_is_never_copied_into_metadata(
        self, bindings,
    ):
        """A normalizer that stored the raw payload would make every
        future provider field durable, including ones nobody reviewed."""
        pull = copy.deepcopy(fx.merged_pull_request())
        pull["installation_access_token"] = "ghs_secret"
        pull["_links"]["self"]["href"] = "https://api.github.com/x"
        envelope = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        serialized = envelope.model_dump_json()
        assert "ghs_secret" not in serialized
        assert "installation_access_token" not in serialized

    def test_no_envelope_describes_individual_activity(self, bindings):
        banned = (
            "productivity", "activity", "hours", "utilization",
            "efficiency", "velocity", "throughput", "commits_count",
        )
        outcome = normalize_github_payloads(
            events=fx.load("events"),
            pull_requests=fx.load("pull_requests"),
            issues=fx.load("issues"),
            bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        )
        offenders = sorted({
            key for envelope in outcome.envelopes
            for key in envelope.metadata
            if any(word in key.lower() for word in banned)
        })
        assert offenders == [], (
            f"{offenders} would make evidence a record of people rather "
            f"than of organizational mechanics"
        )

    def test_long_prose_is_excerpted_and_says_so(self, bindings):
        pull = copy.deepcopy(fx.merged_pull_request())
        pull["body"] = "x" * (CONTENT_EXCERPT_LIMIT + 500)
        envelope = _only(normalize_github_pull_request(
            pull, bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ))
        assert len(envelope.content or "") == CONTENT_EXCERPT_LIMIT
        assert envelope.metadata["content_truncated"] is True, (
            "an excerpt read as a whole body is how a reader concludes "
            "something the full text contradicts"
        )


# ── The vocabulary is closed ─────────────────────────────────────────


class TestEveryEmittedClassIsRecognized:
    def test_no_normalizer_invents_an_event_class(self, bindings):
        """An unrecognized class reaches the temporal layer as a string
        and degrades silently to generic observed evidence, so the set is
        asserted rather than trusted."""
        outcome = normalize_github_payloads(
            events=fx.load("events"),
            pull_requests=(
                fx.load("pull_requests") + fx.load("pull_request_variants")
            ),
            issues=fx.load("issues"),
            bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        )
        for state in ("APPROVED", "CHANGES_REQUESTED"):
            outcome = outcome.merge(normalize_github_review(
                fx.review(state), bindings=bindings, received_at=RECEIVED,
                repository=fx.REPOSITORY, pull_request_number=8,
            ))
        emitted = {envelope.event_type for envelope in outcome.envelopes}
        assert emitted
        assert emitted <= RECOGNIZED_EVENT_CLASSES, (
            f"{sorted(emitted - RECOGNIZED_EVENT_CLASSES)} is not a class "
            f"the stack recognizes"
        )

    def test_the_recognized_set_matches_the_runtime(self):
        """If the runtime learns a new class, this set should know."""
        from lltpm.integrations.runtime import _EVENT_SCOPE_MAP
        from lltpm.runtime.sources import EVENT_CLASS_TO_TEMPORAL_TYPE

        known = set(_EVENT_SCOPE_MAP) | set(EVENT_CLASS_TO_TEMPORAL_TYPE)
        unknown = RECOGNIZED_EVENT_CLASSES - known
        assert unknown == set(), (
            f"{sorted(unknown)} is declared recognized here but no layer "
            f"of the runtime maps it"
        )


# ── Batch reporting ──────────────────────────────────────────────────


class TestBatchOutcome:
    def test_everything_skipped_is_accounted_for(self, bindings):
        pulls = fx.load("pull_requests") + fx.load("pull_request_variants")
        outcome = normalize_github_payloads(
            events=fx.load("events"), pull_requests=pulls,
            issues=fx.load("issues"), bindings=bindings,
            received_at=RECEIVED, repository=fx.REPOSITORY,
        )
        total_in = len(fx.load("events")) + len(pulls) + len(
            fx.load("issues")
        )
        assert len(outcome.envelopes) + outcome.skipped_count == total_in, (
            "a payload that produced no envelope and no report would be "
            "silently discarded"
        )

    def test_merging_preserves_every_category(self):
        from lltpm.integrations.normalizers import SkippedPayload

        left = NormalizationOutcome(unbound=[
            SkippedPayload(source_system=GITHUB, reason="a"),
        ])
        right = NormalizationOutcome(rejected=[
            SkippedPayload(source_system=GITHUB, reason="b"),
        ])
        merged = left.merge(right)
        assert len(merged.unbound) == 1 and len(merged.rejected) == 1
        assert merged.skipped_count == 2

    def test_only_unbound_and_rejected_need_a_person(self):
        from lltpm.integrations.normalizers import SkippedPayload

        quiet = NormalizationOutcome(uninterpreted=[
            SkippedPayload(source_system=GITHUB, reason="a push"),
        ])
        assert not quiet.needs_attention
        loud = NormalizationOutcome(unbound=[
            SkippedPayload(source_system=GITHUB, reason="no binding"),
        ])
        assert loud.needs_attention


# ── End to end: real payloads reaching durable history ───────────────


class TestRealPayloadsReachTheJournal:
    """The gap this commit closes. Before it, no adapter produced an
    envelope, so no real integration data could reach any engine."""

    def _ingest(self, envelopes, *, db, now):
        from lltpm.persistence import (
            InMemoryEventJournalStore,
            InMemoryIntelligenceArtifactStore,
            InMemoryRuntimeStateStore,
            build_manifest,
        )
        from lltpm.runtime import (
            RuntimeStateStore as RuntimeProjection,
            process_durable_organizational_events,
        )
        from lltpm.runtime.baselines import baseline_providers
        from lltpm.runtime.services import LiveIntelligenceServices
        from lltpm.runtime.sources import JournalBackedDataSource
        from lltpm.scenarios.models import ScenarioHorizon

        journal = db["journal"]
        services = LiveIntelligenceServices(
            data_source=JournalBackedDataSource(
                journal=journal, organization_id="acme",
            ),
            as_of=now,
            scenario_horizon=ScenarioHorizon(
                start_at=now, end_at=now + timedelta(days=14),
                maximum_depth=4, minimum_branch_probability=0.02,
            ),
            **baseline_providers(),
        )
        return process_durable_organizational_events(
            envelopes=envelopes,
            organization_id="acme",
            journal=journal,
            state_store=db["state"],
            artifact_store=db["artifacts"],
            runtime_projection=RuntimeProjection(organization_id="acme"),
            intelligence_services=services,
            manifest=build_manifest(policy_fingerprint="test"),
            now=now,
        )

    @pytest.fixture
    def db(self):
        from lltpm.persistence import (
            InMemoryEventJournalStore,
            InMemoryIntelligenceArtifactStore,
            InMemoryRuntimeStateStore,
        )

        return {
            "journal": InMemoryEventJournalStore(),
            "state": InMemoryRuntimeStateStore(),
            "artifacts": InMemoryIntelligenceArtifactStore(),
        }

    @pytest.fixture
    def envelopes(self, bindings):
        outcome = normalize_github_payloads(
            pull_requests=fx.load("pull_requests"),
            issues=fx.load("issues"),
            bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        )
        assert outcome.envelopes
        return outcome.envelopes

    def test_normalized_github_data_becomes_durable_evidence(
        self, db, envelopes,
    ):
        durable, _ = self._ingest(envelopes, db=db, now=RECEIVED)
        assert durable.evidence_records_appended == len(envelopes)
        assert len(db["journal"]) >= len(envelopes)

    def test_the_journal_hash_chain_stays_valid(self, db, envelopes):
        from lltpm.persistence import verify_journal

        self._ingest(envelopes, db=db, now=RECEIVED)
        assert verify_journal(journal=db["journal"]).valid

    def test_re_polling_the_same_data_appends_nothing(self, db, envelopes):
        """The whole point of a stable key, proven where it matters: at
        the durable boundary, not in a unit test of the key function."""
        self._ingest(envelopes, db=db, now=RECEIVED)
        before = len(db["journal"])

        outcome = normalize_github_payloads(
            pull_requests=fx.load("pull_requests"),
            issues=fx.load("issues"),
            bindings=fx.bindings(), received_at=LATER,
            repository=fx.REPOSITORY,
        )
        durable, _ = self._ingest(outcome.envelopes, db=db, now=LATER)
        assert durable.evidence_records_appended == 0
        assert durable.evidence_records_deduplicated == len(
            outcome.envelopes
        )
        evidence_records = [
            r for r in db["journal"].read_from(global_sequence=1)
            if r.event_type.value == "evidence_accepted"
        ]
        assert len(evidence_records) == len(envelopes)
        assert len(db["journal"]) >= before

    def test_reality_time_survives_into_the_journal(self, db, envelopes):
        self._ingest(envelopes, db=db, now=RECEIVED)
        merges = [
            r for r in db["journal"].read_from(global_sequence=1)
            if r.event_type.value == "evidence_accepted"
            and r.payload["evidence"]["event_type"] == "pr_merged"
        ]
        assert merges
        for record in merges:
            assert record.occurred_at.month == 2, (
                "the journal must record when the merge happened, not when "
                "we polled for it"
            )
            assert record.recorded_at >= record.occurred_at


class TestEnvelopeIdentityIsUnique:
    """A regression found by the Slack lane, introduced by Commit 37.

    `make_envelope` built its id as `env-{key[:16]}`. That was safe
    while every key was a sha256 digest, and became a collision the
    moment a normalizer could supply a readable key: the first sixteen
    characters of `github:pull_request:NODE-…` are identical for every
    pull request, so eight envelopes shared two ids.

    It mattered because the id does not stay in the envelope. It becomes
    `DurableEvidenceRecord.evidence_id`, the journal record's
    `evidence_ids`, and the runtime's `root_evidence_ids` — so evidence
    provenance, temporal event ids and source-availability records all
    addressed a *group* of unrelated facts under one name. Journal
    deduplication was unaffected, because that keys on the idempotency
    key, which is why the suite stayed green and only a second reader
    found this.
    """

    def _envelopes(self, bindings):
        return normalize_github_payloads(
            pull_requests=(
                fx.load("pull_requests") + fx.load("pull_request_variants")
            ),
            issues=fx.load("issues"),
            bindings=bindings, received_at=RECEIVED,
            repository=fx.REPOSITORY,
        ).envelopes

    def test_every_envelope_has_its_own_id(self, bindings):
        envelopes = self._envelopes(bindings)
        ids = [envelope.id for envelope in envelopes]
        assert len(set(ids)) == len(ids), (
            f"{len(ids)} envelopes share {len(set(ids))} id(s); evidence "
            f"provenance would name a group of unrelated facts"
        )

    def test_the_id_does_not_leak_the_key_prefix(self, bindings):
        """The specific shape of the bug: a readable key prefix."""
        for envelope in self._envelopes(bindings):
            assert "github:" not in envelope.id
            assert envelope.id.startswith("env-")
            assert len(envelope.id) == len("env-") + 16

    def test_the_id_is_derived_so_it_survives_a_replay(self, bindings):
        """Derived rather than random: re-normalizing the same payload
        has to address the same evidence, or a replay cannot join back
        to what it rebuilt."""
        first = self._envelopes(bindings)
        second = normalize_github_payloads(
            pull_requests=(
                fx.load("pull_requests") + fx.load("pull_request_variants")
            ),
            issues=fx.load("issues"),
            bindings=fx.bindings(), received_at=LATER,
            repository=fx.REPOSITORY,
        ).envelopes
        assert [e.id for e in first] == [e.id for e in second]

    def test_distinct_facts_from_one_resource_get_distinct_ids(self):
        """One pull request yields a review request and later a merge.
        Two facts, two ids."""
        from lltpm.integrations.envelopes import make_envelope

        shared = dict(
            source_system="github", source_type="pull_request",
            source_id="acme/atlas#8", observed_at=RECEIVED,
            received_at=RECEIVED, project_ids=["atlas"],
        )
        requested = make_envelope(
            event_type="action_requested",
            idempotency_key="github:pull_request:N:v1:action_requested",
            **shared,
        )
        merged = make_envelope(
            event_type="pr_merged",
            idempotency_key="github:pull_request:N:v1:pr_merged",
            **shared,
        )
        assert requested.id != merged.id

    def test_the_durable_record_inherits_the_unique_id(self, bindings):
        """Where it actually mattered."""
        from lltpm.runtime.durability import envelope_to_durable_record

        records = [
            envelope_to_durable_record(e) for e in self._envelopes(bindings)
        ]
        evidence_ids = [r.evidence_id for r in records]
        assert len(set(evidence_ids)) == len(evidence_ids)
