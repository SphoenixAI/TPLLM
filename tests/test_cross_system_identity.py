"""Tests for Commit 19 — cross-system identity: the Priya bridge, the
Alex Chen ambiguity, KPI collisions, and the layer bridges into
Commits 14/15/17/18."""

from __future__ import annotations

from datetime import timedelta

from lltpm.coordination import (
    ActorRelationType,
    DecisionPosition,
    OrganizationalActorRef,
    PositionType,
    WorkstreamType,
    build_decision_capsule,
    build_workstream_state,
)
from lltpm.identity import (
    CanonicalEntityType,
    EntityResolutionStatus,
    ExplicitEntityAlias,
    ExternalIdentityType,
    SourceAvailabilityStatus,
    canonicalize_actor_ref,
    detect_identity_conflicts,
    extract_external_identities_from_envelope,
    mark_source_availability,
    register_explicit_alias,
    resolve_decision_capsule_actors,
    resolve_external_identity,
)
from lltpm.integrations import make_envelope
from tests._coordination_fixtures import (
    AS_OF as COORD_AS_OF,
    PROJECT,
    WS,
    actor as coord_actor,
    decision,
    objective,
    request,
    understanding,
)
from tests._identity_fixtures import (
    AS_OF,
    POLICY,
    entity,
    identity,
    priya_registry,
    registry_with,
)


def _resolve(new_identity, registry):
    return resolve_external_identity(
        identity=new_identity,
        canonical_entities=list(registry.entities.values()),
        existing_identities=list(registry.external_identities.values()),
        policy=POLICY,
    )


# ── The Priya cross-system scenario ──────────────────────────────────

class TestPriyaCrossSystemBridge:
    def test_slack_jira_github_all_identify_one_person(self):
        registry = priya_registry()  # already holds the Jira email identity

        slack = identity(
            source_system="slack", external_id="U129",
            display_value="Priya Rao", email="priya.rao@company.com",
            team_hint="platform-architecture",
        )
        github = identity(
            source_system="github",
            identity_type=ExternalIdentityType.HANDLE,
            external_id="prao-infra", handle="prao-infra",
            email="priya.rao@company.com",
        )

        slack_result = _resolve(slack, registry)
        github_result = _resolve(github, registry)

        for result in (slack_result, github_result):
            assert result.status == EntityResolutionStatus.RESOLVED
            assert result.canonical_entity_id == "person_priya_rao"

        # The original source facts survive resolution untouched.
        assert slack.source_system == "slack"
        assert slack.display_value == "Priya Rao"
        assert github.handle == "prao-infra"

    def test_meeting_display_name_alone_stays_unresolved(self):
        # "Priya approved v3" in a transcript, with no identity bridge,
        # must not silently become person_priya_rao.
        registry = priya_registry()
        meet = identity(
            source_system="meet", external_id="attendee-3",
            display_value="P. Rao",
        )
        result = _resolve(meet, registry)
        assert result.status != EntityResolutionStatus.RESOLVED


# ── The Alex Chen ambiguity scenario ─────────────────────────────────

class TestAmbiguousPerson:
    def test_two_alex_chens_surface_both_candidates(self):
        registry = registry_with([
            entity("person_alex_chen_a", name="Alex Chen",
                   metadata={"team": "platform"}),
            entity("person_alex_chen_b", name="Alex Chen",
                   metadata={"team": "platform"}),
        ])
        result = _resolve(
            identity(source_system="meet", external_id="attendee-9",
                     display_value="Alex Chen", team_hint="platform"),
            registry,
        )
        assert result.status == EntityResolutionStatus.AMBIGUOUS
        assert result.canonical_entity_id is None
        assert {c.candidate_entity_id for c in result.candidates} == {
            "person_alex_chen_a", "person_alex_chen_b",
        }
        assert "more identity evidence is required" in (
            result.explanation.lower()
        )


# ── KPI collision scenario ───────────────────────────────────────────

class TestKPICollisions:
    def _kpi_registry(self):
        return registry_with([
            entity("kpi_activation_rate", CanonicalEntityType.KPI,
                   "Activation Rate", aliases=["Activation"],
                   metadata={"unit": "percent", "project_id": PROJECT}),
            entity("kpi_activated_accounts", CanonicalEntityType.KPI,
                   "Activated Accounts", aliases=["Activation"],
                   metadata={"unit": "count", "project_id": PROJECT}),
        ])

    def test_matching_unit_resolves_kpi(self):
        result = _resolve(
            identity(
                source_system="meet",
                identity_type=ExternalIdentityType.KPI_NAME,
                external_id="Activation",
                entity_type=CanonicalEntityType.KPI,
                display_value="Activation",
                metadata={"unit": "percent", "project_id": PROJECT},
            ),
            self._kpi_registry(),
        )
        # Only the percent KPI is even a candidate.
        assert [c.candidate_entity_id for c in result.candidates] == [
            "kpi_activation_rate"
        ]
        assert result.canonical_entity_id == "kpi_activation_rate"

    def test_incompatible_unit_rejected_despite_name_match(self):
        result = _resolve(
            identity(
                source_system="meet",
                identity_type=ExternalIdentityType.KPI_NAME,
                external_id="Activation",
                entity_type=CanonicalEntityType.KPI,
                display_value="Activation",
                metadata={"unit": "count", "project_id": PROJECT},
            ),
            self._kpi_registry(),
        )
        assert "kpi_activation_rate" not in [
            c.candidate_entity_id for c in result.candidates
        ]

    def test_no_semantics_stays_ambiguous(self):
        # "Activation improved." — no unit, no number, no context.
        result = _resolve(
            identity(
                source_system="meet",
                identity_type=ExternalIdentityType.KPI_NAME,
                external_id="Activation",
                entity_type=CanonicalEntityType.KPI,
                display_value="Activation",
            ),
            self._kpi_registry(),
        )
        assert result.status == EntityResolutionStatus.AMBIGUOUS
        assert result.canonical_entity_id is None

    def test_milestone_resolution_preserves_project_scope(self):
        registry = registry_with([
            entity("ms_beta_atlas", CanonicalEntityType.MILESTONE, "Beta",
                   metadata={"project_id": "atlas"}),
            entity("ms_beta_phoenix", CanonicalEntityType.MILESTONE, "Beta",
                   metadata={"project_id": "phoenix"}),
        ])
        result = _resolve(
            identity(
                source_system="jira",
                identity_type=ExternalIdentityType.PROJECT_KEY,
                external_id="Beta",
                entity_type=CanonicalEntityType.MILESTONE,
                display_value="Beta",
                metadata={"project_id": "atlas"},
            ),
            registry,
        )
        assert [c.candidate_entity_id for c in result.candidates] == [
            "ms_beta_atlas"
        ]


# ── Explicit aliases and conflicts ───────────────────────────────────

class TestAliasesAndConflicts:
    def test_explicit_alias_resolves_project(self):
        service = entity("service_account_graph", CanonicalEntityType.SERVICE,
                         "Account Graph Production Service")
        registry = registry_with([service])
        registry = register_explicit_alias(
            registry=registry,
            alias=ExplicitEntityAlias(
                id="alias-ags", canonical_entity_id=service.id,
                alias="account-graph-service", created_by="platform-lead",
                created_at=AS_OF, evidence_ids=["ev-service-catalog"],
            ),
        )
        result = _resolve(
            identity(
                source_system="jira",
                identity_type=ExternalIdentityType.SERVICE_NAME,
                external_id="account-graph-service",
                entity_type=CanonicalEntityType.SERVICE,
                display_value="account-graph-service",
            ),
            registry,
        )
        assert result.status == EntityResolutionStatus.RESOLVED
        assert result.canonical_entity_id == "service_account_graph"

    def test_repository_and_service_stay_distinct(self):
        registry = registry_with([
            entity("repo_account_graph", CanonicalEntityType.REPOSITORY,
                   "account-graph"),
            entity("service_account_graph", CanonicalEntityType.SERVICE,
                   "account-graph"),
        ])
        repo_identity = identity(
            source_system="github",
            identity_type=ExternalIdentityType.REPOSITORY_NAME,
            external_id="account-graph",
            entity_type=CanonicalEntityType.REPOSITORY,
            display_value="account-graph",
        )
        result = _resolve(repo_identity, registry)
        # Same name, but only the repository is a candidate.
        assert [c.candidate_entity_id for c in result.candidates] == [
            "repo_account_graph"
        ]

    def test_multi_linked_identity_surfaces_conflict(self):
        person_a = entity("person_a", name="Alex Chen")
        person_b = entity("person_b", name="Alex Chen")
        shared = identity(source_system="slack", external_id="U555")
        registry = registry_with([person_a, person_b], [(shared, "person_a")])
        # Force the same identity onto the second entity.
        entities = dict(registry.entities)
        entities["person_b"] = person_b.model_copy(
            update={"external_identity_ids": [shared.id]}
        )
        registry = registry.model_copy(update={"entities": entities})

        conflicts = detect_identity_conflicts(registry=registry)
        assert any(c.conflict_type == "identity_multi_link" for c in conflicts)
        assert not any(c.resolved for c in conflicts)  # never auto-chosen


# ── Source deletion ──────────────────────────────────────────────────

class TestSourceDeletion:
    def test_deleted_source_preserves_identity_history(self):
        registry = priya_registry()
        jira_id = next(iter(registry.external_identities))
        updated = mark_source_availability(
            registry=registry,
            external_identity_id=jira_id,
            availability=SourceAvailabilityStatus.DELETED,
            explanation=(
                "This mapping was established using evidence that is no "
                "longer retrievable."
            ),
            verified_at=AS_OF,
        )
        state = updated.external_identities[jira_id].source_state
        assert state.availability == SourceAvailabilityStatus.DELETED
        assert "no longer retrievable" in state.explanation
        # The canonical relation survives.
        assert jira_id in updated.entities["person_priya_rao"].external_identity_ids
        slack = identity(display_value="Priya Rao",
                         email="priya.rao@company.com")
        assert _resolve(slack, updated).canonical_entity_id == (
            "person_priya_rao"
        )


# ── Bridges into Commits 14 / 15 / 17 / 18 ───────────────────────────

class TestLayerBridges:
    def test_envelope_actor_converts_to_external_identity(self):
        envelope = make_envelope(
            source_system="slack", source_type="chat",
            source_id="C1/1", event_type="chat_message",
            observed_at=AS_OF, project_ids=[PROJECT],
            content="Priya approved v3 pending prepaid validation.",
            actor_refs=[
                OrganizationalActorRef(
                    actor_id="U129", display_name="Priya Rao",
                    role="Platform Architecture Lead",
                    team_id="platform-architecture",
                )
            ],
            metadata={"email": "priya.rao@company.com"},
            received_at=AS_OF,
        )
        identities = extract_external_identities_from_envelope(envelope)
        assert len(identities) == 2
        person = identities[0]
        assert person.entity_type == CanonicalEntityType.PERSON
        assert person.external_id == "U129"
        assert person.role_hint == "Platform Architecture Lead"
        assert identities[1].email == "priya.rao@company.com"

    def test_repeated_webhook_produces_same_identity_id(self):
        def build():
            return make_envelope(
                source_system="slack", source_type="chat",
                source_id="C1/1", event_type="chat_message",
                observed_at=AS_OF, project_ids=[PROJECT],
                actor_refs=[
                    OrganizationalActorRef(actor_id="U129",
                                           display_name="Priya Rao")
                ],
                received_at=AS_OF,
            )

        first = extract_external_identities_from_envelope(build())
        second = extract_external_identities_from_envelope(build())
        assert first[0].id == second[0].id

    def test_canonical_actor_bridge_resolves(self):
        registry = priya_registry()
        # Link the ontology-side identity so the actor bridge has a
        # verified anchor.
        ontology_identity = identity(
            source_system="ontology", external_id="priya",
            display_value="Priya Rao", email="priya.rao@company.com",
        )
        from lltpm.identity import (
            link_external_identity,
            register_external_identity,
        )
        registry = register_external_identity(
            registry=registry, identity=ontology_identity,
        )
        registry = link_external_identity(
            registry=registry,
            external_identity_id=ontology_identity.id,
            canonical_entity_id="person_priya_rao",
            linked_at=AS_OF,
        )

        bridged = canonicalize_actor_ref(
            actor=coord_actor(), registry=registry, observed_at=AS_OF,
        )
        assert bridged.canonical_entity_id == "person_priya_rao"
        assert bridged.resolution_status == EntityResolutionStatus.RESOLVED
        # The Commit 14 actor is preserved unchanged — capacities intact.
        assert bridged.actor.role == "Platform Architecture Lead"
        assert bridged.actor.actor_id == "priya"

    def test_capsule_actor_resolution_preserves_factual_content(self):
        workstream = build_workstream_state(
            workstream_id=WS, project_id=PROJECT, vertical="Payments",
            feature_or_scope="Account Graph Migration",
            workstream_type=WorkstreamType.MIGRATION,
            objective=objective(), key_dates=[],
            understanding=understanding(), readiness=None,
            decisions=[decision("dec-1", decided_days_ago=5)],
            action_requests=[request("req-1")], commitments=[],
            structural_signals=[], kpi_ids=[], wins=[],
            primary_contact=coord_actor(), previous_state=None,
            as_of=COORD_AS_OF,
        )
        authority = coord_actor(
            relations=[ActorRelationType.MAY_APPROVE,
                       ActorRelationType.OWNS_SYSTEM],
        )
        positions = [
            DecisionPosition(
                id="p-1", decision_id="dec-1",
                actor=coord_actor("kim", role="Mobile Lead", team="mobile"),
                position_type=PositionType.CONCERN,
                statement="Mobile expects v2 during phase one.",
                observed_at=COORD_AS_OF - timedelta(days=1),
                evidence_ids=["ev-p1"],
            )
        ]
        capsule = build_decision_capsule(
            request=request("req-1", preferred_recipient=authority),
            workstream=workstream,
            decisions=[decision("dec-1", decided_days_ago=5)],
            positions=positions,
            understanding=understanding(),
            readiness=None,
            evidence=[],
            as_of=COORD_AS_OF,
        )
        before = capsule.model_dump()

        resolved = resolve_decision_capsule_actors(
            capsule=capsule, registry=priya_registry(),
        )
        # Resolution happens alongside the capsule, never inside it.
        assert capsule.model_dump() == before
        assert resolved.capsule_id == capsule.id
        assert resolved.requested_by.actor.role == "Payments Integration Lead"
        assert resolved.decision_authority is not None
        assert len(resolved.position_actors) == 1

    def test_procedural_signature_excludes_canonical_person_ids(self):
        from lltpm.memory import build_problem_signature

        workstream = build_workstream_state(
            workstream_id=WS, project_id=PROJECT, vertical="Payments",
            feature_or_scope="Account Graph Migration",
            workstream_type=WorkstreamType.MIGRATION,
            objective=objective(), key_dates=[],
            understanding=understanding(), readiness=None,
            decisions=[], action_requests=[request("req-1")],
            commitments=[], structural_signals=[], kpi_ids=[], wins=[],
            primary_contact=coord_actor(), previous_state=None,
            as_of=COORD_AS_OF,
        )
        signature = build_problem_signature(
            workstream=workstream,
            request=request("req-1", preferred_recipient=coord_actor()),
            unresolved_decisions=[],
            kpi_states=[],
            structural_signals=[],
        )
        # Commit 17's invariant holds: structure only, no identity.
        for token in ("person_priya_rao", "priya", "xid-"):
            assert token not in signature.lower()

    def test_memory_layer_does_not_import_identity(self):
        import inspect
        import lltpm.memory.pattern_matching as matching

        source = inspect.getsource(matching)
        assert "lltpm.identity" not in source
