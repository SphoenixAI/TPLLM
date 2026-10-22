"""Historical scenario replay.

Every replayed prediction is generated from the state reconstructed at
that moment, so a historical forecast can never be scored
using information that only became known later.

The leakage guard here is not advisory — it raises.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from lltpm.scenarios.models import ScenarioHorizon, ScenarioLattice
from lltpm.temporal.models import ProjectTemporalState, TemporalEvent
from lltpm.temporal.state import reconstruct_project_state


class HistoricalScenarioReplayCase(BaseModel):
    project_id: str
    prediction_as_of: datetime
    horizon_end: datetime
    reconstructed_state_id: str
    generated_lattice_id: str
    observed_event_ids: list[str] = Field(default_factory=list)
    observed_outcomes: dict[str, bool | float | str] = Field(
        default_factory=dict
    )


class ReplayLeakageError(ValueError):
    """Raised when a replayed forecast could see its own answer."""


def reconstruct_for_replay(
    *,
    project_id: str,
    temporal_events: list[TemporalEvent],
    prediction_as_of: datetime,
) -> ProjectTemporalState:
    """The only sanctioned way to build replay input state."""
    return reconstruct_project_state(
        project_id=project_id,
        temporal_events=temporal_events,
        as_of=prediction_as_of,
    )


def assert_replay_is_leak_free(
    *,
    state: ProjectTemporalState,
    lattice: ScenarioLattice | None,
    prediction_as_of: datetime,
) -> None:
    """Verify the forecast was built strictly from prior knowledge."""
    if state.evidence_cutoff_at > prediction_as_of:
        raise ReplayLeakageError(
            f"State cutoff {state.evidence_cutoff_at.isoformat()} is after "
            f"the prediction point {prediction_as_of.isoformat()}."
        )
    if lattice is not None and lattice.evidence_cutoff_at is not None:
        if lattice.evidence_cutoff_at > prediction_as_of:
            raise ReplayLeakageError(
                f"Lattice evidence cutoff "
                f"{lattice.evidence_cutoff_at.isoformat()} is after the "
                f"prediction point {prediction_as_of.isoformat()}."
            )


def collect_observed_events(
    *,
    temporal_events: list[TemporalEvent],
    project_id: str,
    prediction_as_of: datetime,
    horizon_end: datetime,
) -> list[TemporalEvent]:
    """What actually happened inside the forecast window.

    Selected by reality time (``occurred_at``), because the question is
    what happened — not when someone noticed.
    """
    return sorted(
        (
            e for e in temporal_events
            if e.project_id == project_id
            and prediction_as_of < e.occurred_at <= horizon_end
        ),
        key=lambda e: (e.occurred_at, e.id),
    )


def build_replay_case(
    *,
    project_id: str,
    temporal_events: list[TemporalEvent],
    prediction_as_of: datetime,
    horizon_end: datetime,
    lattice: ScenarioLattice | None = None,
    observed_outcomes: dict[str, bool | float | str] | None = None,
) -> tuple[HistoricalScenarioReplayCase, ProjectTemporalState]:
    """Reconstruct, verify leak-freedom, then record what happened."""
    state = reconstruct_for_replay(
        project_id=project_id,
        temporal_events=temporal_events,
        prediction_as_of=prediction_as_of,
    )
    assert_replay_is_leak_free(
        state=state, lattice=lattice, prediction_as_of=prediction_as_of,
    )
    observed = collect_observed_events(
        temporal_events=temporal_events,
        project_id=project_id,
        prediction_as_of=prediction_as_of,
        horizon_end=horizon_end,
    )
    case = HistoricalScenarioReplayCase(
        project_id=project_id,
        prediction_as_of=prediction_as_of,
        horizon_end=horizon_end,
        reconstructed_state_id=state.state_fingerprint,
        generated_lattice_id=lattice.id if lattice is not None else "",
        observed_event_ids=[e.id for e in observed],
        observed_outcomes=dict(observed_outcomes or {}),
    )
    return case, state


def build_replay_series(
    *,
    project_id: str,
    temporal_events: list[TemporalEvent],
    prediction_points: list[datetime],
    horizon: ScenarioHorizon,
) -> list[tuple[HistoricalScenarioReplayCase, ProjectTemporalState]]:
    """A rolling series of leak-free historical prediction points."""
    results = []
    for point in sorted(prediction_points):
        horizon_end = point + (horizon.end_at - horizon.start_at)
        results.append(
            build_replay_case(
                project_id=project_id,
                temporal_events=temporal_events,
                prediction_as_of=point,
                horizon_end=horizon_end,
            )
        )
    return results
