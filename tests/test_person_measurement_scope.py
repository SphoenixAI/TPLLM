"""Where the no-worker-scoring invariant holds.

An audit found that the documented guarantee was scoped wrongly: the
existing guardrails enumerated runtime modules by hand, so they covered
the layered stack and said nothing about the original TPM surface,
which graded individuals. It inventoried five classes rather than
resolving them, because deleting a shipped feature is its owner's
decision.

That decision was made: the grading surface is gone.
``PerformanceGrade``, ``PersonMetrics``, ``QuarterlyEvaluation``,
``ResponsivenessGrade``, ``PersonTrend`` and
``AttendeeInfo.engagement_score`` were removed, along with the two agent
tools, the dashboard endpoint and the two risk models that predicted
people rather than structure. The invariant is now one statement about
the whole system rather than two about different halves of it.

This module is what keeps it that way. It detects the *shape* of
per-person measurement rather than a vocabulary — a model whose declared
subject is a person and which also carries a grade, a score, a rate or
an hours count — because the three previous attempts at vocabulary
matching in this codebase each flagged something innocent (a rendered
timestamp, a docstring, ordinary list building) and had to be narrowed.
Shape is harder to get wrong: ``sprint.utilization`` measures a sprint,
and no term list can see that.

What remains flagged measures the *work* or states a *preference*, not
the worker, and each is listed below with the reason. One borderline
case is kept deliberately and argued rather than hidden: response
latency on a routing profile.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tpllm"

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

#: Classes that measure a person. This list was emptied. It stays here
#: as the thing the test asserts remains empty — an exception list with
#: no mechanism to keep it empty is a comment.
PERSON_MEASUREMENT_EXCEPTIONS: dict[tuple[str, str], str] = {}

#: Classes the detector flags whose subject field is a person but which
#: measure the *work*, state a *preference*, or describe *routing*.
#: Listed with the reason rather than tuning the detector until they
#: vanish — a detector bent to produce a clean result stops being
#: evidence of anything.
MEASURES_THE_WORK_NOT_THE_WORKER: dict[tuple[str, str], str] = {
    ("models/task_board.py", "BoardTask"): (
        "effort_hours and actual_hours estimate the task; is_overdue is a "
        "property of the task's due date. 'assignee' names who holds it."
    ),
    ("models/notifications.py", "NotificationPreference"): (
        "quiet_hours is a stated preference about when not to be "
        "contacted, not a measurement of anybody."
    ),
    ("models/calendar.py", "AttendeeInfo"): (
        "preferred_hours is a working-hours window used to find a slot. "
        "engagement_score, which did rate the attendee — and made a "
        "prompt replier a *required* attendee — was removed."
    ),
    ("models/engagement.py", "EngagementProfile"): (
        "The borderline case, kept deliberately. avg/median_response_hours "
        "is how long to allow for a reply, which is a scheduling fact of "
        "the same kind as 'this approval queue takes three days'. What "
        "made the old version an evaluation was the grade on top of it, "
        "the response rate and ignored count it was computed from, and the "
        "escalation it drove — all removed. observed_responses is the "
        "sample size behind the estimate, a property of the estimate. "
        "Nothing here can rank two people: there is no rate, no count of "
        "unanswered requests, and no grade to sort on."
    ),
}

def _executable_source(path: pathlib.Path) -> str:
    """The module's code with every docstring removed, lowercased.

    A note explaining why something was deleted should not read as the
    thing still being there.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
    return ast.unparse(tree).lower()


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
    def test_it_finds_a_planted_per_person_measurement(self, tmp_path):
        """The evaluation model it used to catch is gone, so the detector
        is proved against a synthetic one instead. Without this, every
        assertion below could be passing because the walker is blind."""
        module = tmp_path / "planted.py"
        module.write_text(
            "from pydantic import BaseModel\n\n\n"
            "class Offender(BaseModel):\n"
            "    person: str\n"
            "    reliability_grade: float = 0.0\n"
        )
        import ast

        tree = ast.parse(module.read_text())
        found = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and set(_model_fields(node)) & PERSON_SUBJECT_FIELDS
            and any(
                hint in field.lower()
                for field in _model_fields(node)
                for hint in MEASUREMENT_HINTS
            )
        ]
        assert [node.name for node in found] == ["Offender"]

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
        """The normalizers carry an author's handle so identity
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

    def test_nothing_measures_a_person_anywhere(self, flagged):
        """The invariant, now as one statement about the whole system."""
        assert PERSON_MEASUREMENT_EXCEPTIONS == {}, (
            f"{sorted(PERSON_MEASUREMENT_EXCEPTIONS)} measure a person. "
            f"this list was emptied deliberately; adding to it needs "
            f"the argument that emptied it, in the other direction."
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

    def test_the_benign_entries_stay_in_the_original_surface(self):
        """A new package may not need an entry here at all."""
        allowed_roots = {"models", "workflows", "tools"}
        for path, name in MEASURES_THE_WORK_NOT_THE_WORKER:
            root = path.split("/")[0]
            assert root in allowed_roots, (
                f"{path}:{name} is outside the original TPM surface; new "
                f"code should not need an entry here"
            )


class TestTheAgentCannotGradeAnybody:
    """The part that matters most: what the model can actually call."""

    def test_no_tool_module_grades_a_person(self):
        """Checked against executable code only.

        Docstrings and comments recording *why* something was removed
        must not trip this; a reference that actually resolves must. The
        codebase already uses this technique for its other source
        guardrails, after earlier versions kept catching prose.
        """
        offenders = []
        for path in sorted((_SRC / "tools").glob("*.py")):
            source = _executable_source(path)
            for term in ("performance_grade", "quarterly_evaluation",
                         "productivity", "employee_score",
                         "responsiveness_grade", "generate_evaluation",
                         "engagement_score"):
                if term in source:
                    offenders.append(f"{path.name}:{term}")
        assert offenders == [], (
            f"{offenders} — the agent must not be able to ask for a "
            f"verdict on a colleague"
        )

    def test_the_dashboard_exposes_no_evaluation_endpoint(self):
        source = (_SRC / "dashboard.py").read_text()
        assert "/evaluations/" not in source.replace(
            "# GET /api/programs/{id}/evaluations/{person}", "",
        ), "a per-person performance review must not be served over HTTP"

    def test_no_workflow_assigns_a_grade(self):
        offenders = []
        for path in sorted((_SRC / "workflows").glob("*.py")):
            source = _executable_source(path)
            for term in ("performancegrade", "responsivenessgrade",
                         "quarterlyevaluation", "personmetrics",
                         "persontrend"):
                if term in source:
                    offenders.append(f"{path.name}:{term}")
        assert offenders == [], f"{offenders} grade an individual"
