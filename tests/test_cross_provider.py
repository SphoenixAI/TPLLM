"""Cross-provider tests.

Each provider lane proved its own translation in isolation. These are
the tests neither lane could write: what happens when one workstream is
described by three systems at once, which is the actual situation the
product exists for.

The six scenarios are the ones the sprint brief mandated, and each
exists because a plausible-looking implementation gets it wrong:

1. three systems, one coherent revision — no source becomes total truth;
2. a closed Jira ticket is not a completed workstream;
3. a Slack decision does not lose to a stale Jira field, nor win merely
   by arriving later;
4. a late Slack event cannot regress current state;
5. one person across three handles feeds identity resolution, which
   declines to guess;
6. a corpus full of authors, assignees and reviewers produces no score.

Everything here runs offline. The payloads are each provider's
committed fixtures, so the shapes are the ones the providers publish
rather than ones convenient to the assertion.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta

import pytest

from tpllm.integrations.normalizers import NormalizationOutcome
from tpllm.integrations.providers import PROVIDERS, describe_providers

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

#: One TPLLM project, described by three systems.
PROJECT = "atlas"
GITHUB_SCOPE = "acme/atlas-service"
SLACK_SCOPE = "T0ABCDEF/C0ABCDEF"
JIRA_SCOPE = "acme.atlassian.net/ABC"

SCOPES = {
    "github": GITHUB_SCOPE,
    "slack": SLACK_SCOPE,
    "jira": JIRA_SCOPE,
}

NOW = datetime(2026, 10, 22, 9, 0)


def _load(provider: str, name: str) -> list[dict]:
    return json.loads((FIXTURES / provider / f"{name}.json").read_text())


def _bindings():
    """Every provider's scope bound to the same project.

    The point of the exercise: three source systems, one workstream.
    """
    from tpllm.integrations.normalizers import ProjectBindings, SourceBinding

    return ProjectBindings(bindings=[
        SourceBinding(
            source_system=name, external_scope=scope,
            project_ids=[PROJECT],
        )
        for name, scope in SCOPES.items()
    ])


def _normalize(provider: str, kind: str, payloads: list[dict], *, at=NOW):
    descriptor = PROVIDERS[provider]
    normalizer = descriptor.normalizer()
    bindings = _bindings()
    outcome = NormalizationOutcome()
    for payload in payloads:
        outcome = outcome.merge(normalizer(
            payload_kind=kind, payload=payload, bindings=bindings,
            received_at=at,
            scope=descriptor.normalizer_scope(SCOPES[provider]),
        ))
    return outcome


@pytest.fixture
def db():
    from tpllm.persistence import (
        InMemoryEventJournalStore,
        InMemoryIntelligenceArtifactStore,
        InMemoryRuntimeStateStore,
    )

    return {
        "journal": InMemoryEventJournalStore(),
        "state": InMemoryRuntimeStateStore(),
        "artifacts": InMemoryIntelligenceArtifactStore(),
    }


def _ingest(envelopes, *, db, now=NOW, organization="acme"):
    from tpllm.persistence import build_manifest
    from tpllm.runtime import (
        RuntimeStateStore as RuntimeProjection,
        process_durable_organizational_events,
    )
    from tpllm.runtime.baselines import baseline_providers
    from tpllm.runtime.services import LiveIntelligenceServices
    from tpllm.runtime.sources import JournalBackedDataSource
    from tpllm.scenarios.models import ScenarioHorizon

    services = LiveIntelligenceServices(
        data_source=JournalBackedDataSource(
            journal=db["journal"], organization_id=organization,
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
        organization_id=organization,
        journal=db["journal"],
        state_store=db["state"],
        artifact_store=db["artifacts"],
        runtime_projection=RuntimeProjection(organization_id=organization),
        intelligence_services=services,
        manifest=build_manifest(policy_fingerprint="cross-provider"),
        now=now,
    )


def _all_three() -> list:
    """Evidence from all three systems about one project."""
    envelopes = []
    envelopes += _normalize(
        "github", "pull_request", _load("github", "pull_requests"),
    ).envelopes
    envelopes += _normalize(
        "slack", "message", _load("slack", "messages"),
    ).envelopes
    envelopes += _normalize("jira", "issue", _load("jira", "issues")).envelopes
    return envelopes


# ── 1. One workstream, three systems ─────────────────────────────────


class TestOneWorkstreamThreeSystems:
    def test_all_three_providers_produce_evidence(self):
        for provider, kind, name in (
            ("github", "pull_request", "pull_requests"),
            ("slack", "message", "messages"),
            ("jira", "issue", "issues"),
        ):
            outcome = _normalize(provider, kind, _load(provider, name))
            assert outcome.envelopes, f"{provider} produced nothing"
            assert not outcome.unbound, (
                f"{provider} reported unbound despite a declared binding"
            )

    def test_every_envelope_lands_on_the_same_project(self):
        for envelope in _all_three():
            assert envelope.project_ids == [PROJECT]

    def test_three_sources_become_one_coherent_revision(self, db):
        """The product claim. Not three parallel truths — one revision
        computed against one evidence cutoff."""
        durable, _ = _ingest(_all_three(), db=db)
        assert durable.evidence_records_appended == len(_all_three())
        result = durable.result
        assert result.complete
        # Either a revision was published, or the pass completed and
        # found nothing material. What must not happen is a partial
        # publish or a per-source revision.
        if result.revision_id:
            assert result.projects_affected == [PROJECT]

    def test_no_single_source_dominates_the_journal(self, db):
        _ingest(_all_three(), db=db)
        systems = {
            record.payload["evidence"]["source_system"]
            for record in db["journal"].read_from(global_sequence=1)
            if record.event_type.value == "evidence_accepted"
        }
        assert systems == {"github", "slack", "jira"}

    def test_the_hash_chain_holds_across_providers(self, db):
        from tpllm.persistence import verify_journal

        _ingest(_all_three(), db=db)
        assert verify_journal(journal=db["journal"]).valid

    def test_re_reading_all_three_appends_nothing(self, db):
        """Idempotency has to hold across providers, not just within
        one — otherwise a mixed re-poll inflates every count."""
        first, _ = _ingest(_all_three(), db=db)
        before = len([
            r for r in db["journal"].read_from(global_sequence=1)
            if r.event_type.value == "evidence_accepted"
        ])
        # Without this the claim below is vacuous: "appended nothing"
        # is trivially true of a journal that never held anything.
        assert first.evidence_records_appended > 0
        assert before > 0
        later = NOW + timedelta(days=1)
        durable, _ = _ingest(_all_three(), db=db, now=later)
        after = len([
            r for r in db["journal"].read_from(global_sequence=1)
            if r.event_type.value == "evidence_accepted"
        ])
        assert durable.evidence_records_appended == 0
        assert after == before

    def test_provider_identity_is_preserved_in_the_journal(self, db):
        """So a reader can tell which system said what, which is the
        whole basis for weighing two sources against each other."""
        _ingest(_all_three(), db=db)
        for record in db["journal"].read_from(global_sequence=1):
            if record.event_type.value != "evidence_accepted":
                continue
            evidence = record.payload["evidence"]
            assert evidence["source_system"] in SCOPES
            assert evidence["source_id"]


# ── 2. A closed ticket is not a completed workstream ─────────────────


class TestTicketClosureIsNotCompletion:
    def _done_issues(self) -> list[dict]:
        issues = _load("jira", "issues")
        done = [
            issue for issue in issues
            if str(
                ((issue.get("fields") or {}).get("status") or {})
                .get("statusCategory", {}).get("key", "")
            ).lower() == "done"
            or (issue.get("fields") or {}).get("resolution")
        ]
        if not done:
            pytest.skip("no done issue in the Jira fixtures")
        return done

    def test_a_done_issue_normalizes_to_ticket_completion_only(self):
        outcome = _normalize("jira", "issue", self._done_issues())
        classes = {e.event_type for e in outcome.envelopes}
        assert classes, "a done issue produced no evidence at all"
        assert "ticket_completed" in classes

    def test_a_done_issue_asserts_nothing_about_launch_readiness(self):
        """The guardrail that matters. A ticket moving to Done is
        somebody dragging a card; it is not a validated launch."""
        from tpllm.integrations.runtime import (
            RefreshScope, determine_refresh_scope,
        )

        for envelope in _normalize("jira", "issue", self._done_issues()).envelopes:
            scopes = determine_refresh_scope(envelope)
            assert RefreshScope.LAUNCH_READINESS not in scopes, (
                f"{envelope.event_type} would move launch readiness"
            )
            assert RefreshScope.COORDINATION not in scopes, (
                f"{envelope.event_type} would move a decision"
            )

    def test_closure_plus_failing_checks_does_not_complete_anything(
        self, db,
    ):
        """Jira says done, GitHub says the work is still open. The
        workstream is not complete, and nothing in the pass may claim
        it is."""
        envelopes = _normalize("jira", "issue", self._done_issues()).envelopes
        envelopes += _normalize(
            "github", "pull_request",
            [
                pull for pull in _load("github", "pull_request_variants")
                if not pull.get("merged_at")
            ],
        ).envelopes
        durable, _ = _ingest(envelopes, db=db)
        assert durable.result.complete
        for update in durable.result.project_updates:
            rendered = update.model_dump_json().lower()
            for word in ("launch ready", "launch_ready", "complete=true"):
                assert word not in rendered


# ── 3. A Slack decision against a stale Jira field ───────────────────


class TestSlackDecisionVersusStaleTicket:
    def test_a_chat_message_cannot_move_a_decision_on_its_own(self):
        """Slack is evidence. "v3 approved" in a channel is evidence
        about a decision, not the decision — otherwise any sentence
        becomes organizational truth."""
        from tpllm.integrations.runtime import (
            RefreshScope, determine_refresh_scope,
        )

        outcome = _normalize("slack", "message", _load("slack", "messages"))
        assert outcome.envelopes
        for envelope in outcome.envelopes:
            scopes = determine_refresh_scope(envelope)
            assert scopes == {RefreshScope.EVIDENCE_ONLY}, (
                f"{envelope.event_type} reaches {scopes}"
            )

    def test_neither_source_wins_by_arriving_second(self, db):
        """Arrival order is not temporal authority. Ingesting Jira then
        Slack, and Slack then Jira, must leave the same durable facts."""
        slack = _normalize("slack", "message", _load("slack", "messages")).envelopes
        jira = _normalize("jira", "issue", _load("jira", "issues")).envelopes

        from tpllm.persistence import InMemoryEventJournalStore

        keys = []
        for order in ((slack, jira), (jira, slack)):
            store = {
                "journal": InMemoryEventJournalStore(),
                "state": db["state"].__class__(),
                "artifacts": db["artifacts"].__class__(),
            }
            for batch in order:
                _ingest(batch, db=store)
            keys.append(sorted(
                record.idempotency_key
                for record in store["journal"].read_from(global_sequence=1)
                if record.event_type.value == "evidence_accepted"
            ))
        assert keys[0] == keys[1]

    def test_reality_time_not_arrival_time_orders_the_evidence(self):
        """Each provider's fact keeps its own time, which is what lets a
        later-arriving older fact stay older."""
        for provider, kind, name in (
            ("slack", "message", "messages"),
            ("jira", "issue", "issues"),
            ("github", "pull_request", "pull_requests"),
        ):
            for envelope in _normalize(
                provider, kind, _load(provider, name), at=NOW,
            ).envelopes:
                assert envelope.observed_at <= envelope.received_at
                assert envelope.received_at == NOW


# ── 4. A late event cannot regress current state ─────────────────────


class TestLateArrivalDoesNotRegress:
    def test_an_older_fact_ingested_later_keeps_its_own_time(self, db):
        """The Slack decision happens at 10:05 and is ingested first;
        an older Jira snapshot arrives second. The journal must record
        each at its own reality time."""
        slack = _normalize("slack", "message", _load("slack", "messages")).envelopes
        jira = _normalize("jira", "issue", _load("jira", "issues")).envelopes

        _ingest(slack, db=db, now=NOW)
        _ingest(jira, db=db, now=NOW + timedelta(hours=1))

        records = [
            r for r in db["journal"].read_from(global_sequence=1)
            if r.event_type.value == "evidence_accepted"
        ]
        for record in records:
            # Knowledge time may be later than reality time; reality
            # time must never be rewritten to the moment of arrival.
            assert record.occurred_at <= record.recorded_at

    def test_the_second_batch_does_not_rewrite_the_first(self, db):
        slack = _normalize(
            "slack", "message", _load("slack", "messages"),
        ).envelopes
        _ingest(slack, db=db, now=NOW)
        before = {
            r.idempotency_key: r.occurred_at
            for r in db["journal"].read_from(global_sequence=1)
        }
        _ingest(
            _normalize("jira", "issue", _load("jira", "issues")).envelopes,
            db=db, now=NOW + timedelta(days=2),
        )
        after = {
            r.idempotency_key: r.occurred_at
            for r in db["journal"].read_from(global_sequence=1)
        }
        for key, occurred in before.items():
            assert after[key] == occurred, (
                f"{key} had its reality time rewritten by a later batch"
            )


# ── 5. One person, three handles ─────────────────────────────────────


class TestCrossProviderIdentity:
    def test_each_provider_proposes_its_own_handle(self):
        """A GitHub login, a Slack user id and a Jira account id are
        three external identities. Each is offered to the identity
        layer; none is asserted to be a person."""
        handles: dict[str, set[str]] = {}
        for provider, kind, name in (
            ("github", "pull_request", "pull_requests"),
            ("slack", "message", "messages"),
            ("jira", "issue", "issues"),
        ):
            found = set()
            for envelope in _normalize(
                provider, kind, _load(provider, name),
            ).envelopes:
                for ref in envelope.actor_refs:
                    found.add(ref.actor_id)
            handles[provider] = found

        assert all(handles.values()), f"a provider named nobody: {handles}"

    def test_no_provider_asserts_a_canonical_identity(self):
        for provider, kind, name in (
            ("github", "pull_request", "pull_requests"),
            ("slack", "message", "messages"),
            ("jira", "issue", "issues"),
        ):
            for envelope in _normalize(
                provider, kind, _load(provider, name),
            ).envelopes:
                for ref in envelope.actor_refs:
                    assert ref.canonical_entity_id is None, (
                        f"{provider} decided who {ref.actor_id} is; that "
                        f"is the identity engine's call"
                    )

    def test_the_identity_layer_declines_to_merge_three_handles(self):
        """Three unrelated-looking handles for one person is exactly the
        case where guessing is worse than reporting. The engine must not
        link them without curated evidence."""
        from tpllm.identity.registry import (
            CanonicalEntityRegistry,
            extract_external_identities_from_envelope,
        )
        from tpllm.runtime.identity_resolution import (
            resolve_envelope_identities,
        )

        envelopes = _all_three()
        identities = [
            identity
            for envelope in envelopes
            for identity in extract_external_identities_from_envelope(envelope)
        ]
        assert identities, "no external identity was offered at all"

        _, outcome = resolve_envelope_identities(
            envelopes=envelopes,
            registry=CanonicalEntityRegistry(),
            organization_id="acme",
            now=NOW,
        )
        # With an empty registry there is nothing curated to match, so
        # nothing may be linked. Unresolved is the correct answer.
        assert not outcome.linked, (
            f"linked {outcome.linked} with no curated evidence"
        )

    def test_handles_from_different_providers_do_not_collide(self):
        """Two providers using the same id string must still be two
        external identities, because the id space is per provider."""
        from tpllm.identity.registry import (
            extract_external_identities_from_envelope,
        )

        by_provider: dict[str, set[str]] = {}
        for envelope in _all_three():
            for identity in extract_external_identities_from_envelope(envelope):
                by_provider.setdefault(
                    envelope.source_system, set(),
                ).add(identity.id)
        pooled = [i for ids in by_provider.values() for i in ids]
        assert len(pooled) == len(set(pooled)), (
            "two providers produced the same external identity id"
        )


# ── 6. No worker scoring, across the whole corpus ────────────────────


class TestNoWorkerScoringAcrossProviders:
    """The measurement-scope invariant, checked against a mixed corpus full of
    authors, assignees, reviewers and commenters — the richest input
    anybody has for accidentally scoring somebody."""

    BANNED = (
        "grade", "score", "productivity", "responsiveness", "reliability",
        "efficiency", "utilization", "ranking", "velocity_per",
        "response_rate", "ignored", "hours_worked", "timesheet",
        "was_blocker", "commitments_missed",
    )

    def test_no_envelope_metadata_key_scores_a_person(self):
        offenders = sorted({
            f"{e.source_system}:{key}"
            for e in _all_three()
            for key in e.metadata
            if any(word in key.lower() for word in self.BANNED)
        })
        assert offenders == [], offenders

    def test_no_journal_payload_scores_a_person(self, db):
        _ingest(_all_three(), db=db)
        for record in db["journal"].read_from(global_sequence=1):
            rendered = json.dumps(record.payload).lower()
            for word in ("productivity", "responsiveness_grade",
                         "reliability_grade", "performance_score",
                         "timesheet", "hours_worked"):
                assert word not in rendered, (
                    f"'{word}' reached durable history via "
                    f"{record.event_type.value}"
                )

    def test_jira_worklogs_never_reach_evidence(self):
        """Logged hours per person is the clearest worker metric Jira
        offers, and it is refused at the boundary."""
        for envelope in _normalize(
            "jira", "issue", _load("jira", "issues"),
        ).envelopes:
            rendered = envelope.model_dump_json().lower()
            assert "timespent" not in rendered
            assert "worklog" not in rendered

    def test_slack_reactions_never_become_evidence(self):
        """A reaction has no text, so the only thing it can become is a
        count per person."""
        outcome = _normalize(
            "slack", "event",
            [
                {
                    "type": "event_callback", "event_id": "Ev0CROSS01",
                    "team_id": "T0ABCDEF",
                    "event": {
                        "type": "reaction_added", "user": "U0ABCDEF",
                        "reaction": "thumbsup", "item": {
                            "type": "message", "channel": "C0ABCDEF",
                            "ts": "1771408800.000100",
                        },
                        "event_ts": "1771408800.000200",
                    },
                },
            ],
        )
        assert not outcome.envelopes
        assert outcome.uninterpreted

    def test_the_repo_wide_shape_guardrail_still_passes(self):
        """The authoritative check, run here too so a provider cannot
        introduce a scoring model without this file failing."""
        from tests.test_person_measurement_scope import (
            PERSON_MEASUREMENT_EXCEPTIONS,
            find_person_measurement_classes,
            MEASURES_THE_WORK_NOT_THE_WORKER,
        )

        assert PERSON_MEASUREMENT_EXCEPTIONS == {}
        known = set(MEASURES_THE_WORK_NOT_THE_WORKER)
        assert set(find_person_measurement_classes()) == known


# ── The registry itself ──────────────────────────────────────────────


class TestTheProviderRegistryIsHonest:
    def test_all_three_providers_are_registered(self):
        assert {d.source_system for d in describe_providers()} == {
            "github", "slack", "jira",
        }

    def test_every_provider_exposes_the_uniform_entry_point(self):
        import inspect

        for descriptor in describe_providers():
            signature = inspect.signature(descriptor.normalizer())
            assert set(signature.parameters) >= {
                "payload_kind", "payload", "bindings", "received_at",
                "scope",
            }, f"{descriptor.source_system} has a different signature"

    def test_only_github_claims_a_recorded_contract(self):
        """Slack and Jira are unreachable from the environment this was
        built in. Claiming otherwise is the failure the provenance tiers
        exist to prevent."""
        from tpllm.integrations.contracts import load_contract_bundle

        recorded = {
            name for name in ("github", "slack", "jira")
            if (load_contract_bundle(name) or None)
            and load_contract_bundle(name).verifiable_kinds
        }
        assert recorded == {"github"}

    def test_slack_and_jira_contracts_are_official_not_hand_written(self):
        from tpllm.integrations.contracts import load_contract_bundle

        for name in ("slack", "jira"):
            bundle = load_contract_bundle(name)
            assert bundle is not None
            assert bundle.official_kinds == sorted(bundle.contracts)
            assert bundle.unverifiable_kinds == []

    def test_an_unknown_payload_kind_is_refused_by_every_provider(self):
        for descriptor in describe_providers():
            outcome = descriptor.normalizer()(
                payload_kind="not_a_real_kind", payload={},
                bindings=_bindings(), received_at=NOW,
                scope=SCOPES[descriptor.source_system],
            )
            assert not outcome.envelopes
            assert outcome.rejected or outcome.uninterpreted, (
                f"{descriptor.source_system} silently produced nothing"
            )
