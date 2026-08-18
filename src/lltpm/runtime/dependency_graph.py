"""Intelligence dependency graph (Commit 30).

The orchestrator's central question is not "what happened?" but "what
does what happened invalidate?". This module answers it structurally.

A KPI observation does not merely change a KPI. It changes launch
readiness, which changes the scenario lattice, which changes the
scenario-aware recommendation, which may change the portfolio picture.
Rediscovering that chain by hand at every call site is how a live system
starts publishing a fresh KPI beside a stale scenario.

The graph is a DAG and its topological order is deterministic, so the
same event always produces the same recompute order.
"""

from __future__ import annotations

from pydantic import BaseModel

from lltpm.runtime.models import IntelligenceArtifactType as A


class IntelligenceDependency(BaseModel):
    """``source`` changing makes ``target`` stale."""
    source: A
    target: A
    condition: str | None = None


# The declared shape of derived intelligence. Read each edge as
# "recomputing the source obliges recomputing the target".
INTELLIGENCE_DEPENDENCIES: list[IntelligenceDependency] = [
    IntelligenceDependency(source=A.EVIDENCE, target=A.IDENTITY),
    IntelligenceDependency(source=A.EVIDENCE, target=A.TEMPORAL_STATE),
    IntelligenceDependency(source=A.IDENTITY, target=A.TEMPORAL_STATE),
    IntelligenceDependency(
        source=A.TEMPORAL_STATE, target=A.PROJECT_UNDERSTANDING,
    ),
    IntelligenceDependency(
        source=A.PROJECT_UNDERSTANDING, target=A.WORKSTREAM_STATE,
    ),
    IntelligenceDependency(
        source=A.PROJECT_UNDERSTANDING, target=A.KPI_STATE,
    ),
    IntelligenceDependency(
        source=A.PROJECT_UNDERSTANDING, target=A.AI_QUALITY,
    ),
    IntelligenceDependency(source=A.KPI_STATE, target=A.LAUNCH_READINESS),
    IntelligenceDependency(
        source=A.WORKSTREAM_STATE, target=A.LAUNCH_READINESS,
    ),
    IntelligenceDependency(source=A.TEMPORAL_STATE, target=A.DECISION_STATE),
    IntelligenceDependency(
        source=A.DECISION_STATE, target=A.DECISION_CAPSULE,
    ),
    IntelligenceDependency(
        source=A.DECISION_CAPSULE, target=A.DECISION_RECOMMENDATION,
    ),
    IntelligenceDependency(
        source=A.PROCEDURAL_MEMORY, target=A.DECISION_RECOMMENDATION,
        condition="prior resolutions inform the option set",
    ),
    IntelligenceDependency(source=A.TEMPORAL_STATE, target=A.SCENARIO_LATTICE),
    IntelligenceDependency(source=A.KPI_STATE, target=A.SCENARIO_LATTICE),
    IntelligenceDependency(
        source=A.WORKSTREAM_STATE, target=A.SCENARIO_LATTICE,
    ),
    IntelligenceDependency(
        source=A.LAUNCH_READINESS, target=A.SCENARIO_LATTICE,
    ),
    IntelligenceDependency(
        source=A.SCENARIO_LATTICE,
        target=A.SCENARIO_DECISION_RECOMMENDATION,
    ),
    IntelligenceDependency(
        source=A.DECISION_RECOMMENDATION,
        target=A.SCENARIO_DECISION_RECOMMENDATION,
    ),
    IntelligenceDependency(
        source=A.WORKSTREAM_STATE, target=A.PORTFOLIO_GRAPH,
        condition="only when the change touches a shared resource",
    ),
    IntelligenceDependency(
        source=A.DECISION_STATE, target=A.PORTFOLIO_GRAPH,
        condition="only when the change touches shared authority",
    ),
    IntelligenceDependency(
        source=A.PORTFOLIO_GRAPH, target=A.PORTFOLIO_BOTTLENECKS,
    ),
    IntelligenceDependency(
        source=A.PORTFOLIO_BOTTLENECKS, target=A.PORTFOLIO_SCENARIO,
    ),
    IntelligenceDependency(
        source=A.SCENARIO_LATTICE, target=A.PORTFOLIO_SCENARIO,
        condition="project futures feed the cross-project simulation",
    ),
    IntelligenceDependency(
        source=A.PORTFOLIO_SCENARIO, target=A.PORTFOLIO_RECOMMENDATION,
    ),
]


class DependencyCycleError(ValueError):
    """Raised when the declared dependencies stop being a DAG."""


def build_intelligence_dependency_graph(
    dependencies: list[IntelligenceDependency] | None = None,
) -> dict[A, set[A]]:
    """Map each artifact type to the types it makes stale."""
    dependencies = (
        INTELLIGENCE_DEPENDENCIES if dependencies is None else dependencies
    )
    graph: dict[A, set[A]] = {artifact: set() for artifact in A}
    for dependency in dependencies:
        graph[dependency.source].add(dependency.target)
    return graph


def find_dependency_cycles(graph: dict[A, set[A]]) -> list[tuple[str, ...]]:
    """Every simple cycle, normalized so one cycle is reported once."""
    cycles: set[tuple[str, ...]] = set()

    def walk(node: A, path: list[A]) -> None:
        for target in sorted(graph.get(node, ()), key=lambda a: a.value):
            if target in path:
                cycle = [a.value for a in path[path.index(target):]]
                rotation = min(range(len(cycle)), key=lambda i: cycle[i])
                cycles.add(tuple(cycle[rotation:] + cycle[:rotation]))
            else:
                walk(target, path + [target])

    for start in sorted(graph, key=lambda a: a.value):
        walk(start, [start])
    return sorted(cycles)


def assert_acyclic(graph: dict[A, set[A]] | None = None) -> None:
    graph = graph or build_intelligence_dependency_graph()
    cycles = find_dependency_cycles(graph)
    if cycles:
        raise DependencyCycleError(
            f"Intelligence dependency cycle(s): {cycles} — a cycle would "
            f"make recompute order undefined and could loop forever."
        )


def downstream_artifacts(
    *,
    artifact_types: set[A],
    graph: dict[A, set[A]] | None = None,
    include_self: bool = True,
) -> set[A]:
    """Everything transitively invalidated by these artifacts changing."""
    graph = graph or build_intelligence_dependency_graph()
    seen: set[A] = set(artifact_types) if include_self else set()
    frontier = list(artifact_types)
    while frontier:
        current = frontier.pop()
        for target in graph.get(current, ()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


def upstream_artifacts(
    *,
    artifact_type: A,
    graph: dict[A, set[A]] | None = None,
) -> set[A]:
    """What this artifact depends on — used to explain why it refreshed."""
    graph = graph or build_intelligence_dependency_graph()
    return {
        source for source, targets in graph.items()
        if artifact_type in targets
    }


def dependency_path(
    *,
    source: A,
    target: A,
    graph: dict[A, set[A]] | None = None,
) -> list[A]:
    """The shortest chain of edges from source to target, for
    explanation. Empty when the target is not reachable."""
    graph = graph or build_intelligence_dependency_graph()
    frontier: list[list[A]] = [[source]]
    seen = {source}
    while frontier:
        path = frontier.pop(0)
        if path[-1] == target:
            return path
        for candidate in sorted(
            graph.get(path[-1], ()), key=lambda a: a.value,
        ):
            if candidate not in seen:
                seen.add(candidate)
                frontier.append(path + [candidate])
    return []


def topological_order(
    *,
    artifact_types: set[A] | None = None,
    graph: dict[A, set[A]] | None = None,
) -> list[A]:
    """Deterministic execution order: dependencies before dependents.

    Ties are broken by artifact name, so the same batch always runs in
    the same order — a live system that reordered itself between runs
    would be impossible to reason about.
    """
    graph = graph or build_intelligence_dependency_graph()
    assert_acyclic(graph)
    selected = set(artifact_types) if artifact_types is not None else set(A)

    incoming: dict[A, set[A]] = {a: set() for a in selected}
    for source, targets in graph.items():
        if source not in selected:
            continue
        for target in targets:
            if target in selected:
                incoming[target].add(source)

    ordered: list[A] = []
    ready = sorted(
        (a for a in selected if not incoming[a]), key=lambda a: a.value,
    )
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(
            graph.get(current, ()), key=lambda a: a.value,
        ):
            if target not in selected:
                continue
            incoming[target].discard(current)
            if not incoming[target] and target not in ordered:
                if target not in ready:
                    ready.append(target)
        ready.sort(key=lambda a: a.value)

    if len(ordered) != len(selected):
        raise DependencyCycleError(
            "Could not order every requested artifact; the selection "
            "contains a cycle."
        )
    return ordered


def validate_dependency_graph(
    graph: dict[A, set[A]] | None = None,
) -> None:
    """Every declared type exists and the whole thing is orderable."""
    graph = graph or build_intelligence_dependency_graph()
    known = set(A)
    for source, targets in graph.items():
        if source not in known:
            raise ValueError(f"Unknown artifact type in graph: {source}")
        for target in targets:
            if target not in known:
                raise ValueError(f"Unknown dependency target: {target}")
    assert_acyclic(graph)
    topological_order(graph=graph)


def describe_dependency_edges(
    *,
    source: A,
    target: A,
    graph: dict[A, set[A]] | None = None,
) -> list[str]:
    """Human-readable edge list explaining a propagation."""
    path = dependency_path(source=source, target=target, graph=graph)
    return [
        f"{path[i].value} -> {path[i + 1].value}"
        for i in range(len(path) - 1)
    ]
