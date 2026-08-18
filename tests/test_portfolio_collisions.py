"""Tests for Commit 28 — collision windows across projects."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lltpm.portfolio import (
    PortfolioCollisionType,
    build_portfolio_graph,
    colliding_launches,
    detect_launch_window_collisions,
    detect_resource_collisions,
)
from tests._portfolio_fixtures import (
    ACCOUNT_GRAPH,
    FRIDAY,
    PLATFORM,
    T0,
    four_project_graph_inputs,
)


def _graph():
    return build_portfolio_graph(**four_project_graph_inputs())


def _collisions(**overrides):
    kwargs = dict(graph=_graph(), window_start=T0, window_end=FRIDAY)
    kwargs.update(overrides)
    return detect_resource_collisions(**kwargs)


class TestResourceCollisions:
    def test_shared_approval_collision_detected(self):
        approval = [
            c for c in _collisions()
            if c.collision_type == PortfolioCollisionType.SHARED_APPROVAL
        ]
        assert len(approval) == 1
        assert approval[0].project_ids == [
            "project-a", "project-b", "project-c",
        ]

    def test_shared_service_collision_detected(self):
        service = [
            c for c in _collisions()
            if c.collision_type == PortfolioCollisionType.SHARED_SERVICE
        ]
        assert len(service) == 1
        assert service[0].project_ids == ["project-a", "project-c"]

    def test_unshared_resources_do_not_collide(self):
        resource_ids = {
            r for c in _collisions() for r in c.resource_ids
        }
        assert "service:Partner Gateway" not in resource_ids

    def test_severity_scales_with_project_count(self):
        approval = next(
            c for c in _collisions()
            if c.collision_type == PortfolioCollisionType.SHARED_APPROVAL
        )
        service = next(
            c for c in _collisions()
            if c.collision_type == PortfolioCollisionType.SHARED_SERVICE
        )
        assert approval.severity == "high"      # three projects
        assert service.severity == "moderate"   # two projects

    def test_window_carried_onto_the_collision(self):
        collision = _collisions()[0]
        assert collision.window_start == T0
        assert collision.window_end == FRIDAY

    def test_explanation_names_the_contending_projects(self):
        approval = next(
            c for c in _collisions()
            if c.collision_type == PortfolioCollisionType.SHARED_APPROVAL
        )
        assert PLATFORM in approval.explanation
        assert "project-a" in approval.explanation
        assert "contend" in approval.explanation

    def test_higher_threshold_suppresses_smaller_collisions(self):
        collisions = _collisions(minimum_projects=3)
        types = {c.collision_type for c in collisions}
        assert PortfolioCollisionType.SHARED_APPROVAL in types
        assert PortfolioCollisionType.SHARED_SERVICE not in types

    def test_deterministic(self):
        assert [c.model_dump() for c in _collisions()] == [
            c.model_dump() for c in _collisions()
        ]


class TestLaunchWindowCollisions:
    def test_launches_in_the_same_week_collide(self):
        collisions = detect_launch_window_collisions(
            launch_dates={
                "project-a": T0,
                "project-b": T0 + timedelta(days=3),
                "project-c": T0 + timedelta(days=40),
            },
            window_days=7,
        )
        assert len(collisions) == 1
        assert collisions[0].project_ids == ["project-a", "project-b"]
        assert collisions[0].collision_type == (
            PortfolioCollisionType.LAUNCH_WINDOW
        )

    def test_distant_launches_do_not_collide(self):
        assert detect_launch_window_collisions(
            launch_dates={
                "project-a": T0, "project-b": T0 + timedelta(days=60),
            },
        ) == []

    def test_three_way_collision_is_more_severe(self):
        collisions = detect_launch_window_collisions(
            launch_dates={
                "project-a": T0,
                "project-b": T0 + timedelta(days=2),
                "project-c": T0 + timedelta(days=4),
            },
        )
        assert collisions[0].severity == "high"
        assert len(collisions[0].project_ids) == 3

    def test_single_launch_is_not_a_collision(self):
        assert detect_launch_window_collisions(
            launch_dates={"project-a": T0},
        ) == []

    def test_colliding_launches_query(self):
        mixed = _collisions() + detect_launch_window_collisions(
            launch_dates={
                "project-a": T0, "project-b": T0 + timedelta(days=2),
            },
        )
        launches = colliding_launches(mixed)
        assert all(
            c.collision_type == PortfolioCollisionType.LAUNCH_WINDOW
            for c in launches
        )
        assert launches
