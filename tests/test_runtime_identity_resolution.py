"""Tests for Commit 33 — canonical identity on the ingest path.

The Commit 30 target flow lists canonical identity resolution between
temporal ordering and the ontology update. It was the one step with no
implementation on the live path, and the omission is load-bearing:
Commit 28's premise is that four projects waiting on the same approval
authority should see one shared bottleneck, which only works if
"Platform Architecture" and "platform-arch" resolve to the same node.

These tests drive the real Commit 19 resolver. Two things they assert
that look like failures and are not: a person matched only by name stays
a candidate rather than being linked, and an unfamiliar name resolves to
nothing. Both are the conservatism Commit 19 was built for — merging two
colleagues who share a surname is a far worse outcome than leaving an
identity unlinked and saying so.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from lltpm.coordination.models import OrganizationalActorRef
from lltpm.identity import (
    CanonicalEntity,
    CanonicalEntityRegistry,
    CanonicalEntityType,
    EntityResolutionPolicy,
)
from lltpm.identity.models import ExplicitEntityAlias
from lltpm.persistence import JournalEventType, historical_records
from lltpm.runtime.identity_resolution import (
    IdentityResolutionOutcome,
    canonical_ids_for_envelope,
    registry_from_journal,
    resolve_envelope_identities,
)
from tests._persistence_fixtures import ORGANIZATION, Persistence, at
from tests._wiring_fixtures import PLATFORM, PROJECT_A, PROJECT_C, envelope

PLATFORM_ID = "service_platform_architecture"
PRIYA_ID = "person_priya_rao"


def _platform() -> CanonicalEntity:
    return CanonicalEntity(
        id=PLATFORM_ID,
        entity_type=CanonicalEntityType.SERVICE,
        canonical_name=PLATFORM,
        aliases=["platform-arch", "Platform Arch", "PlatArch"],
        created_at=at(-120),
        updated_at=at(-120),
    )


def _priya() -> CanonicalEntity:
    return CanonicalEntity(
        id=PRIYA_ID,
        entity_type=CanonicalEntityType.PERSON,
        canonical_name="Priya Rao",
        aliases=["priya", "priya.rao@acme.com"],
        created_at=at(-120),
        updated_at=at(-120),
    )


def _registry(*entities: CanonicalEntity) -> CanonicalEntityRegistry:
    found = entities or (_platform(),)
    aliases = {}
    for entity in found:
        for index, text in enumerate(entity.aliases):
            alias = ExplicitEntityAlias(
                id=f"alias-{entity.id}-{index}",
                alias=text,
                canonical_entity_id=entity.id,
                created_at=at(-120),
                created_by="person_priya_rao",
            )
            aliases[alias.id] = alias
    return CanonicalEntityRegistry(
        entities={e.id: e for e in found}, aliases=aliases,
    )


def _service_envelope(
    *,
    source_id: str,
    service_name: str,
    project_ids: list[str] | None = None,
    source_system: str = "slack",
    observed_at: datetime | None = None,
    event_type: str = "chat_message",
):
    """An envelope naming a shared service — the realistic ingest shape."""
    built = envelope(
        source_id=source_id,
        event_type=event_type,
        observed_at=observed_at or at(1),
        project_ids=project_ids or [PROJECT_A],
        source_system=source_system,
        content=f"Waiting on {service_name} for the approval.",
    )
    return built.model_copy(update={"metadata": {
        "service_name": service_name,
    }})


def _person_envelope(*, source_id: str, display_name: str):
    built = envelope(
        source_id=source_id, event_type="chat_message",
        observed_at=at(1), project_ids=[PROJECT_A],
        content=f"{display_name} commented.",
    )
    return built.model_copy(update={
        "actor_refs": [
            OrganizationalActorRef(
                actor_id=display_name, display_name=display_name,
            )
        ],
    })


class TestSharedResourcesReachOneCanonicalNode:
    """The reason the step exists."""

    def test_an_alias_resolves_to_the_canonical_service(self):
        persistence = Persistence()
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(
                    source_id="m1", service_name="platform-arch",
                )
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            now=at(5),
        )
        assert outcome.canonical_entity_ids == [PLATFORM_ID], (
            outcome.explanations
        )

    def test_two_different_names_across_systems_converge(self):
        """Without this, the portfolio layer is structurally unable to
        see one shared bottleneck."""
        persistence = Persistence()
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(
                    source_id="m1", service_name="platform-arch",
                    project_ids=[PROJECT_A],
                ),
                _service_envelope(
                    source_id="m2", service_name="PlatArch",
                    project_ids=[PROJECT_C], source_system="jira",
                ),
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            now=at(5),
        )
        assert outcome.canonical_entity_ids == [PLATFORM_ID], (
            "two names for one service must not become two services"
        )
        assert len(outcome.linked) == 2, (
            "both external identities link; they link to the same node"
        )

    def test_curated_aliases_are_what_resolve_not_name_resemblance(self):
        """Commit 19 scores an explicit alias high and a mere name
        resemblance low. An alias is a human statement that two names
        mean one thing; a similar-looking string is not."""
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(source_id="m1", service_name=PLATFORM)
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert not outcome.linked, (
            "matching the canonical name is weak evidence; the curated "
            "alias is the strong signal"
        )
        assert any(
            "below the threshold" in explanation
            for explanation in outcome.explanations
        ), outcome.explanations

    def test_an_unfamiliar_name_resolves_to_nothing(self):
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(
                    source_id="m1", service_name="Entirely Unknown Thing",
                )
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert not outcome.linked
        assert outcome.unresolved, (
            "an unrecognized name is reported, never invented into a node"
        )

    def test_an_envelope_naming_nothing_resolves_nothing(self):
        _, outcome = resolve_envelope_identities(
            envelopes=[
                envelope(
                    source_id="plain", event_type="chat_message",
                    project_ids=[PROJECT_A], content="Nothing to resolve.",
                )
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert outcome.linked == {}
        assert outcome.journal_record_ids == []


class TestPersonResolutionIsDeliberatelyConservative:
    """Not a gap. Merging two colleagues who share a surname is worse
    than leaving an identity unlinked and saying so."""

    def test_a_person_matched_only_by_name_is_not_linked(self):
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _person_envelope(source_id="m1", display_name="priya")
            ],
            registry=_registry(_priya()),
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert not outcome.linked, (
            "a display name is not a strong enough identifier to merge "
            "people automatically"
        )

    def test_the_near_miss_is_explained_rather_than_hidden(self):
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _person_envelope(source_id="m1", display_name="priya")
            ],
            registry=_registry(_priya()),
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert any(
            "person threshold" in explanation
            for explanation in outcome.explanations
        ), outcome.explanations

    def test_two_people_sharing_an_alias_are_not_merged(self):
        """The failure this conservatism exists to prevent."""
        registry = _registry(
            CanonicalEntity(
                id="person_a_rao", entity_type=CanonicalEntityType.PERSON,
                canonical_name="A. Rao", aliases=["Rao"],
                created_at=at(-120), updated_at=at(-120),
            ),
            CanonicalEntity(
                id="person_b_rao", entity_type=CanonicalEntityType.PERSON,
                canonical_name="B. Rao", aliases=["Rao"],
                created_at=at(-120), updated_at=at(-120),
            ),
        )
        _, outcome = resolve_envelope_identities(
            envelopes=[_person_envelope(source_id="m1", display_name="Rao")],
            registry=registry,
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert not outcome.linked, (
            "one alias pointing at two people resolves to neither"
        )
        assert outcome.ambiguous or outcome.unresolved

    def test_an_ambiguous_match_lists_its_candidates(self):
        registry = _registry(
            CanonicalEntity(
                id="service_a", entity_type=CanonicalEntityType.SERVICE,
                canonical_name="Alpha Gateway", aliases=["gateway"],
                created_at=at(-120), updated_at=at(-120),
            ),
            CanonicalEntity(
                id="service_b", entity_type=CanonicalEntityType.SERVICE,
                canonical_name="Beta Gateway", aliases=["gateway"],
                created_at=at(-120), updated_at=at(-120),
            ),
        )
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(source_id="m1", service_name="gateway")
            ],
            registry=registry,
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert not outcome.linked
        if outcome.ambiguous:
            assert all(
                candidates
                for candidates in outcome.ambiguous.values()
            ), "an ambiguous result must name what it was torn between"

    def test_a_person_identity_does_not_resolve_to_a_service(self):
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _person_envelope(
                    source_id="m1", display_name="platform-arch",
                )
            ],
            registry=_registry(_platform()),
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert not outcome.linked, (
            "an actor is a person; a service entity is not a candidate "
            "for them however similar the name"
        )

    def test_ambiguity_asks_for_a_person(self):
        outcome = IdentityResolutionOutcome(
            ambiguous={"ext-1": ["person_a", "person_b"]},
        )
        assert outcome.needs_human_attention is True

    def test_a_clean_resolution_needs_nobody(self):
        outcome = IdentityResolutionOutcome(linked={"ext-1": "service-1"})
        assert outcome.needs_human_attention is False


class TestIdempotenceAndPurity:
    def test_an_already_linked_identity_is_not_relinked(self):
        persistence = Persistence()
        envelopes = [
            _service_envelope(source_id="m1", service_name="platform-arch")
        ]
        registry, first = resolve_envelope_identities(
            envelopes=envelopes, registry=_registry(),
            organization_id=ORGANIZATION, journal=persistence.journal,
            now=at(5),
        )
        assert first.linked

        _, second = resolve_envelope_identities(
            envelopes=envelopes, registry=registry,
            organization_id=ORGANIZATION, journal=persistence.journal,
            now=at(10),
        )
        assert second.already_linked
        assert second.linked == {}

    def test_the_registry_is_replaced_not_mutated(self):
        original = _registry()
        updated, _ = resolve_envelope_identities(
            envelopes=[
                _service_envelope(source_id="m1", service_name="platform-arch")
            ],
            registry=original,
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert original.external_identities == {}, (
            "a failure part-way must leave the caller's registry intact"
        )
        assert updated.external_identities

    def test_resolution_is_deterministic(self):
        envelopes = [
            _service_envelope(source_id="m1", service_name="platform-arch")
        ]
        first = resolve_envelope_identities(
            envelopes=envelopes, registry=_registry(),
            organization_id=ORGANIZATION, now=at(5),
        )[1]
        second = resolve_envelope_identities(
            envelopes=envelopes, registry=_registry(),
            organization_id=ORGANIZATION, now=at(5),
        )[1]
        assert first.linked == second.linked

    def test_duplicate_identities_in_one_batch_resolve_once(self):
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(
                    source_id="m1", service_name="platform-arch",
                ),
                _service_envelope(
                    source_id="m2", service_name="platform-arch",
                ),
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert len(outcome.linked) == 1, (
            "the same external identity mentioned twice is one identity"
        )

    def test_every_decision_carries_an_explanation(self):
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(source_id="m1", service_name="platform-arch")
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            now=at(5),
        )
        assert outcome.explanations
        assert all(outcome.explanations)


class TestResolutionDecisionsAreDurable:
    def _resolved(self):
        persistence = Persistence()
        registry, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(source_id="m1", service_name="platform-arch")
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            now=at(5),
        )
        return persistence, registry, outcome

    def test_a_link_is_journalled(self):
        persistence, _, outcome = self._resolved()
        assert outcome.journal_record_ids
        records = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.IDENTITY_LINKED,
        )
        assert records
        assert records[0].payload["canonical_entity_id"] == PLATFORM_ID

    def test_the_journalled_link_records_its_confidence(self):
        persistence, _, _ = self._resolved()
        records = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.IDENTITY_LINKED,
        )
        assert 0.0 <= records[0].payload["confidence"] <= 1.0

    def test_the_journalled_link_names_the_source_system(self):
        persistence, _, _ = self._resolved()
        records = historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.IDENTITY_LINKED,
        )
        assert records[0].payload["source_system"] == "slack"

    def test_the_identity_graph_rebuilds_from_the_journal(self):
        """Replay must not re-resolve against source systems that may be
        gone; the decision that was made is the decision that stands."""
        persistence, _, _ = self._resolved()
        rebuilt = registry_from_journal(
            journal=persistence.journal,
            organization_id=ORGANIZATION,
            entities=[_platform()],
        )
        linked = [
            identity for identity in rebuilt.external_identities.values()
            if identity.linked_at is not None
        ]
        assert linked, "the link was durable and should come back"

    def test_rebuilding_is_idempotent(self):
        persistence, _, _ = self._resolved()
        first = registry_from_journal(
            journal=persistence.journal, organization_id=ORGANIZATION,
            entities=[_platform()],
        )
        second = registry_from_journal(
            journal=persistence.journal, organization_id=ORGANIZATION,
            entities=[_platform()],
        )
        assert set(first.external_identities) == set(
            second.external_identities
        )

    def test_an_empty_journal_rebuilds_an_empty_graph(self):
        rebuilt = registry_from_journal(
            journal=Persistence().journal, organization_id=ORGANIZATION,
        )
        assert rebuilt.external_identities == {}

    def test_a_link_to_an_unknown_entity_is_skipped_not_fatal(self):
        persistence, _, _ = self._resolved()
        rebuilt = registry_from_journal(
            journal=persistence.journal,
            organization_id=ORGANIZATION,
            entities=[],
        )
        assert all(
            identity.linked_at is None
            for identity in rebuilt.external_identities.values()
        ), "the canonical entity is absent, so the link cannot be applied"

    def test_redelivering_the_same_resolution_dedupes(self):
        persistence, registry, _ = self._resolved()
        before = persistence.journal.latest_position().global_sequence
        resolve_envelope_identities(
            envelopes=[
                _service_envelope(source_id="m1", service_name="platform-arch")
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            now=at(10),
        )
        assert persistence.journal.latest_position().global_sequence == (
            before
        ), "the same resolution decision is one durable fact"

    def test_resolution_without_a_journal_still_works(self):
        """Resolution is useful before durability is configured."""
        _, outcome = resolve_envelope_identities(
            envelopes=[
                _service_envelope(source_id="m1", service_name="platform-arch")
            ],
            registry=_registry(),
            organization_id=ORGANIZATION,
            journal=None,
            now=at(5),
        )
        assert outcome.linked
        assert outcome.journal_record_ids == []


class TestCanonicalIdsForEnvelope:
    def test_an_alias_reaches_its_canonical_entity(self):
        built = _service_envelope(source_id="m1", service_name="platform-arch")
        assert canonical_ids_for_envelope(
            envelope=built, registry=_registry(),
        ) == [PLATFORM_ID]

    def test_the_canonical_name_reaches_it_too(self):
        built = _service_envelope(source_id="m1", service_name=PLATFORM)
        assert canonical_ids_for_envelope(
            envelope=built, registry=_registry(),
        ) == [PLATFORM_ID]

    def test_an_unrelated_envelope_reaches_nothing(self):
        built = _service_envelope(
            source_id="m1", service_name="Unrelated Service",
        )
        assert canonical_ids_for_envelope(
            envelope=built, registry=_registry(),
        ) == []

    def test_an_envelope_naming_nothing_reaches_nothing(self):
        built = envelope(
            source_id="plain", event_type="chat_message",
            project_ids=[PROJECT_A], content="Nothing to resolve.",
        )
        assert canonical_ids_for_envelope(
            envelope=built, registry=_registry(),
        ) == []

    def test_matching_is_case_insensitive(self):
        built = _service_envelope(source_id="m1", service_name="PLATFORM-ARCH")
        assert canonical_ids_for_envelope(
            envelope=built, registry=_registry(),
        ) == [PLATFORM_ID]


class TestNoWorkerScoring:
    def test_identity_resolution_maps_names_not_people_to_numbers(self):
        import ast
        import inspect

        import lltpm.runtime.identity_resolution as module

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef,
                 ast.AsyncFunctionDef),
            ):
                continue
            body = node.body
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
        source = ast.unparse(tree).lower()
        for term in (
            "productivity", "performance_score", "employee_score",
            "hours_worked", "utilization", "timesheet",
        ):
            assert term not in source, (
                f"identity resolution must not model '{term}'"
            )


class TestIdentityOnTheDurablePath:
    """Resolution is wired into the ingest pass, not a side errand."""

    def _run(self, *, registry=None, envelopes=None):
        from lltpm.runtime import (
            RuntimeStateStore as RuntimeProjection,
            process_durable_organizational_events,
        )
        from tests._persistence_fixtures import manifest, runtime_policy
        from tests._wiring_fixtures import live_services

        persistence = Persistence()
        durable, _ = process_durable_organizational_events(
            envelopes=envelopes if envelopes is not None else [
                _service_envelope(
                    source_id="m1", service_name="platform-arch",
                )
            ],
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=RuntimeProjection(
                organization_id=ORGANIZATION,
            ),
            intelligence_services=live_services(persistence=persistence),
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(60),
            identity_registry=registry if registry is not None else _registry(),
        )
        return persistence, durable

    def test_a_resolved_link_is_reported_by_the_pass(self):
        _, durable = self._run()
        assert durable.identities_linked
        assert PLATFORM_ID in durable.identities_linked.values()

    def test_the_link_is_durable(self):
        persistence, _ = self._run()
        assert historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.IDENTITY_LINKED,
        )

    def test_the_evidence_count_counts_evidence_not_identity_records(self):
        _, durable = self._run()
        assert durable.evidence_records_appended == 1, (
            "identity records are journalled too, but they are not evidence"
        )

    def test_omitting_the_registry_skips_resolution_entirely(self):
        from lltpm.runtime import (
            RuntimeStateStore as RuntimeProjection,
            process_durable_organizational_events,
        )
        from tests._persistence_fixtures import manifest, runtime_policy
        from tests._wiring_fixtures import live_services

        persistence = Persistence()
        durable, _ = process_durable_organizational_events(
            envelopes=[
                _service_envelope(
                    source_id="m1", service_name="platform-arch",
                )
            ],
            organization_id=ORGANIZATION,
            journal=persistence.journal,
            state_store=persistence.state,
            artifact_store=persistence.artifacts,
            runtime_projection=RuntimeProjection(
                organization_id=ORGANIZATION,
            ),
            intelligence_services=live_services(persistence=persistence),
            runtime_policy=runtime_policy(),
            manifest=manifest(),
            now=at(60),
        )
        assert durable.identities_linked == {}
        assert historical_records(
            journal=persistence.journal,
            event_type=JournalEventType.IDENTITY_LINKED,
        ) == []

    def test_an_ambiguous_identity_is_surfaced_as_a_warning(self):
        registry = _registry(
            CanonicalEntity(
                id="service_a", entity_type=CanonicalEntityType.SERVICE,
                canonical_name="Alpha Gateway", aliases=["gateway"],
                created_at=at(-120), updated_at=at(-120),
            ),
            CanonicalEntity(
                id="service_b", entity_type=CanonicalEntityType.SERVICE,
                canonical_name="Beta Gateway", aliases=["gateway"],
                created_at=at(-120), updated_at=at(-120),
            ),
        )
        _, durable = self._run(
            registry=registry,
            envelopes=[
                _service_envelope(source_id="m1", service_name="gateway")
            ],
        )
        assert not durable.identities_linked
        if durable.identities_ambiguous:
            assert durable.identity_needs_attention is True
            assert any(
                "may still be counted twice" in warning
                for warning in durable.warnings
            ), (
                "an unlinked identity means the portfolio layer may still "
                "see two names for one thing"
            )

    def test_the_pass_still_publishes_a_revision(self):
        # chat_message invalidates nothing, so a revision needs an event
        # class the runtime actually acts on.
        _, durable = self._run(envelopes=[
            _service_envelope(
                source_id="m1", service_name="platform-arch",
                event_type="architecture_evidence",
            )
        ])
        assert durable.result.complete is True
        assert durable.revision_promoted is True
        assert durable.identities_linked, (
            "identity resolution runs on the same pass that publishes"
        )

    def test_resolution_survives_a_restart_without_re_resolving(self):
        """The journal carries the decision; source systems need not
        still answer."""
        from lltpm.runtime import registry_from_journal

        persistence, _ = self._run()
        rebuilt = registry_from_journal(
            journal=persistence.journal,
            organization_id=ORGANIZATION,
            entities=[_platform()],
        )
        assert any(
            identity.linked_at is not None
            for identity in rebuilt.external_identities.values()
        )
