"""Architecture boundary regression tests (Commit 20).

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
    "evaluation",
    "memory",
    "integrations",
    "application",
]

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "lltpm"


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
                    if not module.startswith("lltpm."):
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
        # The Commit 20 inversion: intelligence must not depend back on
        # coordination. The one-directional edge remains legal.
        assert "coordination" not in graph["intelligence"], (
            "intelligence must not import coordination — that recreates "
            "the cycle Commit 20 removed."
        )
        assert ("coordination", "intelligence") not in [
            tuple(sorted(c)) for c in find_package_cycles(graph)
        ]

    def test_no_first_party_package_cycles_at_all(self, graph):
        assert_no_forbidden_package_cycles(graph)

    def test_intelligence_uses_core_for_shared_primitives(self, graph):
        assert "core" in graph["intelligence"]


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
