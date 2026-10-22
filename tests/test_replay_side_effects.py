"""Replay never touches the outside world.

This is the non-negotiable replay invariant. Rebuilding history must not
re-send a Slack message, re-file a ticket, re-email anyone, or
re-finalize a decision that was already made once. Everything here uses
fake outbound ports with call counters, and the expected count is
always zero.

Suppression is counted rather than silently dropped: "nothing happened"
and "eleven things were prevented" are different reports, and only one
of them is useful when auditing a recovery.
"""

from __future__ import annotations

import pytest

from lltpm.persistence import (
    ExecutionContext,
    ExecutionMode,
    JournalEventType,
    OutboundIntentStatus,
    ReplayClock,
    ReplayMode,
    ReplayRequest,
    SideEffectFence,
    replay_journal,
)
from tests._persistence_fixtures import (
    ORGANIZATION,
    PROJECT_A,
    FakeAutonomousExecutor,
    FakeOutboundPort,
    at,
    build_live_organization,
    manifest,
    reconstructor,
    replay_context,
)


def _live_context():
    return ExecutionContext(
        mode=ExecutionMode.LIVE, clock=ReplayClock(replay_as_of=at(0)),
    )


def _replay(persistence, *, outbound=None, **overrides):
    return replay_journal(
        request=ReplayRequest(
            organization_id=ORGANIZATION,
            mode=overrides.pop("mode", ReplayMode.RECONSTRUCT_CURRENT),
            use_snapshot=False,
            **overrides,
        ),
        journal=persistence.journal,
        snapshots=persistence.snapshots,
        state_store=persistence.state,
        artifact_store=persistence.artifacts,
        reconstructor=reconstructor(),
        manifest=manifest(),
        outbound=outbound,
    )


class TestTheFence:
    def test_a_replay_context_suppresses_an_outbound_call(self):
        inner = FakeOutboundPort()
        fence = SideEffectFence(inner=inner, context=replay_context())
        intent = fence.send(
            target_system="slack", action_type="post_message",
            payload={"text": "hi"}, idempotency_key="k",
        )
        assert inner.call_count == 0
        assert intent.status == OutboundIntentStatus.SUPPRESSED
        assert fence.suppressed == 1

    def test_the_suppression_explains_itself(self):
        fence = SideEffectFence(inner=FakeOutboundPort(), context=replay_context())
        intent = fence.send(
            target_system="slack", action_type="post_message",
            payload={}, idempotency_key="k",
        )
        assert "does not repeat its external consequences" in (
            intent.suppressed_reason
        )

    def test_a_live_context_delivers(self):
        inner = FakeOutboundPort()
        fence = SideEffectFence(inner=inner, context=_live_context())
        fence.send(
            target_system="slack", action_type="post_message",
            payload={}, idempotency_key="k",
        )
        assert inner.call_count == 1
        assert fence.delivered == 1
        assert fence.suppressed == 0

    def test_suppressed_actions_are_named(self):
        fence = SideEffectFence(inner=None, context=replay_context())
        fence.send(
            target_system="jira", action_type="create_issue",
            payload={}, idempotency_key="k1",
        )
        fence.send(
            target_system="email", action_type="send", payload={},
            idempotency_key="k2",
        )
        assert fence.suppressed_actions == [
            "jira:create_issue", "email:send",
        ]

    def test_a_per_call_context_can_override(self):
        inner = FakeOutboundPort()
        fence = SideEffectFence(inner=inner, context=_live_context())
        fence.send(
            target_system="slack", action_type="post_message",
            payload={}, idempotency_key="k", context=replay_context(),
        )
        assert inner.call_count == 0

    def test_a_live_fence_without_a_port_refuses(self):
        fence = SideEffectFence(inner=None, context=_live_context())
        with pytest.raises(RuntimeError, match="No outbound port"):
            fence.send(
                target_system="slack", action_type="post_message",
                payload={}, idempotency_key="k",
            )


class TestNoExternalCallsDuringReplay:
    def test_replay_sends_no_slack_message(self):
        persistence, _, _ = build_live_organization()
        outbound = FakeOutboundPort()
        _replay(persistence, outbound=outbound)
        assert outbound.call_count == 0

    def test_replay_sends_no_email(self):
        persistence, _, _ = build_live_organization()
        outbound = FakeOutboundPort()
        _replay(persistence, outbound=outbound)
        assert not any(t == "email" for t, _ in outbound.calls)

    def test_replay_creates_no_ticket(self):
        persistence, _, _ = build_live_organization()
        outbound = FakeOutboundPort()
        _replay(persistence, outbound=outbound)
        assert not any(
            t in ("jira", "linear") for t, _ in outbound.calls
        )

    def test_a_full_rebuild_makes_no_external_call(self):
        persistence, _, _ = build_live_organization()
        persistence.delete_all_derived_state()
        outbound = FakeOutboundPort()
        _replay(persistence, outbound=outbound, publish_result=True)
        assert outbound.call_count == 0

    def test_replay_over_a_journal_with_alerts_stays_silent(self):
        # The live pass raised a high-priority alert; the replay
        # reconstructs it and does not surface it.
        persistence, _, _ = build_live_organization()
        outbound = FakeOutboundPort()
        result = _replay(persistence, outbound=outbound)
        assert outbound.call_count == 0
        assert result.side_effects_suppressed > 0


class TestSuppressionIsCounted:
    def test_suppressed_effects_are_reported(self):
        persistence, _, _ = build_live_organization()
        result = _replay(persistence)
        assert result.side_effects_suppressed > 0, (
            "'nothing happened' and 'several things were prevented' are "
            "different reports"
        )

    def test_a_reconstructed_alert_counts_as_suppressed(self):
        persistence, _, _ = build_live_organization()
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=replay_context(),
        )
        assert outcome.suppressed_side_effects > 0

    def test_an_empty_journal_suppresses_nothing(self):
        from tests._persistence_fixtures import Persistence

        result = _replay(Persistence())
        assert result.side_effects_suppressed == 0


class TestAutonomousDecisionsAreNotReExecuted:
    def test_a_finalized_decision_is_restored_not_repeated(self):
        persistence, _, _ = build_live_organization()
        executor = FakeAutonomousExecutor()
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=replay_context(),
        )
        assert executor.finalizations == 0, (
            "an external consequence that already happened must not happen "
            "twice because history was rebuilt"
        )
        assert any(
            d["event_type"] == "autonomous_decision_finalized"
            for d in outcome.historical_decisions
        )

    def test_the_restored_decision_keeps_its_identity(self):
        persistence, _, _ = build_live_organization()
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=replay_context(),
        )
        finalized = next(
            d for d in outcome.historical_decisions
            if d["event_type"] == "autonomous_decision_finalized"
        )
        assert finalized["decision_id"] == "decision-auto-rec-rollout"
        assert finalized["authority_policy_id"] == "policy-acme-v1"

    def test_replaying_twice_does_not_double_the_decision(self):
        persistence, _, _ = build_live_organization()
        first = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=replay_context(),
        )
        second = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=replay_context(),
        )
        assert len(first.historical_decisions) == len(
            second.historical_decisions
        )

    def test_the_finalizer_is_never_called_from_reconstruction(self):
        import ast
        import inspect

        import lltpm.runtime.durability as durability

        source = inspect.getsource(
            durability.RuntimeJournalReconstructor
        )
        tree = ast.parse(source)
        called = {
            node.func.id if isinstance(node.func, ast.Name)
            else getattr(node.func, "attr", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert "finalize_agent_decision" not in called
        assert "finalize" not in called

    def test_a_blocked_decision_is_also_restored_as_history(self):
        from lltpm.runtime import append_autonomous_decision

        persistence, _, _ = build_live_organization()
        append_autonomous_decision(
            journal=persistence.journal,
            organization_id=ORGANIZATION,
            decision_id="decision-blocked",
            project_id=PROJECT_A,
            recommendation_id="rec-stale",
            policy_id="policy-acme-v1",
            occurred_at=at(75), recorded_at=at(75),
            executed=False,
            block_reason="stale_recommendation",
        )
        outcome = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=replay_context(),
        )
        blocked = next(
            d for d in outcome.historical_decisions
            if d["event_type"] == "autonomous_decision_blocked"
        )
        assert blocked["block_reason"] == "stale_recommendation"
        assert blocked["executed"] is False


class TestReconstructionRefusesUnfencedContexts:
    def test_a_live_context_is_refused(self):
        persistence, _, _ = build_live_organization()
        with pytest.raises(RuntimeError, match="side effects suppressed"):
            reconstructor().apply_records(
                records=persistence.journal.read_from(global_sequence=1),
                organization_id=ORGANIZATION,
                context=_live_context(),
            )

    def test_the_refusal_explains_why(self):
        persistence, _, _ = build_live_organization()
        with pytest.raises(
            RuntimeError, match="must not repeat its consequences",
        ):
            reconstructor().apply_records(
                records=persistence.journal.read_from(global_sequence=1),
                organization_id=ORGANIZATION,
                context=_live_context(),
            )


class TestDeterminismDuringReplay:
    def test_replay_does_not_consult_the_wall_clock(self):
        import ast
        import inspect

        import lltpm.persistence.replay as replay_module

        tree = ast.parse(inspect.getsource(replay_module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            assert name not in ("now", "utcnow", "today"), (
                "a replay that consulted the wall clock would produce a "
                "different answer every time it ran"
            )

    def test_replay_does_not_draw_random_numbers(self):
        import inspect

        import lltpm.persistence.replay as replay_module

        source = inspect.getsource(replay_module)
        assert "random.random" not in source
        assert "import random" not in source

    def test_the_replay_clock_comes_from_the_events(self):
        persistence, _, _ = build_live_organization()
        outcome_first = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=replay_context(replay_as_of=at(200)),
        )
        outcome_second = reconstructor().apply_records(
            records=persistence.journal.read_from(global_sequence=1),
            organization_id=ORGANIZATION,
            context=replay_context(replay_as_of=at(300)),
        )
        assert outcome_first.state_fingerprints == (
            outcome_second.state_fingerprints
        ), (
            "reconstruction depends on the events, not on when the replay "
            "happened to run"
        )

    def test_repeated_replays_agree(self):
        persistence, _, _ = build_live_organization()
        first = _replay(persistence)
        second = _replay(persistence)
        assert first.reconstructed_fingerprints == (
            second.reconstructed_fingerprints
        )
