"""Risk prediction models.

Historical pattern analysis for milestone slips, scope growth, dependency
cascades and allocation load — early warning before a problem becomes a
blocker.

**What was removed, and why.** This module also held
``PersonMetrics``, ``PerformanceGrade`` and ``QuarterlyEvaluation``: a
per-person performance review with responsiveness, reliability,
collaboration and overall letter grades, a ``collaboration_score``
computed from how often someone "was the blocker", and narrative
strengths and areas for improvement. The agent could generate one on
request.

That is a different product from the one this system became. The
organizational-reasoning stack measures dependency congestion, approval
queues, capacity constraints, decision latency, information divergence
and context handoff depth — and it is *more* capable for it. It can say
that a team looks slow because four projects share one approval
authority. A letter grade takes the same evidence and converts a
structural failure into an individual's quarterly score, which is both
less true and less useful.

So the risk signals here are structural. Where a person appears, they
appear as the locus of an allocation decision — "seven concurrent items
are assigned here" is a statement about how work was distributed, not
about the person holding it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class RiskSignalType(str, Enum):
    """Structural signals only.

    ``RESPONSE_DEGRADATION`` ("person getting slower"),
    ``ENGAGEMENT_DROP`` ("engagement score declining"),
    ``COMMITMENT_MISS`` ("commitments not being met") and
    ``BLOCKER_PATTERN`` (who recurrently blocked things) were removed.
    Each named an individual as the risk.
    """
    MILESTONE_SLIP = "milestone_slip"       # historical pattern of slips
    SCOPE_CREEP = "scope_creep"             # increasing scope changes
    DEPENDENCY_CHAIN = "dependency_chain"   # cascade risk from dependencies
    ALLOCATION_LOAD = "allocation_load"     # too much assigned to one point


class PredictionConfidence(str, Enum):
    HIGH = "high"        # strong historical pattern, >3 data points
    MEDIUM = "medium"    # some pattern, 2-3 data points
    LOW = "low"          # weak signal, 1 data point


class RiskPrediction(BaseModel):
    """A predicted risk based on historical patterns.

    ``affected_people`` names who a risk reaches — who needs to know, or
    who holds an over-allocated queue. It is not a list of people the
    risk is *about*: nothing here scores or ranks them.
    """
    id: str = ""
    signal_type: RiskSignalType = RiskSignalType.MILESTONE_SLIP
    title: str = ""
    description: str = ""
    confidence: PredictionConfidence = PredictionConfidence.MEDIUM
    probability: float = 0.5  # 0.0 to 1.0
    impact: str = "medium"    # low | medium | high | critical
    affected_milestones: list[str] = Field(default_factory=list)
    affected_people: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    data_points: list[str] = Field(default_factory=list)
    predicted_at: datetime = Field(default_factory=datetime.utcnow)
    program_id: str = ""
