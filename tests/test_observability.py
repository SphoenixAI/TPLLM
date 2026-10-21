"""Tests for Commit 36 — operational logging.

Three things are being protected here, and only the first is about
formatting.

**Logs must not become a second journal.** The journal is authoritative,
hash-chained and replayed; a log line is none of those. So the tests
check that logging is additive — turning it on or off changes no result
— and that nothing in the stack configures handlers behind an
application's back.

**Logs must not become a credential leak.** The journal refuses secret
material; a log line redacts it, because a formatter that raised would
take down the operation it was describing. Both behaviours are pinned.

**Logs must not become surveillance.** LLTPM models organizational
mechanics and never individual activity, and an observability layer is
the easiest place for that to erode by accident. The field vocabulary is
a closed list and a source guardrail walks every log call in the package
to enforce it.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import pathlib

import pytest

from lltpm.observability import (
    REDACTED,
    RESERVED_RECORD_ATTRIBUTES,
    SECRET_KEY_FRAGMENTS,
    STRUCTURED_FIELD_NAMES,
    HumanReadableFormatter,
    JsonLinesFormatter,
    LogFormat,
    configure_logging,
    extra_fields,
    is_secret_field_name,
    redact_fields,
    redact_value,
    resolve_level,
)
from lltpm.observability.fields import record_fields
from lltpm.observability.redaction import MAX_DEPTH, looks_like_secret_value

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "lltpm"

_LOG_METHODS = frozenset({
    "debug", "info", "warning", "error", "critical", "exception", "log",
})


def _record(
    *, level: int = logging.INFO, message: str = "hello", **fields,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="lltpm.test", level=level, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )
    for name, value in fields.items():
        setattr(record, name, value)
    return record


# ── The JSON formatter ───────────────────────────────────────────────


class TestJsonLinesFormatter:
    def test_one_line_of_valid_json(self):
        rendered = JsonLinesFormatter().format(_record())
        assert "\n" not in rendered
        assert json.loads(rendered)["message"] == "hello"

    def test_the_fixed_keys_come_first_and_in_order(self):
        document = json.loads(JsonLinesFormatter().format(_record()))
        assert list(document)[:4] == [
            "timestamp", "level", "logger", "message",
        ]

    def test_the_timestamp_is_utc_and_explicit_about_it(self):
        document = json.loads(JsonLinesFormatter().format(_record()))
        assert document["timestamp"].endswith("Z"), (
            "a log line without a zone is unjoinable with anything else"
        )

    def test_structured_fields_are_promoted_to_top_level(self):
        document = json.loads(JsonLinesFormatter().format(
            _record(revision_id="revision-7", journal_sequence=12)
        ))
        assert document["revision_id"] == "revision-7"
        assert document["journal_sequence"] == 12

    def test_fields_are_ordered_so_two_runs_diff_cleanly(self):
        first = JsonLinesFormatter().format(
            _record(revision_id="r1", artifact_type="kpi_state")
        )
        second = JsonLinesFormatter().format(
            _record(artifact_type="kpi_state", revision_id="r1")
        )
        assert list(json.loads(first))[4:] == list(json.loads(second))[4:]

    def test_a_field_cannot_displace_the_fixed_keys(self):
        record = _record()
        record.__dict__["message"] = "spoofed"
        document = json.loads(JsonLinesFormatter().format(record))
        assert document["message"] == "hello"

    def test_an_exception_is_carried_as_its_own_field(self):
        try:
            raise ValueError("journal is unreachable")
        except ValueError:
            import sys
            record = _record(level=logging.ERROR)
            record.exc_info = sys.exc_info()
        document = json.loads(JsonLinesFormatter().format(record))
        assert "ValueError: journal is unreachable" in document["exception"]

    def test_a_non_serializable_value_does_not_break_the_line(self):
        document = json.loads(JsonLinesFormatter().format(
            _record(reason=object())
        ))
        assert isinstance(document["reason"], str)


class TestHumanReadableFormatter:
    def test_it_renders_one_line_with_the_fields_appended(self):
        rendered = HumanReadableFormatter().format(
            _record(revision_id="revision-3")
        )
        assert "lltpm.test hello" in rendered
        assert "revision_id=revision-3" in rendered

    def test_no_fields_means_no_empty_brackets(self):
        assert "[" not in HumanReadableFormatter().format(_record())

    def test_it_redacts_exactly_as_the_json_formatter_does(self):
        rendered = HumanReadableFormatter().format(
            _record(reason="Bearer abc.def")
        )
        assert "abc.def" not in rendered
        assert REDACTED in rendered


# ── Redaction ────────────────────────────────────────────────────────


class TestRedaction:
    @pytest.mark.parametrize(
        "name",
        ["token", "access_token", "slack_bot_token", "api_key", "API_KEY",
         "Authorization", "webhook_secret", "signing_key", "password",
         "private_key", "session_cookie", "credential_id"],
    )
    def test_credential_field_names_are_redacted(self, name):
        assert is_secret_field_name(name)
        assert redact_fields({name: "sensitive"})[name] == REDACTED

    @pytest.mark.parametrize(
        "name",
        ["revision_id", "journal_sequence", "project_id", "artifact_type",
         "reason", "evidence_count"],
    )
    def test_ordinary_field_names_survive(self, name):
        assert not is_secret_field_name(name)
        assert redact_fields({name: "kept"})[name] == "kept"

    @pytest.mark.parametrize(
        "value",
        ["ghp_abcdefghijklmnop", "xoxb-1-2-abcdef", "Bearer abcdef",
         "-----BEGIN RSA PRIVATE KEY-----", "sk-ant-api03-xyz",
         "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"],
    )
    def test_credential_values_are_redacted_whatever_the_field_is_called(
        self, value,
    ):
        assert looks_like_secret_value(value)
        assert redact_fields({"reason": value})["reason"] == REDACTED

    def test_a_sentence_mentioning_a_token_is_not_itself_a_token(self):
        explanation = "the slack call failed; refresh the credentials"
        assert redact_fields({"reason": explanation})["reason"] == explanation

    def test_nested_structures_are_walked(self):
        redacted = redact_value(
            {"outer": [{"api_key": "abc", "project_id": "atlas"}]}
        )
        assert redacted["outer"][0]["api_key"] == REDACTED
        assert redacted["outer"][0]["project_id"] == "atlas"

    def test_a_pathological_depth_terminates_instead_of_recursing(self):
        node: dict = {"leaf": "value"}
        for _ in range(MAX_DEPTH + 4):
            node = {"nest": node}
        rendered = json.dumps(redact_value(node))
        assert "depth>" in rendered

    def test_a_self_referential_structure_does_not_hang(self):
        node: dict = {}
        node["self"] = node
        assert "depth>" in json.dumps(redact_value(node))

    def test_a_value_whose_own_repr_raises_cannot_take_down_the_caller(self):
        class Awkward:
            def __repr__(self):
                raise RuntimeError("not even repr works")

        record = _record(reason=Awkward())
        # Losing the fields is acceptable. Losing the operation the line
        # was merely describing is not.
        document = json.loads(JsonLinesFormatter().format(record))
        assert document["message"] == "hello"
        assert "log_error" in document
        assert "unrenderable" in HumanReadableFormatter().format(record)

    def test_a_formatted_line_carries_no_credential(self):
        document = json.loads(JsonLinesFormatter().format(
            _record(reason="ghp_0123456789abcdef", idempotency_key="k-1")
        ))
        assert "ghp_" not in json.dumps(document)
        assert document["idempotency_key"] == "k-1"


class TestRedactionAgreesWithTheJournal:
    """The journal owns the same vocabulary and must not import this one.

    ``persistence`` imports no first-party package, so sharing the list
    would mean breaking a boundary that exists for a better reason. The
    duplication is deliberate; this test is what keeps it honest.
    """

    def test_every_name_the_journal_refuses_is_also_redacted(self):
        from lltpm.persistence.models import _SECRET_KEY_MARKERS

        for marker in _SECRET_KEY_MARKERS:
            assert is_secret_field_name(marker), (
                f"the journal refuses a payload key containing '{marker}' "
                f"but a log line would print it"
            )

    def test_every_value_the_journal_refuses_is_also_redacted(self):
        from lltpm.persistence.models import _SECRET_VALUE_MARKERS

        for marker in _SECRET_VALUE_MARKERS:
            assert looks_like_secret_value(f"{marker}rest-of-the-value")

    def test_the_log_vocabulary_is_strictly_wider(self):
        """Logs see fields the journal never would — headers, cookies,
        passphrases from a misconfigured integration — so the redaction
        list is a superset rather than a copy."""
        from lltpm.persistence.models import _SECRET_KEY_MARKERS

        assert set(SECRET_KEY_FRAGMENTS) > set(_SECRET_KEY_MARKERS)


# ── The field vocabulary ─────────────────────────────────────────────


class TestFieldVocabulary:
    def test_no_structured_field_collides_with_a_record_attribute(self):
        collisions = STRUCTURED_FIELD_NAMES & RESERVED_RECORD_ATTRIBUTES
        assert collisions == frozenset(), (
            f"{sorted(collisions)} would raise KeyError at the call site"
        )

    def test_the_reserved_set_is_derived_rather_than_transcribed(self):
        for attribute in ("module", "args", "levelname", "message", "name"):
            assert attribute in RESERVED_RECORD_ATTRIBUTES

    def test_extra_fields_refuses_a_reserved_name(self):
        with pytest.raises(ValueError, match="LogRecord attributes"):
            extra_fields(module="sneaky")

    def test_extra_fields_passes_ordinary_names_through(self):
        assert extra_fields(revision_id="r1") == {"revision_id": "r1"}

    def test_record_fields_returns_only_what_a_call_site_attached(self):
        fields = record_fields(_record(revision_id="r1"))
        assert fields == {"revision_id": "r1"}

    def test_the_vocabulary_has_no_field_for_individual_activity(self):
        banned = (
            "productivity", "activity", "keystroke", "hours_worked",
            "utilization", "efficiency", "screen", "browser", "timesheet",
            "actor_id", "employee", "user_name",
        )
        offenders = sorted(
            name for name in STRUCTURED_FIELD_NAMES
            if any(word in name for word in banned)
        )
        assert offenders == [], (
            f"{offenders} would make the log a record of people rather "
            f"than of organizational mechanics"
        )

    def test_the_vocabulary_has_no_field_for_evidence_content(self):
        for name in ("content", "normalized_content", "message_text", "body"):
            assert name not in STRUCTURED_FIELD_NAMES


# ── Configuration ────────────────────────────────────────────────────


@pytest.fixture
def restore_logging():
    logger = logging.getLogger("lltpm")
    before = (list(logger.handlers), logger.level, logger.propagate)
    yield logger
    logger.handlers, logger.level, logger.propagate = (
        before[0], before[1], before[2],
    )


class TestConfiguration:
    def test_it_installs_exactly_one_handler_of_its_own(
        self, restore_logging,
    ):
        """Counted by tag, not by total: another command group may have
        configured logging earlier in the run, and this call replaces
        that handler rather than adding to it."""
        configure_logging(level="info", stream=io.StringIO())
        tagged = [
            handler for handler in restore_logging.handlers
            if getattr(handler, "_lltpm_tag", None)
        ]
        assert len(tagged) == 1

    def test_calling_it_twice_does_not_double_every_line(
        self, restore_logging,
    ):
        stream = io.StringIO()
        configure_logging(level="info", stream=stream)
        configure_logging(level="info", stream=stream)
        logging.getLogger("lltpm.test").info("once")
        assert stream.getvalue().count("once") == 1

    def test_it_leaves_the_root_logger_alone(self, restore_logging):
        root_before = list(logging.getLogger().handlers)
        configure_logging(level="debug", stream=io.StringIO())
        assert logging.getLogger().handlers == root_before, (
            "a library that reconfigures root decides how its host logs"
        )

    def test_json_format_emits_parseable_lines(self, restore_logging):
        stream = io.StringIO()
        configure_logging(
            level="info", log_format=LogFormat.JSON, stream=stream,
        )
        logging.getLogger("lltpm.test").info(
            "structured", extra={"revision_id": "revision-9"},
        )
        document = json.loads(stream.getvalue().strip())
        assert document["revision_id"] == "revision-9"

    def test_the_level_is_respected(self, restore_logging):
        stream = io.StringIO()
        configure_logging(level="warning", stream=stream)
        logger = logging.getLogger("lltpm.test")
        logger.info("quiet")
        logger.warning("loud")
        assert "quiet" not in stream.getvalue()
        assert "loud" in stream.getvalue()

    def test_off_silences_even_critical(self, restore_logging):
        stream = io.StringIO()
        configure_logging(level="off", stream=stream)
        logging.getLogger("lltpm.test").critical("not wanted")
        assert stream.getvalue() == ""

    def test_an_unknown_level_is_refused_by_name(self):
        with pytest.raises(ValueError, match="Unknown log level"):
            resolve_level("chatty")

    def test_a_numeric_level_passes_through(self):
        assert resolve_level(logging.DEBUG) == logging.DEBUG

    def test_an_unknown_format_is_refused(self, restore_logging):
        with pytest.raises(ValueError):
            configure_logging(log_format="yaml", stream=io.StringIO())


class TestLibrariesDoNotConfigureLogging:
    """Only an application may install a handler.

    Every module in the package emits through ``getLogger(__name__)``
    and nothing else, so embedding LLTPM leaves its host's logging setup
    untouched. The CLI is the single exception, by design.
    """

    ALLOWED_CONFIGURERS = {
        "observability/configuration.py",
        "runtime_cli.py",
        "__init__.py",  # the package's own NullHandler
    }

    def _calls(self, attribute: str) -> list[str]:
        found = []
        for path in sorted(_SRC.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == attribute
                ):
                    found.append(str(path.relative_to(_SRC)))
        return sorted(set(found))

    def test_nothing_calls_basic_config(self):
        assert self._calls("basicConfig") == []

    def test_only_the_logging_layer_adds_a_handler(self):
        offenders = [
            path for path in self._calls("addHandler")
            if path not in self.ALLOWED_CONFIGURERS
            and pathlib.Path(path).name != "__init__.py"
        ]
        assert offenders == [], (
            f"{offenders} install log handlers; that is an application's "
            f"decision, not a library's"
        )

    def test_no_domain_package_imports_the_logging_layer(self):
        """A log call site needs the standard library and nothing else.

        ``persistence`` could not import this package without breaking
        its own boundary, and the rest have no reason to.
        """
        offenders = []
        for path in sorted(_SRC.rglob("*.py")):
            relative = path.relative_to(_SRC)
            # Top-level modules are application wiring by construction —
            # the CLI groups and their shared plumbing — and the
            # architecture tests do not treat them as packages either.
            # The invariant is about packages: a domain must be able to
            # log without taking on a dependency.
            if len(relative.parts) == 1:
                continue
            if relative.parts[0] == "observability":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(a.name for a in node.names)
                if any(m.startswith("lltpm.observability") for m in modules):
                    offenders.append(str(relative))
        assert sorted(set(offenders)) == []


# ── The source guardrail over every log call ─────────────────────────


def _log_call_fields() -> list[tuple[str, int, str]]:
    """Every (file, line, field name) passed as ``extra`` in the package."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra":
                    continue
                if not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(
                        key.value, str
                    ):
                        found.append((
                            str(path.relative_to(_SRC)), node.lineno, key.value,
                        ))
    return found


class TestEveryLogCallSite:
    """Walks the real source, so a future call site is covered too."""

    def test_the_guardrail_actually_finds_call_sites(self):
        assert len(_log_call_fields()) > 10, (
            "the walker found almost nothing; it has stopped checking "
            "anything"
        )

    def test_no_call_site_uses_a_reserved_record_attribute(self):
        offenders = [
            (path, line, field) for path, line, field in _log_call_fields()
            if field in RESERVED_RECORD_ATTRIBUTES
        ]
        assert offenders == [], (
            f"{offenders} would raise KeyError inside logging at runtime"
        )

    def test_every_call_site_uses_the_agreed_vocabulary(self):
        offenders = sorted({
            f"{path}:{line} {field}"
            for path, line, field in _log_call_fields()
            if field not in STRUCTURED_FIELD_NAMES
        })
        assert offenders == [], (
            f"{offenders} are outside STRUCTURED_FIELD_NAMES — either add "
            f"the field to the vocabulary deliberately or use the existing "
            f"name, because three spellings of one idea make the fields "
            f"unqueryable"
        )

    def test_no_call_site_logs_individual_activity(self):
        banned = (
            "productivity", "keystroke", "hours_worked", "utilization",
            "efficiency", "screen_activity", "browser_activity",
            "timesheet", "employee",
        )
        offenders = sorted({
            f"{path}:{line} {field}"
            for path, line, field in _log_call_fields()
            if any(word in field for word in banned)
        })
        assert offenders == [], (
            f"{offenders} would turn observability into activity tracking"
        )

    def test_no_call_site_logs_evidence_content(self):
        banned = {"content", "normalized_content", "payload", "body", "text"}
        offenders = sorted({
            f"{path}:{line} {field}"
            for path, line, field in _log_call_fields()
            if field in banned
        })
        assert offenders == [], (
            f"{offenders} would copy evidence out of the journal and into "
            f"a transient log that has none of its guarantees"
        )
