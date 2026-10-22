"""Architecture boundary regression tests.

Builds a first-party import graph by AST and asserts the layering that
the codebase intends:

                 core
                /    \\
               v      v
       coordination  intelligence          identity (isolated)

Peer domains may depend downward on core; core depends on nothing.
``coordination -> intelligence`` is an allowed one-directional layered
edge (coordination reads intelligence's owned snapshots); the reverse
edge is forbidden because it would recreate the cycle.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

FIRST_PARTY_PACKAGES = [
    "core",
    "identity",
    "temporal",
    "structural",
    "intelligence",
    "coordination",
    "scenarios",
    "portfolio",
    "evaluation",
    "memory",
    "integrations",
    "application",
    "runtime",
    "persistence",
    "observability",
]

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tpllm"


def build_first_party_import_graph(
    packages: list[str] | None = None,
    root: pathlib.Path | None = None,
) -> dict[str, set[str]]:
    """Map each first-party package to the first-party packages it
    imports, by parsing source — never by importing it."""
    packages = packages or FIRST_PARTY_PACKAGES
    root = root or _SRC
    graph: dict[str, set[str]] = {p: set() for p in packages}

    for package in packages:
        directory = root / package
        if not directory.exists():
            continue
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(a.name for a in node.names)
                for module in modules:
                    if not module.startswith("tpllm."):
                        continue
                    target = module.split(".")[1]
                    if target in graph and target != package:
                        graph[package].add(target)
    return graph


def find_package_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """Return every simple cycle in the package graph, normalized so the
    same cycle is reported once."""
    cycles: set[tuple[str, ...]] = set()

    def walk(node: str, path: list[str]) -> None:
        for target in sorted(graph.get(node, ())):
            if target in path:
                cycle = path[path.index(target):]
                rotation = min(range(len(cycle)), key=lambda i: cycle[i])
                cycles.add(tuple(cycle[rotation:] + cycle[:rotation]))
            else:
                walk(target, path + [target])

    for start in sorted(graph):
        walk(start, [start])
    return sorted(cycles)


def assert_no_forbidden_package_cycles(
    graph: dict[str, set[str]],
    allowed: set[tuple[str, ...]] | None = None,
) -> None:
    allowed = allowed or set()
    found = {c for c in find_package_cycles(graph)}
    forbidden = found - allowed
    assert not forbidden, (
        f"Forbidden package dependency cycle(s): {sorted(forbidden)}"
    )


@pytest.fixture(scope="module")
def graph() -> dict[str, set[str]]:
    return build_first_party_import_graph()


class TestGraphTooling:
    def test_graph_covers_expected_packages(self, graph):
        assert set(graph) == set(FIRST_PARTY_PACKAGES)

    def test_cycle_detector_finds_a_synthetic_cycle(self):
        synthetic = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": set()}
        assert find_package_cycles(synthetic) == [("a", "b", "c")]
        with pytest.raises(AssertionError, match="cycle"):
            assert_no_forbidden_package_cycles(synthetic)

    def test_cycle_detector_accepts_a_dag(self):
        dag = {"a": {"b", "c"}, "b": {"c"}, "c": set()}
        assert find_package_cycles(dag) == []
        assert_no_forbidden_package_cycles(dag)


class TestCoreIsFoundational:
    def test_core_imports_nothing_first_party(self, graph):
        assert graph["core"] == set(), (
            f"core must not import any first-party package, found "
            f"{sorted(graph['core'])} — core is the bottom of the graph."
        )

    @pytest.mark.parametrize(
        "forbidden",
        ["coordination", "intelligence", "memory", "application",
         "integrations", "identity", "structural"],
    )
    def test_core_has_no_upward_imports(self, graph, forbidden):
        assert forbidden not in graph["core"]


class TestTemporalIsFoundational:
    def test_temporal_imports_nothing_first_party(self, graph):
        assert graph["temporal"] == set(), (
            f"temporal must not import any first-party package, found "
            f"{sorted(graph['temporal'])} — every scenario forecast roots "
            f"in it, so it stays at the bottom of the graph."
        )


class TestPortfolioComposesScenarioPrimitives:
    """Portfolio scenarios widened this boundary deliberately.

    Portfolio futures are built from the same horizon and tail-mass
    primitives as project futures, so ``portfolio -> scenarios`` is a
    legal downward edge. Everything else stays duck-typed: portfolio
    reads project state and identity by shape, not by import.
    """

    ALLOWED = {"scenarios"}

    def test_portfolio_depends_only_on_scenarios(self, graph):
        assert graph["portfolio"] <= self.ALLOWED, (
            f"portfolio may compose {sorted(self.ALLOWED)} and nothing "
            f"else, found {sorted(graph['portfolio'])} — cross-project "
            f"inputs are read duck-typed rather than by importing their "
            f"packages."
        )

    def test_scenarios_does_not_depend_back_on_portfolio(self, graph):
        assert "portfolio" not in graph["scenarios"], (
            "the portfolio edge is one-directional; the reverse would "
            "make single-project forecasting depend on the whole "
            "organization's topology."
        )

    @pytest.mark.parametrize(
        "forbidden",
        ["coordination", "intelligence", "memory", "application",
         "integrations", "identity"],
    )
    def test_portfolio_has_no_upward_or_sibling_imports(self, graph, forbidden):
        assert forbidden not in graph["portfolio"]


class TestIdentityIsIsolated:
    @pytest.mark.parametrize(
        "forbidden", ["coordination", "intelligence", "application", "memory"],
    )
    def test_identity_does_not_import_higher_layers(self, graph, forbidden):
        assert forbidden not in graph["identity"], (
            f"identity must stay low-level; it imported {forbidden}."
        )


class TestNoForbiddenCycles:
    def test_coordination_intelligence_cycle_is_gone(self, graph):
        # The core-contracts inversion: intelligence must not depend back on
        # coordination. The one-directional edge remains legal.
        assert "coordination" not in graph["intelligence"], (
            "intelligence must not import coordination — that recreates "
            "the cycle core contracts removed."
        )
        assert ("coordination", "intelligence") not in [
            tuple(sorted(c)) for c in find_package_cycles(graph)
        ]

    def test_no_first_party_package_cycles_at_all(self, graph):
        assert_no_forbidden_package_cycles(graph)

    def test_intelligence_uses_core_for_shared_primitives(self, graph):
        assert "core" in graph["intelligence"]


class TestRuntimeIsOrchestrationOnly:
    """The live orchestrator sits at the top of the graph.

    ``runtime`` composes the domains beneath it; nothing composes
    ``runtime``. An orchestration layer that domains imported back would
    stop being an orchestration layer — the scheduling concern would have
    leaked into everything it schedules.
    """

    @pytest.mark.parametrize(
        "domain",
        ["core", "identity", "temporal", "structural", "intelligence",
         "coordination", "scenarios", "portfolio", "evaluation", "memory",
         "integrations", "application", "persistence"],
    )
    def test_no_domain_imports_the_runtime(self, graph, domain):
        assert "runtime" not in graph[domain], (
            f"{domain} must not import runtime — the runtime orchestrates "
            f"the domains, and the dependency only points that way."
        )

    def test_the_runtime_composes_the_domains_it_orchestrates(self, graph):
        assert {
            "coordination", "integrations", "temporal", "persistence",
        } <= graph["runtime"], (
            "the runtime must actually compose the existing domains rather "
            "than reimplementing them"
        )

    def test_adding_the_runtime_introduced_no_cycle(self, graph):
        assert_no_forbidden_package_cycles(graph)
        assert not any(
            "runtime" in cycle for cycle in find_package_cycles(graph)
        )


class TestPersistenceIsAPort:
    """Durable storage sits at the bottom, behind protocols.

    ``persistence`` imports no first-party package at all, so a storage
    backend can never drag a domain along with it, and the runtime is the
    only layer that wires one in.
    """

    def test_persistence_imports_nothing_first_party(self, graph):
        assert graph["persistence"] == set(), (
            f"persistence must stand alone, found "
            f"{sorted(graph['persistence'])}."
        )

    @pytest.mark.parametrize(
        "domain",
        ["core", "identity", "temporal", "structural", "intelligence",
         "coordination", "scenarios", "portfolio", "evaluation", "memory",
         "integrations", "application"],
    )
    def test_no_domain_depends_on_persistence(self, graph, domain):
        assert "persistence" not in graph[domain], (
            f"{domain} must stay storage-agnostic; durable state is wired "
            f"in at the runtime seam."
        )

    def test_only_the_runtime_composes_persistence(self, graph):
        composers = {
            package for package, deps in graph.items()
            if "persistence" in deps
        }
        assert composers == {"runtime"}


class TestObservabilityIsApplicationInfrastructure:
    """Logging was added without adding a dependency.

    Call sites throughout the codebase emit through the standard
    library; this package only formats and configures, and only an
    application entry point imports it. That is ordinary stdlib logging
    discipline, and it is also the only arrangement the existing
    boundaries permit: ``persistence`` imports no first-party package at
    all, so it could not import a logging helper even where one would be
    convenient.
    """

    def test_observability_imports_nothing_first_party(self, graph):
        assert graph["observability"] == set(), (
            f"the logging layer must stand alone, found "
            f"{sorted(graph['observability'])} — anything it imported "
            f"would become a dependency of every log call site."
        )

    @pytest.mark.parametrize(
        "domain",
        ["core", "identity", "temporal", "structural", "intelligence",
         "coordination", "scenarios", "portfolio", "evaluation", "memory",
         "integrations", "application", "persistence", "runtime"],
    )
    def test_no_package_depends_on_the_logging_layer(self, graph, domain):
        assert "observability" not in graph[domain], (
            f"{domain} must log through the standard library; depending on "
            f"a logging package would make emitting a line an import "
            f"decision."
        )

    def test_adding_it_introduced_no_cycle(self, graph):
        assert_no_forbidden_package_cycles(graph)


class TestApplicationIsNotSharedInfrastructure:
    @pytest.mark.parametrize(
        "consumer",
        ["core", "identity", "coordination", "intelligence", "memory",
         "integrations"],
    )
    def test_no_domain_depends_on_application(self, graph, consumer):
        assert "application" not in graph[consumer], (
            f"{consumer} must not depend on application — calibration "
            f"authority is never shared domain infrastructure."
        )
