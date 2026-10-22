"""Temporal queries and change explanation."""

from __future__ import annotations

from tpllm.temporal import (
    TemporalEventType,
    what_changed_between,
    what_was_known_at,
    what_was_true_at,
)
from tests._temporal_fixtures import PROJECT, at, event

E = TemporalEventType


class TestTruthVersusKnowledge:
    def _events(self):
        return [
            event("e-fail", E.BLOCKER_OPENED, "blk-1",
                  occurred_at=at(0), known_at=at(1.25)),
            event("e-old", E.CLAIM_SUPPORTED, "claim-1",
                  occurred_at=at(-2), known_at=at(-2)),
        ]

    def test_queries_return_different_results(self):
        moment = at(0.5)
        known = what_was_known_at(
            events=self._events(), as_of=moment, project_id=PROJECT,
        )
        true = what_was_true_at(
            events=self._events(), as_of=moment, project_id=PROJECT,
        )
        assert {e.id for e in known} == {"e-old"}
        assert {e.id for e in true} == {"e-old", "e-fail"}

    def test_project_scoping(self):
        events = self._events() + [
            event("e-other", E.CLAIM_SUPPORTED, "claim-x",
                  occurred_at=at(-2), known_at=at(-2),
                  project_id="phoenix"),
        ]
        known = what_was_known_at(
            events=events, as_of=at(5), project_id=PROJECT,
        )
        assert "e-other" not in {e.id for e in known}

    def test_unscoped_query_returns_all_projects(self):
        events = self._events() + [
            event("e-other", E.CLAIM_SUPPORTED, "claim-x",
                  occurred_at=at(-2), known_at=at(-2),
                  project_id="phoenix"),
        ]
        known = what_was_known_at(events=events, as_of=at(5))
        assert "e-other" in {e.id for e in known}


class TestWhatChangedBetween:
    def _events(self):
        return [
            event("e1", E.CLAIM_SUPPORTED, "claim-scope", known_at=at(0)),
            event("e2", E.DECISION_CONCLUSIVE, "dec-1", known_at=at(1)),
            event("e3", E.BLOCKER_OPENED, "blk-1", known_at=at(4)),
            event("e4", E.CLAIM_CONTESTED, "claim-scope", known_at=at(5)),
            event("e5", E.DECISION_REOPENED, "dec-1", known_at=at(6)),
        ]

    def _delta(self, start, end):
        return what_changed_between(
            project_id=PROJECT, temporal_events=self._events(),
            from_as_of=start, to_as_of=end,
        )

    def test_material_change_explained(self):
        delta = self._delta(at(2), at(7))
        assert delta.material is True
        assert delta.blockers_opened == ["blk-1"]
        assert delta.claims_newly_contested == ["claim-scope"]
        assert delta.decisions_removed == ["dec-1"]
        assert "blocker(s) opened" in delta.explanation
        assert "became contested" in delta.explanation
        assert "lost authority" in delta.explanation

    def test_no_material_change(self):
        delta = self._delta(at(2), at(3))
        assert delta.material is False
        assert delta.newly_known_event_ids == []
        assert "nothing material changed" in delta.explanation

    def test_new_events_without_state_change_are_reported_immaterial(self):
        events = [
            event("e1", E.CLAIM_SUPPORTED, "claim-1", known_at=at(0)),
            # Re-affirming the same claim changes no state.
            event("e2", E.CLAIM_SUPPORTED, "claim-1", known_at=at(3)),
        ]
        delta = what_changed_between(
            project_id=PROJECT, temporal_events=events,
            from_as_of=at(1), to_as_of=at(4),
        )
        assert delta.newly_known_event_ids == ["e2"]
        assert delta.material is False

    def test_decision_becoming_authoritative_is_material(self):
        delta = self._delta(at(0.5), at(2))
        assert delta.decisions_added == ["dec-1"]
        assert delta.material is True
        assert "became authoritative" in delta.explanation

    def test_delta_is_deterministic(self):
        assert self._delta(at(2), at(7)).model_dump() == (
            self._delta(at(2), at(7)).model_dump()
        )


class TestLayerBoundaries:
    def test_temporal_imports_no_other_tpllm_package(self):
        import ast
        import pathlib

        root = pathlib.Path("src/tpllm/temporal")
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for n in ast.walk(tree):
                modules = []
                if isinstance(n, ast.ImportFrom) and n.module:
                    modules.append(n.module)
                elif isinstance(n, ast.Import):
                    modules.extend(a.name for a in n.names)
                for module in modules:
                    if module.startswith("tpllm."):
                        assert module.startswith("tpllm.temporal"), (
                            f"{path.name} imports {module}; the temporal "
                            f"layer must stay at the bottom of the graph."
                        )
