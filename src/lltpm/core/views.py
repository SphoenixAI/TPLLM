"""Core cross-domain views and protocols (Commit 20).

Read-only contracts that let a peer domain consume another's state
without importing its concrete models. ``ReadinessView`` is a Protocol
because consumers only read fields; ``ReadinessSummaryView`` is the
concrete projection for cases where a value object must be passed or
persisted.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


@runtime_checkable
class ReadinessView(Protocol):
    """The launch-readiness surface a consumer outside intelligence
    actually needs."""

    @property
    def project_id(self) -> str:
        ...

    @property
    def overall_score(self) -> float:
        ...

    @property
    def overall_confidence(self) -> float:
        ...

    @property
    def critical_blocker_ids(self) -> list[str]:
        ...


class ReadinessSummaryView(BaseModel):
    """Concrete projection satisfying ``ReadinessView``."""
    project_id: str
    launch_id: str | None = None
    overall_score: float
    overall_confidence: float
    critical_blocker_ids: list[str] = Field(default_factory=list)


@runtime_checkable
class ActorView(Protocol):
    """Minimal read surface of an actor, satisfied by
    ``core.ActorReference`` and everything derived from it."""

    @property
    def actor_id(self) -> str:
        ...

    @property
    def role(self) -> str | None:
        ...

    @property
    def team_id(self) -> str | None:
        ...


def summarize_readiness(readiness: object) -> ReadinessSummaryView | None:
    """Adapter: project any launch-readiness snapshot into the core view
    by reading it duck-typed. Returns None when there is no readiness.

    Keeping this adapter in core means neither peer domain has to know
    the other's concrete model to exchange readiness.
    """
    if readiness is None:
        return None
    scorecard = getattr(readiness, "scorecard", None)
    if scorecard is None:
        return None
    blockers = getattr(scorecard, "blockers", []) or []
    critical: list[str] = []
    for blocker in blockers:
        if getattr(blocker, "resolved", False):
            continue
        severity = getattr(blocker, "severity", None)
        if getattr(severity, "value", severity) == "critical":
            critical.append(str(getattr(blocker, "id", "")))
    return ReadinessSummaryView(
        project_id=str(getattr(scorecard, "project_id", "")),
        launch_id=getattr(scorecard, "launch_id", None),
        overall_score=float(getattr(scorecard, "overall_score", 0.0)),
        overall_confidence=float(
            getattr(scorecard, "overall_confidence", 0.0)
        ),
        critical_blocker_ids=critical,
    )
