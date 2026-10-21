"""Where the no-worker-scoring invariant actually holds (Commit 40).

The project states, in docs/assistant-development-guide.md and in the README, that it models
organizational mechanics and never individual productivity, and that
source-level guardrail tests enforce it. The first half is the design
intent. The second half was not true as written: the existing guardrails
enumerate the runtime modules by hand, so they cover the layered
organizational-reasoning stack and say nothing about the original TPM
surface — which contains per-person response rates, responsiveness
grades and a quarterly evaluation report with four letter grades,
exposed to the agent as callable tools.

This module replaces the claim with a measured one. It detects the
*shape* of per-person measurement rather than a vocabulary — a model
whose declared subject is a person and which also carries a grade, a
score, a rate or an hours count — because the three previous attempts at
vocabulary matching in this codebase each flagged something innocent
(a rendered timestamp, a docstring, ordinary list building) and had to
be narrowed. Shape is harder to get wrong: ``sprint.utilization``
measures a sprint, and no term list can see that.

Two assertions carry the weight:

- the layered stack has **no** per-person measurement at all, which is
  the invariant as stated and is true;
- every class in the legacy surface that does measure a person is
  enumerated here with a justification, and the set must match exactly
  — so nothing new joins it quietly and the inventory cannot rot.

What this file does not do is decide whether the legacy surface should
exist. That is a product question about a shipped feature, and the
honest engineering answer is to make the contradiction impossible to
miss rather than to resolve it unilaterally.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "lltpm"

#: A field name that declares the subject of a model to be a person.
#: Deliberately exact rather than substring: ``actor_id`` on an
#: authority reference names who holds a decision right, which is
#: organizational structure, not measurement.
PERSON_SUBJECT_FIELDS = frozenset({
    "person", "employee", "employee_id", "assignee", "individual",
})

#: Field-name fragments that make a field a measurement of that subject.
MEASUREMENT_HINTS = (
    "grade", "score", "rate", "hours", "efficiency", "productivity",
    "utilization", "velocity", "ranking", "metrics", "responses",
    "ignored", "missed", "late", "overdue", "blocker",
)

#: The layered organizational-reasoning stack. The invariant holds here
#: completely, and this is the part the documentation is about.
LAYERED_STACK = (
    "core", "temporal", "identity", "structural", "persistence",
    "intelligence", "coordination", "scenarios", "portfolio", "memory",
    "evaluation", "application", "runtime", "observability", "ontology",
    "advisory",
)

#: Legacy classes that genuinely measure a person. Each entry is a
#: statement that this contradicts the product guardrail, not a licence.
PERSON_MEASUREMENT_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("models/calendar.py", "AttendeeInfo"): (
        "engagement_score rates an attendee. Predates the guardrail; used "
        "to decide who needs a meeting rather than to evaluate anyone, but "
        "it is still a per-person score."
    ),
    ("models/engagement.py", "EngagementProfile"): (
        "Per-person response rate, response hours, ignored-message count "
        "and a responsiveness grade. Its stated purpose is choosing when "
        "and how to contact someone, which is a different purpose from "
        "evaluation — and the same data."
    ),
    ("models/prediction.py", "PersonMetrics"): (
        "Per-person response rate, commitments missed and late, times they "
        "were the blocker, delegations overdue. This is individual "
        "productivity measurement by any reading."
    ),
    ("models/prediction.py", "QuarterlyEvaluation"): (
        "A quarterly performance review: responsiveness, reliability, "
        "collaboration and overall letter grades, strengths, areas for "
        "improvement and a trend. Directly contradicts the guardrail, and "
        "is reachable from the agent through generate_evaluation."
    ),
    ("models/trends.py", "PersonTrend"): (
        "Per-person response rate and response hours over 7 and 30 days, "
        "with an is_degrading flag. Behavioural tracking over time."
    ),
}

#: Classes the detector flags whose subject field is a person but whose
#: measurements are of the *work*, not the worker. Listed with the reason
#: rather than tuning the detector until they vanish — a detector bent to
#: produce a clean result stops being evidence of anything.
MEASURES_THE_WORK_NOT_THE_WORKER: dict[tuple[str, str], str] = {
    ("models/task_board.py", "BoardTask"): (
        "effort_hours and actual_hours estimate the task; is_overdue is a "
        "property of the task's due date. 'assignee' names who holds it."
    ),
    ("models/notifications.py", "NotificationPreference"): (
        "quiet_hours is a stated preference about when not to be "
        "contacted, not a measurement of anybody."
    ),
}


def _model_fields(node: ast.ClassDef) -> list[str]:
    """Annotated fields and properties a class declares."""
    names: list[str] = []
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(
            child.target, ast.Name
        ):
            names.append(child.target.id)
        elif isinstance(child, ast.FunctionDef) and any(
            isinstance(decorator, ast.Name) and decorator.id == "property"
            for decorator in child.decorator_list
        ):
            names.append(child.name)
    return names


def find_person_measurement_classes() -> dict[tuple[str, str], list[str]]:
    """Every class whose subject is a person and which measures them."""
    found: dict[tuple[str, str], list[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            names = _model_fields(node)
            if not set(names) & PERSON_SUBJECT_FIELDS:
                continue
            measures = sorted({
                name for name in names
                if any(hint in name.lower() for hint in MEASUREMENT_HINTS)
            })
            if measures:
                key = (str(path.relative_to(_SRC)), node.name)
                found[key] = measures
    return found


@pytest.fixture(scope="module")
def flagged() -> dict[tuple[str, str], list[str]]:
    return find_person_measurement_classes()


class TestTheDetectorWorks:
    def test_it_finds_the_known_evaluation_model(self, flagged):
        """If this stops matching, the detector has gone blind and every
        assertion below becomes vacuous."""
        assert ("models/prediction.py", "QuarterlyEvaluation") in flagged

    def test_it_does_not_flag_sprint_capacity(self, flagged):
        """`sprint.utilization` is committed work over capacity — an
        organizational measure. No vocabulary list can tell that apart;
        shape can."""
        assert not any(
            name == "Sprint" for _, name in flagged
        )

    def test_it_does_not_flag_an_authority_reference(self, flagged):
        """`actor_id` names who holds a decision right. That is
        structure, and the earlier vocabulary-based guardrails kept
        catching it."""
        assert not any(
            name in {"ActorReference", "OrganizationalActorRef"}
            for _, name in flagged
        )


class TestTheLayeredStackIsClean:
    """The invariant as documented, and it holds here."""

    @pytest.mark.parametrize("package", LAYERED_STACK)
    def test_no_package_measures_a_person(self, flagged, package):
        offenders = sorted(
            f"{path}:{name}" for path, name in flagged
            if path.startswith(f"{package}/")
        )
        assert offenders == [], (
            f"{offenders} measure a person inside '{package}'. The "
            f"organizational-reasoning stack models capacity, "
            f"dependencies, authority and information flow — never what "
            f"an individual did."
        )

    def test_the_new_integration_path_is_clean(self, flagged):
        """Commit 37's normalizers carry an author's handle so identity
        resolution can work on it. Carrying a handle is not measuring
        the person it names."""
        offenders = [
            f"{path}:{name}" for path, name in flagged
            if path.startswith("integrations/")
        ]
        assert offenders == []


class TestTheLegacySurfaceIsEnumerated:
    """Not resolved — inventoried, so it cannot be forgotten."""

    def test_every_flagged_class_is_accounted_for(self, flagged):
        known = set(PERSON_MEASUREMENT_EXCEPTIONS) | set(
            MEASURES_THE_WORK_NOT_THE_WORKER
        )
        unaccounted = sorted(
            f"{path}:{name}" for path, name in set(flagged) - known
        )
        assert unaccounted == [], (
            f"{unaccounted} measure a person and are not in this file's "
            f"inventory. Either the code should not do that, or the entry "
            f"belongs here with a justification — silence is the one "
            f"option the project's stated guardrail rules out."
        )

    def test_the_inventory_has_not_gone_stale(self, flagged):
        known = set(PERSON_MEASUREMENT_EXCEPTIONS) | set(
            MEASURES_THE_WORK_NOT_THE_WORKER
        )
        gone = sorted(f"{path}:{name}" for path, name in known - set(flagged))
        assert gone == [], (
            f"{gone} are listed here but no longer exist. An inventory "
            f"nobody prunes stops being an inventory."
        )

    def test_the_count_is_what_the_documentation_claims(self, flagged):
        assert len(PERSON_MEASUREMENT_EXCEPTIONS) == 5, (
            "the README and the research note state how many legacy "
            "classes measure a person; this is the number they cite"
        )

    @pytest.mark.parametrize(
        "key", sorted(PERSON_MEASUREMENT_EXCEPTIONS),
        ids=lambda key: f"{key[0]}:{key[1]}",
    )
    def test_each_exception_says_what_it_is(self, key):
        justification = PERSON_MEASUREMENT_EXCEPTIONS[key]
        assert len(justification) > 80, (
            f"{key} needs a real justification; a one-word note turns an "
            f"inventory back into a silence"
        )

    @pytest.mark.parametrize(
        "key", sorted(MEASURES_THE_WORK_NOT_THE_WORKER),
        ids=lambda key: f"{key[0]}:{key[1]}",
    )
    def test_each_benign_entry_says_why(self, key):
        assert len(MEASURES_THE_WORK_NOT_THE_WORKER[key]) > 80

    def test_the_exceptions_are_confined_to_the_original_surface(self):
        """A new module may not join this list. The invariant holds going
        forward whatever is decided about what already exists."""
        allowed_roots = {"models", "workflows", "tools"}
        for path, name in PERSON_MEASUREMENT_EXCEPTIONS:
            root = path.split("/")[0]
            assert root in allowed_roots, (
                f"{path}:{name} is outside the original TPM surface; the "
                f"guardrail is not negotiable for new code"
            )


class TestTheAgentSurfaceIsNamed:
    """The part that matters most: what the model can actually call."""

    def test_the_evaluation_tool_is_known_to_exist(self):
        source = (_SRC / "tools" / "prediction_tools.py").read_text()
        assert "generate_evaluation" in source, (
            "if this tool is removed, update the README, the research "
            "note and this test together — the claim and the code have "
            "to move at the same time"
        )

    def test_no_tool_module_outside_prediction_tools_grades_a_person(self):
        offenders = []
        for path in sorted((_SRC / "tools").glob("*.py")):
            if path.name == "prediction_tools.py":
                continue
            source = path.read_text().lower()
            for term in ("performance_grade", "quarterly_evaluation",
                         "productivity", "employee_score"):
                if term in source:
                    offenders.append(f"{path.name}:{term}")
        assert offenders == [], (
            f"{offenders} — person grading must not spread to another "
            f"tool module"
        )
