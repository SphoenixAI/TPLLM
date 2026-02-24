"""Risk Prediction and Employee Evaluation models.

Risk Prediction:
- Historical pattern analysis for milestone slips, blocker frequency
- Predictive risk scoring based on team velocity and engagement patterns
- Early warning signals before problems become blockers

Employee Evaluation:
- Response accuracy and timeliness
- Commitment follow-through rate
- High-priority message response rate
- Collaboration patterns
- Quarterly evaluation reports
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RiskSignalType(str, Enum):
    MILESTONE_SLIP = "milestone_slip"           # historical pattern of slips
    RESPONSE_DEGRADATION = "response_degradation"  # person getting slower
    SCOPE_CREEP = "scope_creep"                 # increasing scope changes
    DEPENDENCY_CHAIN = "dependency_chain"       # cascade risk from dependencies
    CAPACITY_OVERLOAD = "capacity_overload"     # person has too much assigned
    ENGAGEMENT_DROP = "engagement_drop"         # engagement score declining
    BLOCKER_PATTERN = "blocker_pattern"         # recurring blockers
    COMMITMENT_MISS = "commitment_miss"         # commitments not being met


class PredictionConfidence(str, Enum):
    HIGH = "high"        # strong historical pattern, >3 data points
    MEDIUM = "medium"    # some pattern, 2-3 data points
    LOW = "low"          # weak signal, 1 data point


class RiskPrediction(BaseModel):
    """A predicted risk based on historical patterns."""
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


class PerformanceGrade(str, Enum):
    EXCEPTIONAL = "exceptional"    # top 10%
    STRONG = "strong"              # above average
    MEETING = "meeting"            # meeting expectations
    BELOW = "below"                # below expectations
    CRITICAL = "critical"          # needs immediate attention


class PersonMetrics(BaseModel):
    """Quantitative metrics for a person over a time period."""
    person: str
    period_start: date = Field(default_factory=date.today)
    period_end: date = Field(default_factory=date.today)

    # Response metrics
    total_requests_received: int = 0
    total_responses: int = 0
    avg_response_hours: float = 0.0
    high_priority_requests: int = 0
    high_priority_responses: int = 0
    ignored_messages: int = 0

    # Commitment metrics
    commitments_made: int = 0
    commitments_met: int = 0
    commitments_missed: int = 0
    commitments_late: int = 0

    # Collaboration metrics
    unblocked_others: int = 0    # times they helped unblock someone
    was_blocker: int = 0          # times they were the blocker
    delegations_completed: int = 0
    delegations_overdue: int = 0

    # Scope metrics
    scope_changes_proposed: int = 0
    scope_changes_with_justification: int = 0

    @property
    def response_rate(self) -> float:
        if self.total_requests_received == 0:
            return 1.0
        return self.total_responses / self.total_requests_received

    @property
    def high_priority_response_rate(self) -> float:
        if self.high_priority_requests == 0:
            return 1.0
        return self.high_priority_responses / self.high_priority_requests

    @property
    def commitment_follow_through(self) -> float:
        if self.commitments_made == 0:
            return 1.0
        return self.commitments_met / self.commitments_made

    @property
    def collaboration_score(self) -> float:
        """Higher = more helpful, lower = more blocking."""
        total = self.unblocked_others + self.was_blocker
        if total == 0:
            return 0.5
        return self.unblocked_others / total


class QuarterlyEvaluation(BaseModel):
    """Quarterly evaluation report for a team member."""
    person: str
    program_id: str = ""
    quarter: str = ""  # e.g. "2026-Q1"
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    # Metrics
    metrics: PersonMetrics = Field(default_factory=lambda: PersonMetrics(person=""))

    # Grades
    responsiveness_grade: PerformanceGrade = PerformanceGrade.MEETING
    reliability_grade: PerformanceGrade = PerformanceGrade.MEETING
    collaboration_grade: PerformanceGrade = PerformanceGrade.MEETING
    overall_grade: PerformanceGrade = PerformanceGrade.MEETING

    # Narrative
    strengths: list[str] = Field(default_factory=list)
    areas_for_improvement: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)

    # Trend
    trend: str = "stable"  # improving | stable | declining
    previous_grade: PerformanceGrade | None = None
