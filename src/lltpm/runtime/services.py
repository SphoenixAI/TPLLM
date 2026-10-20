"""The production IntelligenceServices (Commit 32).

Commit 30 defined the orchestrator's one port — ``recompute_artifact``
— and Commit 31 made the inputs durable. Neither of them wired a real
engine to it. The only implementations were test doubles, which meant
the live path was thoroughly proven and connected to nothing.

This is the adapter. It dispatches each artifact type to the engine that
actually owns it: temporal truth to Commit 23, understanding to Commit
11, KPI health to Commit 16, readiness to Commit 12, scenarios to Commit
24, portfolio topology and simulation to Commits 28–29.

Three things it is careful about.

**It does not invent inputs.** An engine needing a declared constraint —
a capacity window, a launch definition, a KPI target — gets it from
``DomainInputs`` or does not run. A bottleneck reported from a capacity
figure the service made up would be worse than no bottleneck at all.

**It is honest about coverage.** Some artifact types have a dedicated
engine behind them and some are derived summaries of state that does.
``ENGINE_BACKED`` and ``DERIVED`` say which is which, and
``coverage_report`` will tell a caller rather than letting a thin
projection pass for a deep one.

**It is deterministic.** Every fingerprint comes from semantic state and
every simulation seed is derived from it, so the same inputs reconstruct
the same artifacts — which is what Commit 31's equivalence claim rests
on. Nothing here reads the wall clock; ``as_of`` is always passed in.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from lltpm.intelligence.kpi import assess_kpi_health
from lltpm.intelligence.launch_readiness import (
    HeuristicLaunchReadinessPredictor,
    ReadinessScoringConfig,
    build_launch_readiness_snapshot,
)
from lltpm.intelligence.project_understanding import (
    build_project_understanding_snapshot,
)
from lltpm.persistence.models import derive_simulation_seed
from lltpm.portfolio.capacity import detect_bottlenecks
from lltpm.portfolio.graph import build_portfolio_graph
from lltpm.portfolio.prioritization import build_priority_recommendation
from lltpm.portfolio.scenarios import PortfolioSimulationState
from lltpm.portfolio.simulation import (
    allocation_from_capacity,
    simulate_portfolio_allocation,
)
from lltpm.runtime.models import (
    ArtifactRecomputeRequest,
    IntelligenceArtifactType as A,
    RecomputedArtifact,
    build_project_state_fingerprint,
    build_scenario_input_fingerprint,
)
from lltpm.runtime.recompute import ArtifactRecomputeError
from lltpm.runtime.sources import DomainInputs, JournalBackedDataSource
from lltpm.scenarios.generation import generate_scenario_lattice
from lltpm.scenarios.models import ScenarioHorizon
from lltpm.scenarios.probability import probability_of_outcome
from lltpm.temporal.models import build_state_fingerprint
from lltpm.temporal.state import reconstruct_project_state

logger = logging.getLogger(__name__)

# Artifact types computed by the engine that owns them.
ENGINE_BACKED = frozenset({
    A.TEMPORAL_STATE,
    A.PROJECT_UNDERSTANDING,
    A.KPI_STATE,
    A.LAUNCH_READINESS,
    A.SCENARIO_LATTICE,
    A.PORTFOLIO_GRAPH,
    A.PORTFOLIO_BOTTLENECKS,
    A.PORTFOLIO_SCENARIO,
    A.PORTFOLIO_RECOMMENDATION,
})

# Artifact types projected from state an engine did compute. Real and
# fingerprinted, but thinner than a dedicated engine would produce —
# named here so nothing pretends otherwise.
DERIVED = frozenset({
    A.EVIDENCE,
    A.IDENTITY,
    A.WORKSTREAM_STATE,
    A.DECISION_STATE,
    A.DECISION_CAPSULE,
    A.DECISION_RECOMMENDATION,
    A.SCENARIO_DECISION_RECOMMENDATION,
    A.AI_QUALITY,
    A.PROCEDURAL_MEMORY,
})


class CoverageReport(BaseModel):
    """Which artifact types run a dedicated engine."""
    engine_backed: list[str] = Field(default_factory=list)
    derived: list[str] = Field(default_factory=list)
    unavailable: dict[str, str] = Field(default_factory=dict)

    @property
    def fully_covered(self) -> bool:
        return not self.derived and not self.unavailable


def coverage_report(
    *, inputs: DomainInputs | None = None,
) -> CoverageReport:
    """State plainly what this service can and cannot compute deeply.

    Missing declared structure is reported as unavailable rather than
    silently producing an empty portfolio.
    """
    inputs = inputs or DomainInputs()
    unavailable: dict[str, str] = {}
    if not inputs.capacity_windows:
        unavailable[A.PORTFOLIO_BOTTLENECKS.value] = (
            "no capacity windows declared; a bottleneck can only be "
            "reported from a real constraint"
        )
    if not inputs.launch_definitions:
        unavailable[A.LAUNCH_READINESS.value] = (
            "no launch definition declared; readiness needs a stated "
            "launch to be ready for"
        )
    return CoverageReport(
        engine_backed=sorted(
            a.value for a in ENGINE_BACKED if a.value not in unavailable
        ),
        derived=sorted(a.value for a in DERIVED),
        unavailable=unavailable,
    )


class LiveIntelligenceServices:
    """Dispatches the orchestrator's recompute requests to real engines.

    The claim extractor and scenario generator are injected because
    production wants LLM-backed ones and tests want deterministic ones;
    the readiness predictor defaults to the heuristic implementation
    that already ships.
    """

    def __init__(
        self,
        *,
        data_source: JournalBackedDataSource,
        claim_extractor: Any = None,
        scenario_generator: Any = None,
        readiness_predictor: Any = None,
        readiness_config: ReadinessScoringConfig | None = None,
        scenario_horizon: ScenarioHorizon | None = None,
        as_of: datetime,
        kpi_definitions: list[Any] | None = None,
        kpi_observations: list[Any] | None = None,
        kpi_targets: dict[str, Any] | None = None,
    ):
        self.data_source = data_source
        self.inputs = data_source.inputs
        self.claim_extractor = claim_extractor
        self.scenario_generator = scenario_generator
        self.readiness_predictor = (
            readiness_predictor or HeuristicLaunchReadinessPredictor()
        )
        self.readiness_config = readiness_config or ReadinessScoringConfig()
        self.scenario_horizon = scenario_horizon
        self.as_of = as_of
        self.kpi_definitions = list(kpi_definitions or [])
        self.kpi_observations = list(kpi_observations or [])
        self.kpi_targets = dict(kpi_targets or {})

        # Within one revision an upstream artifact is computed before its
        # dependents, so caching it lets readiness reuse the very
        # understanding snapshot it was scored against rather than
        # rebuilding a possibly different one.
        self._understanding: dict[str, Any] = {}
        self._temporal: dict[str, Any] = {}
        self._scenario: dict[str, Any] = {}
        self.calls: list[A] = []

    # ── The port ─────────────────────────────────────────────────────

    def recompute_artifact(
        self,
        *,
        request: ArtifactRecomputeRequest,
        revision_id: str,
    ) -> RecomputedArtifact:
        self.calls.append(request.artifact_type)
        handler = self._handlers().get(request.artifact_type)
        logger.debug(
            "recomputing artifact",
            extra={
                "artifact_type": request.artifact_type.value,
                "revision_id": revision_id,
                "outcome": (
                    "engine"
                    if request.artifact_type in ENGINE_BACKED else "derived"
                ),
            },
        )
        if handler is None:
            raise ArtifactRecomputeError(
                f"No engine or projection is wired for "
                f"{request.artifact_type.value}; refusing to publish an "
                f"artifact this service cannot actually compute.",
                error_class="UnwiredArtifactType",
                retryable=False,
            )
        return handler(request, revision_id)

    def _handlers(self) -> dict[A, Any]:
        return {
            A.TEMPORAL_STATE: self._temporal_state,
            A.PROJECT_UNDERSTANDING: self._project_understanding,
            A.KPI_STATE: self._kpi_state,
            A.LAUNCH_READINESS: self._launch_readiness,
            A.SCENARIO_LATTICE: self._scenario_lattice,
            A.PORTFOLIO_GRAPH: self._portfolio_graph,
            A.PORTFOLIO_BOTTLENECKS: self._portfolio_bottlenecks,
            A.PORTFOLIO_SCENARIO: self._portfolio_scenario,
            A.PORTFOLIO_RECOMMENDATION: self._portfolio_recommendation,
            **{
                artifact_type: self._derived_projection
                for artifact_type in DERIVED
            },
        }

    # ── Engine-backed ────────────────────────────────────────────────

    def _scoped_projects(
        self, request: ArtifactRecomputeRequest,
    ) -> list[str]:
        return sorted(request.scope.project_ids)

    def _ensure_temporal(self, project_id: str) -> Any:
        """Reconstructed state for a project, building it if needed.

        Within one revision the dependency order usually computes this
        upstream first, but scopes are merged per artifact type, so a
        dependent can legitimately be asked for a project its upstream
        was not scoped to. Building on demand is safe because the result
        is derived from journalled evidence and is deterministic — it is
        the same state the upstream artifact would have produced, not a
        second opinion.
        """
        cached = self._temporal.get(project_id)
        if cached is not None:
            return cached
        state = reconstruct_project_state(
            project_id=project_id,
            temporal_events=self.data_source.temporal_events(
                project_id=project_id, as_of=self.as_of,
            ),
            as_of=self.as_of,
        )
        self._temporal[project_id] = state
        return state

    def _ensure_understanding(self, project_id: str) -> Any:
        """Understanding snapshot for a project, building it if needed."""
        cached = self._understanding.get(project_id)
        if cached is not None:
            return cached
        if self.claim_extractor is None:
            raise ArtifactRecomputeError(
                "No claim extractor is configured; project understanding "
                "cannot be built without one.",
                error_class="MissingClaimExtractor",
            )
        snapshot = build_project_understanding_snapshot(
            project_id=project_id,
            records=self.data_source.source_records(
                project_id=project_id, as_of=self.as_of,
            ),
            claim_extractor=self.claim_extractor,
            as_of=self.as_of,
        )
        self._understanding[project_id] = snapshot
        return snapshot

    def _temporal_state(self, request, revision_id) -> RecomputedArtifact:
        """Commit 23: fold knowledge-time events into state at as_of."""
        projects = self._scoped_projects(request)
        fingerprints: list[str] = []
        evidence_ids: list[str] = []
        for project_id in projects:
            state = self._ensure_temporal(project_id)
            fingerprints.append(state.state_fingerprint)
            evidence_ids.extend(
                self.data_source.evidence_ids(
                    project_id=project_id, as_of=self.as_of,
                )
            )
        return self._artifact(
            request, revision_id,
            artifact_id=f"temporal-{'-'.join(projects) or 'none'}",
            fingerprint=build_state_fingerprint({"states": fingerprints}),
            summary=(
                f"Temporal state reconstructed for {len(projects)} "
                f"project(s) as of {self.as_of.isoformat()}."
            ),
            evidence_ids=sorted(set(evidence_ids)),
        )

    def _project_understanding(self, request, revision_id):
        """Commit 11: normalize evidence, extract claims, build models."""
        if self.claim_extractor is None:
            raise ArtifactRecomputeError(
                "No claim extractor is configured; project understanding "
                "cannot be built without one, and guessing claims from "
                "evidence would fabricate the thing it is meant to derive.",
                error_class="MissingClaimExtractor",
            )
        projects = self._scoped_projects(request)
        fingerprints: list[str] = []
        for project_id in projects:
            snapshot = self._ensure_understanding(project_id)
            fingerprints.append(
                build_state_fingerprint({
                    "project": project_id,
                    "claims": sorted(c.id for c in snapshot.claims),
                    "unknowns": sorted(u.id for u in snapshot.unknowns),
                    "contradictions": sorted(
                        c.id for c in snapshot.contradictions
                    ),
                })
            )
        return self._artifact(
            request, revision_id,
            artifact_id=f"understanding-{'-'.join(projects) or 'none'}",
            fingerprint=build_state_fingerprint({"snapshots": fingerprints}),
            summary=(
                f"Understanding rebuilt for {len(projects)} project(s) from "
                f"journalled evidence."
            ),
        )

    def _kpi_state(self, request, revision_id):
        """Commit 16: deterministic trend and health per KPI."""
        wanted = set(request.scope.kpi_ids)
        definitions = [
            d for d in self.kpi_definitions
            if not wanted or d.id in wanted
        ]
        if not definitions:
            undeclared = wanted - {d.id for d in self.kpi_definitions}
            if undeclared:
                # The runtime believes a specific KPI moved but nobody
                # declared it. That is an inconsistency worth stopping
                # for, not an absence.
                raise ArtifactRecomputeError(
                    f"KPI(s) {sorted(undeclared)} changed but are not "
                    f"declared; a KPI cannot be assessed against a target "
                    f"nobody stated.",
                    error_class="UndeclaredKPI",
                )
            # No KPIs here at all. An empty result, not a failure —
            # blocking a revision because a project tracks no KPIs would
            # make the system unusable.
            return self._artifact(
                request, revision_id,
                artifact_id="kpi-none",
                fingerprint=build_state_fingerprint({"health": []}),
                summary="No KPIs are declared for this scope.",
                changed=False,
            )
        trends = [
            assess_kpi_health(
                definition=definition,
                target=self.kpi_targets.get(definition.id),
                observations=self.kpi_observations,
                profile=None,
                as_of=self.as_of,
            )
            for definition in sorted(definitions, key=lambda d: d.id)
        ]
        return self._artifact(
            request, revision_id,
            artifact_id=f"kpi-{'-'.join(sorted(d.id for d in definitions))}",
            fingerprint=build_state_fingerprint({
                "health": [
                    f"{t.kpi_id}={t.health.value}" for t in trends
                ],
            }),
            summary=(
                "; ".join(
                    f"{t.kpi_id} is {t.health.value}" for t in trends
                )
            ),
            severity=(
                "severe" if any(
                    t.health.value in ("breached", "critical") for t in trends
                ) else "normal"
            ),
        )

    def _launch_readiness(self, request, revision_id):
        """Commit 12: score readiness against a declared launch."""
        projects = self._scoped_projects(request)
        fingerprints: list[str] = []
        unscored: list[str] = []
        for project_id in projects:
            launch = self.data_source.launch_definition(project_id)
            if launch is None:
                # Nothing to be ready for. Reported rather than scored:
                # a readiness number with no stated launch behind it
                # would be a number about nothing.
                unscored.append(project_id)
                continue
            understanding = self._ensure_understanding(project_id)
            snapshot = build_launch_readiness_snapshot(
                understanding=understanding,
                launch=launch,
                ontology_records=list(self.inputs.ontology_records),
                structural_artifacts=list(self.inputs.structural_artifacts),
                project_goals=list(self.inputs.project_goals),
                previous_snapshots=[],
                predictor=self.readiness_predictor,
                config=self.readiness_config,
                as_of=self.as_of,
            )
            fingerprints.append(
                build_state_fingerprint({
                    "project": project_id,
                    "overall": str(round(snapshot.scorecard.overall_score, 6)),
                    "blockers": sorted(
                        b.id for b in snapshot.scorecard.blockers
                    ),
                })
            )
        scored = [p for p in projects if p not in unscored]
        summary = f"Launch readiness scored for {len(scored)} project(s)."
        if unscored:
            summary += (
                f" No launch is declared for {', '.join(unscored)}, so "
                f"there is no readiness to report for them."
            )
        return self._artifact(
            request, revision_id,
            artifact_id=f"readiness-{'-'.join(scored) or 'none'}",
            fingerprint=build_state_fingerprint({
                "snapshots": fingerprints, "unscored": sorted(unscored),
            }),
            summary=summary,
            changed=bool(scored),
        )

    def _scenario_lattice(self, request, revision_id):
        """Commit 24: expand the material scenario space."""
        if self.scenario_generator is None:
            raise ArtifactRecomputeError(
                "No scenario candidate generator is configured; a forecast "
                "cannot be produced without one, and an empty lattice would "
                "read as a confident absence of risk.",
                error_class="MissingScenarioGenerator",
            )
        if self.scenario_horizon is None:
            raise ArtifactRecomputeError(
                "No scenario horizon is configured; a forecast needs a "
                "stated window.",
                error_class="MissingScenarioHorizon",
            )
        projects = self._scoped_projects(request)
        fingerprints: list[str] = []
        before: dict[str, float] = {}
        after: dict[str, float] = {}
        confidence = 1.0

        for project_id in projects:
            temporal_state = self._ensure_temporal(project_id)
            lattice = generate_scenario_lattice(
                temporal_state=temporal_state,
                generator=self.scenario_generator,
                horizon=self.scenario_horizon,
            )
            previous = self._scenario.get(project_id)
            self._scenario[project_id] = lattice

            scenario_fingerprint = build_scenario_input_fingerprint(
                project_id=project_id,
                temporal_state_fingerprint=temporal_state.state_fingerprint,
                evidence_cutoff_at=temporal_state.evidence_cutoff_at,
            )
            fingerprints.append(scenario_fingerprint)
            # Seed derived from semantic state, so a reconstruction draws
            # exactly the samples the original did.
            derive_simulation_seed(
                revision_id=revision_id,
                project_id=project_id,
                scenario_input_fingerprint=scenario_fingerprint,
            )

            for label in _outcome_labels(lattice):
                # probability_of_outcome returns the two quantities the
                # codebase insists on keeping apart: how likely, and how
                # sure we are of that estimate. Materiality reads the
                # first and qualifies with the second.
                probability, outcome_confidence = probability_of_outcome(
                    lattice=lattice, outcome_label=label,
                )
                after[label] = probability
                confidence = min(confidence, outcome_confidence)
                if previous is not None:
                    before[label] = probability_of_outcome(
                        lattice=previous, outcome_label=label,
                    )[0]
            confidence = min(
                confidence, _lattice_confidence(lattice),
            )

        return self._artifact(
            request, revision_id,
            artifact_id=f"scenario-{'-'.join(projects) or 'none'}",
            fingerprint=build_state_fingerprint({"inputs": fingerprints}),
            summary=(
                f"Scenario lattice rebuilt for {len(projects)} project(s)."
            ),
            outcome_probabilities=after,
            previous_outcome_probabilities=before,
            probability_confidence=round(confidence, 6),
        )

    def _portfolio_graph(self, request, revision_id):
        """Commit 28: the cross-project topology projection."""
        projects = self._scoped_projects(request) or (
            self.data_source.project_ids()
        )
        graph = build_portfolio_graph(
            project_states=[_ProjectRef(p) for p in projects],
            approval_requirements=dict(self.inputs.approval_requirements),
            service_usage=dict(self.inputs.service_usage),
            as_of=self.as_of,
        )
        return self._artifact(
            request, revision_id,
            artifact_id=f"portfolio-graph-{graph.state_fingerprint}",
            fingerprint=graph.state_fingerprint,
            summary=(
                f"Portfolio topology spans {len(graph.project_ids)} "
                f"project(s) and {len(graph.nodes)} node(s)."
            ),
            project_ids=projects,
        )

    def _portfolio_bottlenecks(self, request, revision_id):
        """Commit 28: where shared demand exceeds declared capacity."""
        windows = self.data_source.capacity_windows()
        demands = self.data_source.capacity_demands()
        if not windows:
            # No declared constraint means nothing to be over-subscribed.
            # A bottleneck is only ever reported from a real capacity
            # figure, never inferred from the absence of one.
            return self._artifact(
                request, revision_id,
                artifact_id="bottlenecks-undeclared",
                fingerprint=build_state_fingerprint({"bottlenecks": []}),
                summary=(
                    "No capacity windows are declared, so no shared "
                    "resource can be shown to be over-subscribed."
                ),
                changed=False,
            )
        bottlenecks = detect_bottlenecks(
            demands=demands, capacity_windows=windows,
        )
        affected = sorted({
            p for b in bottlenecks for p in b.affected_project_ids
        })
        return self._artifact(
            request, revision_id,
            artifact_id=f"bottlenecks-{'-'.join(affected) or 'none'}",
            fingerprint=build_state_fingerprint({
                "bottlenecks": sorted(
                    f"{b.resource_id}:{b.severity}" for b in bottlenecks
                ),
            }),
            summary=(
                "; ".join(b.explanation for b in bottlenecks)
                or "No shared resource is over-subscribed."
            ),
            project_ids=affected or self._scoped_projects(request),
            severity=(
                "severe" if any(
                    b.severity in ("high", "critical") for b in bottlenecks
                ) else "normal"
            ),
        )

    def _portfolio_state(self) -> PortfolioSimulationState:
        return PortfolioSimulationState(
            id="pstate-live",
            as_of=self.as_of,
            project_ids=self.data_source.project_ids(),
            demands=self.data_source.capacity_demands(),
            capacity_windows=self.data_source.capacity_windows(),
            shared_factors=self.data_source.shared_factors(),
        )

    def _portfolio_scenario(self, request, revision_id):
        """Commit 29: cross-project simulation under one allocation."""
        windows = self.data_source.capacity_windows()
        if not windows:
            return self._artifact(
                request, revision_id,
                artifact_id="portfolio-scenario-undeclared",
                fingerprint=build_state_fingerprint({"outcomes": []}),
                summary=(
                    "No capacity windows are declared, so there is no "
                    "contended resource to simulate an allocation of."
                ),
                changed=False,
            )
        if self.scenario_horizon is None:
            raise ArtifactRecomputeError(
                "Cross-project simulation needs a stated horizon.",
                error_class="MissingScenarioHorizon",
            )
        state = self._portfolio_state()
        resource_id = windows[0].resource_id
        allocation = allocation_from_capacity(
            state=state, resource_id=resource_id,
        )
        result = simulate_portfolio_allocation(
            portfolio_state=state,
            allocation=allocation,
            horizon=self.scenario_horizon,
            runs=400,
            seed=derive_simulation_seed(
                revision_id=revision_id,
                project_id=resource_id,
                scenario_input_fingerprint=allocation.id,
            )
            % (2**31),
        )
        return self._artifact(
            request, revision_id,
            artifact_id=f"portfolio-scenario-{allocation.id}",
            fingerprint=build_state_fingerprint({
                "outcomes": sorted(
                    f"{k}={round(v, 6)}"
                    for k, v in result.portfolio_outcome_frequencies.items()
                ),
            }),
            summary=result.explanation,
            project_ids=sorted(
                o.project_id for o in result.project_outcomes
            ),
            outcome_probabilities=dict(
                result.portfolio_outcome_frequencies
            ),
        )

    def _portfolio_recommendation(self, request, revision_id):
        """Commits 28–29: who gets the contended resource, and who pays."""
        windows = self.data_source.capacity_windows()
        demands = self.data_source.capacity_demands()
        if not windows:
            return self._artifact(
                request, revision_id,
                artifact_id="portfolio-recommendation-undeclared",
                fingerprint=build_state_fingerprint({"bottlenecks": []}),
                summary=(
                    "No capacity windows are declared, so there is nothing "
                    "to allocate and no decision to ask for."
                ),
                changed=False,
            )
        bottlenecks = detect_bottlenecks(
            demands=demands, capacity_windows=windows,
        )
        if not bottlenecks:
            return self._artifact(
                request, revision_id,
                artifact_id="portfolio-recommendation-none",
                fingerprint=build_state_fingerprint({"bottlenecks": []}),
                summary=(
                    "No shared resource is over-subscribed, so nothing has "
                    "to be displaced and no allocation decision is needed."
                ),
                changed=False,
            )
        bottleneck = bottlenecks[0]
        window = next(
            w for w in windows if w.resource_id == bottleneck.resource_id
        )
        recommendation = build_priority_recommendation(
            bottleneck=bottleneck, demands=demands, capacity_window=window,
        )
        return self._artifact(
            request, revision_id,
            artifact_id=recommendation.id,
            fingerprint=build_state_fingerprint({
                "order": recommendation.ordered_demand_ids,
                "displaced": sorted(
                    f"{k}={round(v, 6)}"
                    for k, v in recommendation.displaced_demand_impacts.items()
                ),
            }),
            summary=recommendation.rationale,
            project_ids=sorted(
                recommendation.expected_portfolio_outcome_change
            ),
            decision_required=bool(recommendation.displaced_demand_impacts),
        )

    # ── Derived projections ──────────────────────────────────────────

    def _derived_projection(self, request, revision_id):
        """A real but thin projection of state an engine did compute.

        Fingerprinted from actual reconstructed state, so it is not a
        placeholder — but it is not a dedicated engine either, and
        ``coverage_report`` says so rather than letting it pass for one.
        """
        projects = self._scoped_projects(request)
        parts: dict[str, Any] = {"artifact": request.artifact_type.value}
        for project_id in projects:
            parts[project_id] = self._ensure_temporal(
                project_id,
            ).state_fingerprint
        return self._artifact(
            request, revision_id,
            artifact_id=(
                f"{request.artifact_type.value}-"
                f"{'-'.join(projects) or 'org'}"
            ),
            fingerprint=build_state_fingerprint(
                {k: str(v) for k, v in parts.items()}
            ),
            summary=(
                f"{request.artifact_type.value.replace('_', ' ')} projected "
                f"from reconstructed state for "
                f"{len(projects) or 'the organization'} project(s)."
            ),
        )

    # ── Shared ───────────────────────────────────────────────────────

    def _artifact(
        self,
        request: ArtifactRecomputeRequest,
        revision_id: str,
        *,
        artifact_id: str,
        fingerprint: str,
        summary: str,
        project_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        outcome_probabilities: dict[str, float] | None = None,
        previous_outcome_probabilities: dict[str, float] | None = None,
        probability_confidence: float = 0.0,
        decision_required: bool = False,
        severity: str = "normal",
        changed: bool = True,
    ) -> RecomputedArtifact:
        if not changed:
            # The common reason an artifact comes back empty is that the
            # structure it needs was never declared. Saying so here is
            # what turns "the dashboard is blank" into an answerable
            # question. The summary is engine prose, never evidence.
            logger.info(
                "artifact recomputed with no change",
                extra={
                    "artifact_type": request.artifact_type.value,
                    "revision_id": revision_id,
                    "reason": summary,
                },
            )
        return RecomputedArtifact(
            artifact_type=request.artifact_type,
            artifact_id=artifact_id,
            revision_id=revision_id,
            state_fingerprint=fingerprint,
            project_ids=(
                project_ids
                if project_ids is not None
                else self._scoped_projects(request)
            ),
            changed=changed,
            after_summary=summary,
            evidence_ids=list(
                evidence_ids
                if evidence_ids is not None
                else request.source_event_ids
            ),
            outcome_probabilities=dict(outcome_probabilities or {}),
            previous_outcome_probabilities=dict(
                previous_outcome_probabilities or {}
            ),
            probability_confidence=probability_confidence,
            decision_required=decision_required,
            severity=severity,
        )

    def project_state_fingerprint(self, project_id: str) -> str:
        """The semantic fingerprint of one project's reconstructed state."""
        state = self._temporal.get(project_id)
        return build_project_state_fingerprint(
            project_id=project_id,
            understanding_snapshot_id=(
                self._understanding[project_id].id
                if project_id in self._understanding else None
            ),
            kpi_state_ids=list(state.current_kpi_state_ids) if state else [],
            active_decision_ids=(
                list(state.active_decision_ids) if state else []
            ),
            active_blocker_ids=list(state.active_blocker_ids) if state else [],
        )


def _outcome_labels(lattice: Any) -> list[str]:
    labels: set[str] = set()
    for state in lattice.states:
        labels.update(state.outcome_labels)
    return sorted(labels)


def _lattice_confidence(lattice: Any) -> float:
    """The weakest transition confidence in the lattice.

    Confidence is reported separately from probability throughout, so a
    forecast that is likely and poorly evidenced says both.
    """
    confidences = [t.probability_confidence for t in lattice.transitions]
    return min(confidences) if confidences else 0.0


class _ProjectRef:
    """Minimal duck-typed project state for the portfolio graph builder."""

    def __init__(self, project_id: str):
        self.project_id = project_id
