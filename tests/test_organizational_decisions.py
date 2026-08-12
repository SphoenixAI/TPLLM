"""Tests for Commit 14 — latest conclusive decision beats latest message."""

from __future__ import annotations

from datetime import timedelta

from lltpm.coordination import (
    OrganizationalDecisionStatus,
    get_latest_conclusive_decision,
    reopen_decision,
    supersede_decision,
)
from tests._coordination_fixtures import AS_OF, WS, decision


class TestLatestConclusiveDecision:
    def test_tentative_never_selected(self):
        decisions = [
            decision("d1", status=OrganizationalDecisionStatus.TENTATIVE),
            decision("d2", status=OrganizationalDecisionStatus.UNDER_DISCUSSION),
            decision("d3", status=OrganizationalDecisionStatus.PROPOSED),
        ]
        assert get_latest_conclusive_decision(
            decisions=decisions, workstream_id=WS, as_of=AS_OF,
        ) is None

    def test_newer_opinion_does_not_supersede_conclusive(self):
        # Conclusive decision Aug 8; random opinion Aug 9 arrives as an
        # under-discussion record. The Aug 8 decision stays authoritative.
        conclusive = decision("d-aug8", decided_days_ago=2)
        opinion = decision(
            "d-aug9-opinion",
            status=OrganizationalDecisionStatus.UNDER_DISCUSSION,
        )
        latest = get_latest_conclusive_decision(
            decisions=[conclusive, opinion], workstream_id=WS, as_of=AS_OF,
        )
        assert latest is not None
        assert latest.id == "d-aug8"

    def test_explicit_supersede_replaces_old(self):
        old = decision("d-old", decided_days_ago=10)
        new = decision("d-new", decided_days_ago=1)
        old, new = supersede_decision(old=old, new=new)
        latest = get_latest_conclusive_decision(
            decisions=[old, new], workstream_id=WS, as_of=AS_OF,
        )
        assert latest.id == "d-new"
        assert old.status == OrganizationalDecisionStatus.SUPERSEDED
        assert old.superseded_by_decision_id == "d-new"
        assert new.supersedes_decision_id == "d-old"

    def test_reopened_superseder_restores_original(self):
        old = decision("d-old", decided_days_ago=10)
        new = decision("d-new", decided_days_ago=1)
        old, new = supersede_decision(old=old, new=new)
        reopened_new = reopen_decision(
            decision=new, reason="Prepaid validation results disputed",
            evidence_ids=["ev-dispute"],
        )
        latest = get_latest_conclusive_decision(
            decisions=[old, reopened_new], workstream_id=WS, as_of=AS_OF,
        )
        # Supersession void while the superseder is reopened.
        assert latest is not None
        assert latest.id == "d-old"
        assert reopened_new.status == OrganizationalDecisionStatus.REOPENED
        assert "ev-dispute" in reopened_new.evidence_ids

    def test_future_decision_ignored(self):
        future = decision("d-future", decided_days_ago=-3)
        past = decision("d-past", decided_days_ago=5)
        latest = get_latest_conclusive_decision(
            decisions=[future, past], workstream_id=WS, as_of=AS_OF,
        )
        assert latest.id == "d-past"

    def test_most_recent_effective_wins(self):
        older = decision("d-older", decided_days_ago=9)
        newer = decision("d-newer", decided_days_ago=1)
        latest = get_latest_conclusive_decision(
            decisions=[older, newer], workstream_id=WS, as_of=AS_OF,
        )
        assert latest.id == "d-newer"

    def test_other_workstream_ignored(self):
        other = decision("d-other", workstream_id="ws-search")
        assert get_latest_conclusive_decision(
            decisions=[other], workstream_id=WS, as_of=AS_OF,
        ) is None

    def test_affected_workstream_counts(self):
        cross = decision(
            "d-cross", workstream_id="ws-billing",
            affected_workstream_ids=[WS],
        )
        latest = get_latest_conclusive_decision(
            decisions=[cross], workstream_id=WS, as_of=AS_OF,
        )
        assert latest.id == "d-cross"
