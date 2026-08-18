"""Tests for Commit 28 — capacity, bottlenecks, and the canonical
four-approval case."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.portfolio import (
    CapacityResourceType,
    OrganizationalCapacityWindow,
    aggregate_demand,
    detect_bottlenecks,
    detect_shared_failure_domains,
    explain_bottleneck,
)
from tests._portfolio_fixtures import (
    FRIDAY,
    PLATFORM,
    T0,
    approval_window,
    demand,
    four_approval_demands,
)


class TestCapacityWindow:
    def test_available_is_estimate_minus_committed(self):
        window = approval_window(capacity=3.0, committed=1.0)
        assert window.available_capacity_units == 2.0

    def test_available_never_negative(self):
        window = approval_window(capacity=1.0, committed=5.0)
        assert window.available_capacity_units == 0.0

    def test_window_must_be_ordered(self):
        with pytest.raises(ValueError, match="end after it starts"):
            approval_window(start=FRIDAY, end=T0)

    def test_confidence_is_separate_from_capacity(self):
        window = approval_window(capacity=2.0, confidence=0.4)
        # Capacity is an estimate; how sure we are is a different number.
        assert window.estimated_capacity_units == 2.0
        assert window.confidence == 0.4

    def test_confidence_bounded(self):
        with pytest.raises(ValueError, match="confidence"):
            approval_window(confidence=1.5)

    def test_capacity_describes_a_resource_not_a_person(self):
        fields = set(OrganizationalCapacityWindow.model_fields)
        for forbidden in ("person_id", "individual", "throughput_per_person",
                          "tickets_per_week", "productivity"):
            assert forbidden not in fields


class TestFourApprovalCanonicalCase:
    """Four requests, capacity for about two — one bottleneck, not four
    unrelated pending approvals."""

    def _bottlenecks(self, capacity: float = 2.0):
        return detect_bottlenecks(
            demands=four_approval_demands(),
            capacity_windows=[approval_window(capacity=capacity)],
            milestone_links={
                "project-a": ["milestone-beta"],
                "project-c": ["milestone-beta"],
            },
        )

    def test_four_requests_produce_one_bottleneck(self):
        bottlenecks = self._bottlenecks()
        assert len(bottlenecks) == 1, (
            "four projects blocked on one authority is one organizational "
            "constraint, not four independent problems"
        )

    def test_all_requesting_projects_are_named(self):
        bottleneck = self._bottlenecks()[0]
        assert bottleneck.affected_project_ids == [
            "project-a", "project-b", "project-c", "project-d",
        ]

    def test_overload_ratio_is_correct(self):
        bottleneck = self._bottlenecks()[0]
        assert bottleneck.demand_units == 4.0
        assert bottleneck.available_units == 2.0
        assert bottleneck.overload_ratio == pytest.approx(2.0)
        assert bottleneck.severity == "high"

    def test_capacity_confidence_is_retained(self):
        bottleneck = self._bottlenecks()[0]
        assert bottleneck.capacity_confidence == 0.6

    def test_downstream_milestones_carried(self):
        bottleneck = self._bottlenecks()[0]
        assert "milestone-beta" in bottleneck.downstream_milestone_ids

    def test_evidence_aggregated_from_window_and_demands(self):
        bottleneck = self._bottlenecks()[0]
        assert "ev-approval-queue" in bottleneck.evidence_ids
        assert "ev-dem-a" in bottleneck.evidence_ids

    def test_sufficient_capacity_produces_no_bottleneck(self):
        assert self._bottlenecks(capacity=5.0) == []

    def test_severity_scales_with_overload(self):
        assert self._bottlenecks(capacity=1.0)[0].severity == "critical"
        assert self._bottlenecks(capacity=3.0)[0].severity == "moderate"

    def test_deterministic(self):
        assert [b.model_dump() for b in self._bottlenecks()] == [
            b.model_dump() for b in self._bottlenecks()
        ]


class TestBottleneckExplanation:
    def test_explanation_is_structural_not_a_judgment(self):
        explanation = self._explanation()
        # It reports the constraint and the shared cause.
        assert PLATFORM in explanation
        assert "4 project(s)" in explanation
        assert "share one constraint" in explanation
        # It never characterises the team.
        for judgment in ("slow", "unresponsive", "failing", "bottleneck team",
                         "not responsive", "behind"):
            assert judgment not in explanation.lower()

    def test_uncertain_capacity_is_hedged(self):
        explanation = self._explanation(confidence=0.6)
        assert "approximately" in explanation
        assert "confidence 0.60" in explanation

    def test_confident_capacity_is_stated_as_estimated(self):
        explanation = self._explanation(confidence=0.95)
        assert "an estimated" in explanation

    def _explanation(self, confidence: float = 0.6) -> str:
        window = approval_window(confidence=confidence)
        return explain_bottleneck(
            resource_id=PLATFORM,
            project_ids=[
                "project-a", "project-b", "project-c", "project-d",
            ],
            demand_units=4.0, available=2.0, window=window,
        )


class TestDemandAggregation:
    def test_only_demands_for_the_resource_count(self):
        demands = four_approval_demands() + [
            demand(
                demand_id="dem-other", project_id="project-e",
                criticality=0.5, resource_id="Partner Ops",
            ),
        ]
        competing = aggregate_demand(
            demands=demands, resource_id=PLATFORM,
            window=approval_window(),
        )
        assert [d.id for d in competing] == [
            "dem-a", "dem-b", "dem-c", "dem-d",
        ]

    def test_demands_outside_the_window_are_excluded(self):
        late = demand(
            demand_id="dem-late", project_id="project-e", criticality=0.9,
        ).model_copy(update={
            "earliest_needed_at": FRIDAY + timedelta(days=3),
            "latest_needed_at": FRIDAY + timedelta(days=5),
        })
        competing = aggregate_demand(
            demands=[*four_approval_demands(), late],
            resource_id=PLATFORM, window=approval_window(),
        )
        assert "dem-late" not in [d.id for d in competing]


class TestSharedFailureDomains:
    def test_shared_domain_detected(self):
        domains = detect_shared_failure_domains(
            demands=four_approval_demands(),
        )
        assert len(domains) == 1
        domain = domains[0]
        assert domain.resource_id == PLATFORM
        assert len(domain.project_ids) == 4

    def test_projects_without_an_alternative_are_named(self):
        domain = detect_shared_failure_domains(
            demands=four_approval_demands(),
        )[0]
        # Only C has neither a workaround nor an alternative resource.
        assert domain.projects_without_alternative == ["project-c"]
        assert domain.single_point_of_failure is True
        assert "no alternative route" in domain.explanation

    def test_all_projects_covered_is_not_a_single_point_of_failure(self):
        demands = [
            demand(
                demand_id=f"dem-{i}", project_id=f"project-{i}",
                criticality=0.8, has_workaround=True, workaround="fallback",
            )
            for i in range(3)
        ]
        domain = detect_shared_failure_domains(demands=demands)[0]
        assert domain.single_point_of_failure is False
        assert "every dependent project" in domain.explanation

    def test_single_project_dependency_is_not_shared(self):
        assert detect_shared_failure_domains(
            demands=[
                demand(
                    demand_id="dem-solo", project_id="project-a",
                    criticality=0.9,
                ),
            ],
        ) == []
