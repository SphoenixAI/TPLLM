"""Tests for the Slack read-only port and the webhook signature seam.

The reader exists so the offline suite and a future live check drive the
same code. What is pinned here is therefore mostly what the reader is
not allowed to do, and the three Slack-specific traps:

- **Slack reports failure with HTTP 200.** ``ok: false`` in the body. A
  client that trusted the status code would read ``invalid_auth`` as an
  empty channel — and an empty channel is a real, meaningful answer, so
  the two must never be conflated.
- **A 429 must not spin, truncate, or report success.** It waits what
  Slack asked, bounded; retries a bounded number of times; and raises
  when the budget is gone rather than returning what it managed.
- **A page boundary is not the end of a channel.** Pagination follows
  the cursor to a declared bound, and a read stopped by that bound says
  so and carries the cursor.

The signature seam is tested even though nothing hosts webhooks yet,
because the first thing to accept a Slack delivery will otherwise
either invent its own check or skip one.
"""

from __future__ import annotations

import ast
import email.message
import hashlib
import hmac
import io
import pathlib
import urllib.error

import pytest

from tpllm.integrations.github_reader import SourceUnavailableError
from tpllm.integrations.slack_reader import (
    DEFAULT_PAGE_LIMIT,
    DEFAULT_REPLAY_WINDOW_SECONDS,
    IDENTIFYING_QUERY_PARAMETERS,
    SIGNATURE_VERSION,
    SIGNING_SECRET_ENVIRONMENT_VARIABLES,
    SLACK_METHODS,
    TOKEN_ENVIRONMENT_VARIABLES,
    PaginatedRead,
    RecordedSlackReader,
    SignatureInvalidError,
    SlackApiError,
    SlackRateLimitPolicy,
    SlackReader,
    UrllibSlackReader,
    check_slack_response,
    compute_signature,
    method_path,
    read_paginated,
    recording_key,
    signature_base_string,
    signing_secret_from_environment,
    verify_signature,
)
from tests import _slack_fixtures as fx

_SOURCE = pathlib.Path("src/tpllm/integrations/slack_reader.py")

#: 32+ bytes, per the project's convention for keys in tests.
SIGNING_SECRET = "slack-signing-secret-for-tests-0123456789"


# ── Read-only by construction ────────────────────────────────────────


class TestTheReaderIsReadOnly:
    def test_only_get_is_ever_issued(self):
        """Enforced against the source, not promised in a docstring: an
        integration that ingests evidence has no business writing to the
        system it reads."""
        tree = ast.parse(_SOURCE.read_text())
        methods = {
            keyword.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "method"
            and isinstance(keyword.value, ast.Constant)
        }
        assert methods == {"GET"}, f"found HTTP verbs {sorted(methods)}"

    @pytest.mark.parametrize("verb", ["POST", "PATCH", "PUT", "DELETE"])
    def test_no_write_verb_is_nameable_in_the_module(self, verb):
        assert verb not in _SOURCE.read_text(), (
            f"'{verb}' appears in the reader; a read-only port should not "
            f"be able to name a write"
        )

    def test_every_declared_method_is_a_slack_read_method(self):
        """Slack's write methods are named for what they do —
        ``chat.postMessage``, ``chat.delete``, ``conversations.invite``.
        None of them can appear here, so the declaration itself is part
        of the guarantee."""
        for template in SLACK_METHODS.values():
            method = template.split("?")[0]
            assert method.split(".")[-1] in {
                "history", "replies", "info", "list",
            }, f"'{method}' is not obviously a read"


# ── The token stays private ──────────────────────────────────────────


class TestTheTokenStaysPrivate:
    def test_the_token_is_not_an_attribute_anyone_can_read(self):
        reader = UrllibSlackReader(token="x" * 40)
        assert reader.authenticated is True
        assert not any(
            name for name in dir(reader)
            if name in {"token", "get_token", "bearer"}
        )

    def test_the_token_is_not_in_the_representation(self):
        reader = UrllibSlackReader(token="xoxb-s3cret-value-0123456789")
        assert "s3cret" not in repr(reader)
        assert "authenticated=True" in repr(reader)

    def test_a_token_is_read_from_the_environment(self, monkeypatch):
        for name in TOKEN_ENVIRONMENT_VARIABLES:
            monkeypatch.delenv(name, raising=False)
        assert UrllibSlackReader().authenticated is False
        monkeypatch.setenv(TOKEN_ENVIRONMENT_VARIABLES[0], "xoxb-abc")
        assert UrllibSlackReader().authenticated is True

    def test_the_tpllm_specific_variable_wins(self, monkeypatch):
        """So a deployment can scope TPLLM's token separately from
        whatever else on the host reads SLACK_BOT_TOKEN."""
        assert TOKEN_ENVIRONMENT_VARIABLES == (
            "TPLLM_SLACK_TOKEN", "SLACK_BOT_TOKEN",
        )
        monkeypatch.setenv("SLACK_BOT_TOKEN", "shared")
        monkeypatch.setenv("TPLLM_SLACK_TOKEN", "scoped")
        assert UrllibSlackReader().authenticated

    def test_a_blank_environment_token_is_not_a_token(self, monkeypatch):
        for name in TOKEN_ENVIRONMENT_VARIABLES:
            monkeypatch.setenv(name, "   ")
        assert UrllibSlackReader().authenticated is False

    def test_the_signing_secret_is_a_separate_variable(self, monkeypatch):
        """One authenticates us to Slack, the other authenticates Slack
        to us. A deployment that conflated them would accept forged
        deliveries."""
        assert not set(SIGNING_SECRET_ENVIRONMENT_VARIABLES) & set(
            TOKEN_ENVIRONMENT_VARIABLES
        )
        for name in SIGNING_SECRET_ENVIRONMENT_VARIABLES:
            monkeypatch.delenv(name, raising=False)
        assert signing_secret_from_environment() is None
        monkeypatch.setenv(SIGNING_SECRET_ENVIRONMENT_VARIABLES[0], "s")
        assert signing_secret_from_environment() == "s"

    def test_no_log_call_in_the_reader_can_reach_a_credential(self):
        """The Authorization header is in scope at the log site, so the
        line logs the method and the status and nothing else."""
        tree = ast.parse(_SOURCE.read_text())
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {
                    "debug", "info", "warning", "error", "critical",
                    "exception", "log",
                }
            ):
                continue
            rendered = ast.dump(node)
            for forbidden in (
                "headers", "_token", "signing_secret", "raw_body",
                "Authorization",
            ):
                assert forbidden not in rendered, (
                    f"a log call in the reader mentions '{forbidden}'"
                )

    def test_log_fields_come_from_the_agreed_vocabulary(self):
        """The field vocabulary is a closed list, asserted globally; this
        checks the new module before that suite has to."""
        from tpllm.observability.fields import STRUCTURED_FIELD_NAMES

        tree = ast.parse(_SOURCE.read_text())
        used: set[str] = set()
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"debug", "info", "warning", "error"}
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg == "extra" and isinstance(
                    keyword.value, ast.Dict
                ):
                    used.update(
                        key.value for key in keyword.value.keys
                        if isinstance(key, ast.Constant)
                    )
        assert used, "the walker found no log fields; it checks nothing"
        assert used <= STRUCTURED_FIELD_NAMES, (
            f"{sorted(used - STRUCTURED_FIELD_NAMES)} are not in the "
            f"structured field vocabulary"
        )


# ── ok: false inside a 200 ───────────────────────────────────────────


def _response(body: bytes, *, status: int = 200):
    class _Response:
        def __init__(self):
            self.status = status

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Response()


def _http_error(code: int, *, retry_after: str | None = None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://slack.com/api/conversations.history", code, "err",
        headers, io.BytesIO(b""),
    )


class TestSlackSignalsFailureInsideA200:
    def test_an_ok_response_passes_through(self):
        document = {"ok": True, "messages": []}
        assert check_slack_response(
            document, method="conversations.history",
        ) is document

    def test_a_not_ok_response_raises_rather_than_reading_as_empty(self):
        """The failure this exists for: ``invalid_auth`` returning an
        empty message list is indistinguishable, downstream, from a
        channel where nobody said anything."""
        with pytest.raises(SlackApiError, match="invalid_auth"):
            check_slack_response(
                {"ok": False, "error": "invalid_auth"},
                method="conversations.history",
            )

    def test_the_slack_error_code_is_carried_for_a_caller_to_act_on(self):
        with pytest.raises(SlackApiError) as caught:
            check_slack_response(
                {"ok": False, "error": "channel_not_found"},
                method="conversations.history",
            )
        assert caught.value.error == "channel_not_found"
        assert not caught.value.rate_limited

    def test_a_ratelimited_body_is_recognized_as_a_rate_limit(self):
        with pytest.raises(SlackApiError) as caught:
            check_slack_response(
                {"ok": False, "error": "ratelimited"},
                method="conversations.history",
            )
        assert caught.value.rate_limited
        assert caught.value.status == 429

    def test_a_missing_ok_is_not_treated_as_success(self):
        with pytest.raises(SlackApiError):
            check_slack_response({"messages": []}, method="x")

    def test_a_non_object_body_is_an_outage_not_a_refusal(self):
        with pytest.raises(SourceUnavailableError, match="not an object"):
            check_slack_response([{"ok": True}], method="x")

    def test_an_api_error_is_still_a_source_unavailable_error(self):
        """So a caller that only wants "we could not look" keeps
        working."""
        assert issubclass(SlackApiError, SourceUnavailableError)

    def test_the_live_reader_checks_ok_too(self, monkeypatch):
        import tpllm.integrations.slack_reader as module

        monkeypatch.setattr(
            module.urllib.request, "urlopen",
            lambda *a, **k: _response(b'{"ok": false, "error": "no_perm"}'),
        )
        with pytest.raises(SlackApiError, match="no_perm"):
            UrllibSlackReader(token="t" * 40).get("conversations.history")


# ── Rate limiting ────────────────────────────────────────────────────


class TestRateLimiting:
    @pytest.fixture
    def waits(self):
        return []

    def _reader(self, waits, responses, *, policy=None):
        """A reader whose transport answers from a queue, so a 429 can
        be exercised without a network and without real waiting."""
        queue = list(responses)

        def urlopen(*args, **kwargs):
            outcome = queue.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        reader = UrllibSlackReader(
            token="t" * 40, rate_limit_policy=policy,
            sleep=waits.append,
        )
        reader._urlopen_queue = queue
        return reader, urlopen

    def test_a_429_waits_what_slack_asked_and_then_succeeds(
        self, waits, monkeypatch,
    ):
        import tpllm.integrations.slack_reader as module

        reader, urlopen = self._reader(waits, [
            _http_error(429, retry_after="3"),
            _response(b'{"ok": true, "messages": []}'),
        ])
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        assert reader.get("conversations.history")["ok"] is True
        assert waits == [3.0], (
            "the wait must be the one the provider asked for, not a "
            "guess of our own"
        )

    def test_a_429_with_no_header_uses_the_declared_default(
        self, waits, monkeypatch,
    ):
        """Slack documents the header. This is what happens when it is
        absent, rather than retrying instantly in a tight loop."""
        import tpllm.integrations.slack_reader as module

        policy = SlackRateLimitPolicy(default_retry_after_seconds=2.5)
        reader, urlopen = self._reader(waits, [
            _http_error(429),
            _response(b'{"ok": true}'),
        ], policy=policy)
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        reader.get("conversations.history")
        assert waits == [2.5]

    def test_an_unparseable_header_falls_back_rather_than_guessing(
        self, waits, monkeypatch,
    ):
        """An HTTP-date Retry-After is legal in the RFC and is not a
        format this reader has ever observed. Guessing a wait from it
        would be worse than the declared default."""
        import tpllm.integrations.slack_reader as module

        reader, urlopen = self._reader(waits, [
            _http_error(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT"),
            _response(b'{"ok": true}'),
        ])
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        reader.get("conversations.history")
        assert waits == [1.0]

    def test_a_persistent_429_raises_rather_than_reporting_success(
        self, waits, monkeypatch,
    ):
        """The three things a naive retry loop does wrong, and the one
        that matters most: a caller handed a partial answer labelled
        success would act on it."""
        import tpllm.integrations.slack_reader as module

        policy = SlackRateLimitPolicy(max_attempts=3)
        reader, urlopen = self._reader(
            waits, [_http_error(429, retry_after="1")] * 3, policy=policy,
        )
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        with pytest.raises(SlackApiError) as caught:
            reader.get("conversations.history")
        assert caught.value.status == 429
        assert waits == [1.0, 1.0], (
            "three attempts means two waits; a third wait would be a wait "
            "nobody is going to use"
        )

    def test_the_retry_budget_is_bounded_so_it_cannot_spin(
        self, waits, monkeypatch,
    ):
        import tpllm.integrations.slack_reader as module

        policy = SlackRateLimitPolicy(max_attempts=2)
        reader, urlopen = self._reader(
            waits, [_http_error(429, retry_after="1")] * 10, policy=policy,
        )
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        with pytest.raises(SlackApiError):
            reader.get("conversations.history")
        assert len(waits) == 1
        assert len(reader._urlopen_queue) == 8, (
            "two attempts were made and no more"
        )

    def test_one_attempt_means_never_retry(self, waits, monkeypatch):
        """A legitimate choice for an interactive command, which should
        fail rather than appear to hang."""
        import tpllm.integrations.slack_reader as module

        policy = SlackRateLimitPolicy(max_attempts=1)
        reader, urlopen = self._reader(
            waits, [_http_error(429, retry_after="1")], policy=policy,
        )
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        with pytest.raises(SlackApiError):
            reader.get("conversations.history")
        assert waits == []

    def test_a_wait_beyond_the_bound_is_refused_not_slept(
        self, waits, monkeypatch,
    ):
        """Slack has been known to ask for minutes on a throttled
        method. Clamping and waiting would make the command appear to
        hang; failing says what happened."""
        import tpllm.integrations.slack_reader as module

        policy = SlackRateLimitPolicy(max_wait_seconds=10.0)
        reader, urlopen = self._reader(
            waits, [_http_error(429, retry_after="600")], policy=policy,
        )
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        with pytest.raises(SlackApiError, match="beyond the 10s"):
            reader.get("conversations.history")
        assert waits == []

    def test_a_non_rate_limit_error_is_not_retried(
        self, waits, monkeypatch,
    ):
        import tpllm.integrations.slack_reader as module

        reader, urlopen = self._reader(waits, [_http_error(500)])
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        with pytest.raises(SourceUnavailableError, match="500"):
            reader.get("conversations.history")
        assert waits == []

    def test_an_unreachable_host_is_an_outage_not_a_refusal(
        self, waits, monkeypatch,
    ):
        import tpllm.integrations.slack_reader as module

        reader, urlopen = self._reader(
            waits, [urllib.error.URLError("no route")],
        )
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        with pytest.raises(SourceUnavailableError, match="could not be"):
            reader.get("conversations.history")

    def test_a_non_json_body_is_reported_as_such(self, waits, monkeypatch):
        import tpllm.integrations.slack_reader as module

        reader, urlopen = self._reader(waits, [_response(b"<html>")])
        monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
        with pytest.raises(SourceUnavailableError, match="non-JSON"):
            reader.get("conversations.history")

    def test_the_policy_bounds_are_enforced_by_the_model(self):
        for bad in (
            {"max_attempts": 0}, {"max_wait_seconds": 0},
            {"default_retry_after_seconds": -1}, {"max_pages": 0},
        ):
            with pytest.raises(Exception):
                SlackRateLimitPolicy(**bad)

    def test_the_policy_is_frozen(self):
        policy = SlackRateLimitPolicy()
        with pytest.raises(Exception):
            policy.max_attempts = 99


# ── Pagination ───────────────────────────────────────────────────────


class TestPagination:
    def test_a_cursor_is_followed_to_the_end(self):
        reader = RecordedSlackReader({
            "conversations.history?channel=C0ABCDEF": {
                "ok": True, "messages": [{"ts": "1.1"}],
                "response_metadata": {"next_cursor": "page2"},
            },
            "conversations.history?channel=C0ABCDEF&cursor=page2": {
                "ok": True, "messages": [{"ts": "2.2"}],
                "response_metadata": {"next_cursor": ""},
            },
        })
        result = read_paginated(
            reader=reader, method="history", channel=fx.CHANNEL,
        )
        assert [item["ts"] for item in result.items] == ["1.1", "2.2"]
        assert result.pages_read == 2
        assert result.complete and not result.truncated

    def test_a_single_page_is_complete(self):
        reader = RecordedSlackReader({
            "conversations.history?channel=C0ABCDEF": {
                "ok": True, "messages": [{"ts": "1.1"}],
            },
        })
        result = read_paginated(
            reader=reader, method="history", channel=fx.CHANNEL,
        )
        assert result.complete and result.pages_read == 1

    def test_the_page_bound_reports_truncation_rather_than_an_ending(self):
        """A reader that returned the pages it managed and said nothing
        would turn a long channel into a short one, and every count
        derived from it would be wrong in a direction nobody could
        see."""
        reader = RecordedSlackReader({
            "conversations.history?channel=C0ABCDEF": {
                "ok": True, "messages": [{"ts": "1.1"}],
                "response_metadata": {"next_cursor": "page2"},
            },
        })
        result = read_paginated(
            reader=reader, method="history", channel=fx.CHANNEL,
            policy=SlackRateLimitPolicy(max_pages=1),
        )
        assert result.truncated is True
        assert not result.complete
        assert result.next_cursor == "page2", (
            "the unfetched cursor is carried so the read can be resumed "
            "rather than restarted or abandoned"
        )

    def test_a_cursor_of_whitespace_is_not_a_cursor(self):
        reader = RecordedSlackReader({
            "conversations.history?channel=C0ABCDEF": {
                "ok": True, "messages": [],
                "response_metadata": {"next_cursor": "   "},
            },
        })
        assert read_paginated(
            reader=reader, method="history", channel=fx.CHANNEL,
        ).complete

    def test_a_refusal_mid_pagination_is_raised_not_swallowed(self):
        reader = RecordedSlackReader({
            "conversations.history?channel=C0ABCDEF": {
                "ok": True, "messages": [{"ts": "1.1"}],
                "response_metadata": {"next_cursor": "page2"},
            },
            "conversations.history?channel=C0ABCDEF&cursor=page2": {
                "ok": False, "error": "ratelimited",
            },
        })
        with pytest.raises(SlackApiError):
            read_paginated(
                reader=reader, method="history", channel=fx.CHANNEL,
            )

    def test_the_documented_history_envelope_paginates(self):
        """Driven off the committed fixture, so the pagination contract
        is pinned to the documented shape rather than to a mock written
        to agree with the code."""
        document = fx.history_response()
        cursor = document["response_metadata"]["next_cursor"]
        reader = RecordedSlackReader({
            "conversations.history?channel=C0ABCDEF": document,
            f"conversations.history?channel=C0ABCDEF&cursor={cursor}": {
                "ok": True, "messages": [], "has_more": False,
            },
        })
        result = read_paginated(
            reader=reader, method="history", channel=fx.CHANNEL,
        )
        assert result.pages_read == 2
        assert result.complete
        assert len(result.items) == len(document["messages"])

    def test_a_read_is_never_complete_and_truncated_at_once(self):
        assert PaginatedRead(truncated=True).complete is False
        assert PaginatedRead().complete is True


# ── The recorded reader ──────────────────────────────────────────────


class TestRecordedReader:
    RESPONSES = {
        "conversations.history?channel=C0ABCDEF&limit=100": {
            "ok": True, "messages": [{"ts": "1.1"}],
        },
        "/conversations.info?channel=C0ABCDEF": {
            "ok": True, "channel": {"id": "C0ABCDEF"},
        },
    }

    def test_it_satisfies_the_protocol(self):
        assert isinstance(RecordedSlackReader({}), SlackReader)

    def test_how_a_caller_asked_is_not_part_of_the_key(self):
        """``limit`` describes how we asked, not what Slack returned. A
        recording keyed on it would miss on a harmless page-size
        change."""
        reader = RecordedSlackReader(self.RESPONSES)
        assert reader.get(
            "conversations.history?channel=C0ABCDEF&limit=5"
        )["ok"] is True

    def test_what_was_returned_is_part_of_the_key(self):
        """The opposite failure: a reader that ignored ``channel`` would
        serve one channel's messages for another's."""
        reader = RecordedSlackReader(self.RESPONSES)
        with pytest.raises(SourceUnavailableError):
            reader.get("conversations.history?channel=C0OTHER1&limit=100")

    def test_a_leading_slash_is_irrelevant(self):
        reader = RecordedSlackReader(self.RESPONSES)
        assert reader.get("conversations.info?channel=C0ABCDEF")["ok"]

    def test_parameter_order_is_irrelevant(self):
        reader = RecordedSlackReader(self.RESPONSES)
        assert reader.get(
            "conversations.history?limit=100&channel=C0ABCDEF"
        )["ok"]

    def test_a_missing_recording_is_a_missing_fixture_not_an_outage(self):
        reader = RecordedSlackReader({})
        with pytest.raises(SourceUnavailableError, match="missing fixture"):
            reader.get("conversations.history?channel=C0ABCDEF")

    def test_it_records_what_was_asked_for(self):
        reader = RecordedSlackReader(self.RESPONSES)
        reader.get("conversations.history?channel=C0ABCDEF&limit=7")
        assert reader.requested == [
            "conversations.history?channel=C0ABCDEF"
        ]

    def test_a_recorded_refusal_still_refuses(self):
        """So the offline suite can exercise the error paths through the
        same code the live reader uses."""
        reader = RecordedSlackReader({
            "conversations.history?channel=C0ABCDEF": {
                "ok": False, "error": "not_in_channel",
            },
        })
        with pytest.raises(SlackApiError, match="not_in_channel"):
            reader.get("conversations.history?channel=C0ABCDEF")

    @pytest.mark.parametrize("name", IDENTIFYING_QUERY_PARAMETERS)
    def test_each_identifying_parameter_changes_the_key(self, name):
        """Every one of them describes what Slack returned, so adding
        one must give a different recording."""
        assert recording_key(f"conversations.history?{name}=v") != (
            recording_key("conversations.history")
        )

    @pytest.mark.parametrize("name", ["limit", "inclusive", "pretty"])
    def test_a_how_we_asked_parameter_does_not_change_the_key(self, name):
        assert recording_key(f"conversations.history?{name}=v") == (
            recording_key("conversations.history")
        )


class TestMethodDeclaration:
    def test_the_methods_are_declared_with_their_queries(self):
        path = method_path("history", channel=fx.CHANNEL, limit=5)
        assert path == f"conversations.history?channel={fx.CHANNEL}&limit=5"

    def test_a_thread_read_names_its_root(self):
        path = method_path(
            "replies", channel=fx.CHANNEL, ts="1772010000.000100",
        )
        assert "ts=1772010000.000100" in path

    def test_a_cursor_is_url_encoded(self):
        """Slack's cursors are base64 and routinely contain ``=``, which
        would otherwise terminate the parameter."""
        path = method_path(
            "history", channel=fx.CHANNEL, cursor="bWVzc2FnZToxMg==",
        )
        assert "cursor=bWVzc2FnZToxMg%3D%3D" in path
        assert recording_key(path).endswith("cursor=bWVzc2FnZToxMg==")

    def test_the_default_page_limit_is_declared(self):
        assert DEFAULT_PAGE_LIMIT == 100
        assert f"limit={DEFAULT_PAGE_LIMIT}" in method_path(
            "history", channel=fx.CHANNEL,
        )


# ── Webhook signature verification ──────────────────────────────────


class TestSignatureVerification:
    """Slack's documented ``v0`` scheme. Built before anything hosts
    webhooks, because the first thing to accept a delivery will
    otherwise either invent its own check or skip one."""

    BODY = b'{"type":"event_callback","event_id":"Ev0ABCDEF1"}'
    TIMESTAMP = "1772010000"
    NOW = 1772010010.0

    def _signature(self, *, secret=SIGNING_SECRET, timestamp=None, body=None):
        return compute_signature(
            signing_secret=secret,
            timestamp=timestamp or self.TIMESTAMP,
            raw_body=body if body is not None else self.BODY,
        )

    def test_the_base_string_is_the_documented_one(self):
        assert signature_base_string(
            timestamp=self.TIMESTAMP, raw_body=self.BODY,
        ) == b"v0:" + self.TIMESTAMP.encode() + b":" + self.BODY

    def test_the_signature_is_the_documented_hmac(self):
        """Computed independently here, so the test fails if the scheme
        drifts rather than agreeing with whatever the module does."""
        expected = hmac.new(
            SIGNING_SECRET.encode(),
            b"v0:" + self.TIMESTAMP.encode() + b":" + self.BODY,
            hashlib.sha256,
        ).hexdigest()
        assert self._signature() == f"{SIGNATURE_VERSION}={expected}"

    def test_a_valid_signature_passes(self):
        verify_signature(
            signing_secret=SIGNING_SECRET, timestamp=self.TIMESTAMP,
            raw_body=self.BODY, provided_signature=self._signature(),
            now=self.NOW,
        )

    def test_a_tampered_body_fails(self):
        tampered = self.BODY.replace(b"Ev0ABCDEF1", b"Ev0ABCDEF2")
        with pytest.raises(SignatureInvalidError, match="does not match"):
            verify_signature(
                signing_secret=SIGNING_SECRET, timestamp=self.TIMESTAMP,
                raw_body=tampered, provided_signature=self._signature(),
                now=self.NOW,
            )

    def test_a_single_byte_change_fails(self):
        with pytest.raises(SignatureInvalidError):
            verify_signature(
                signing_secret=SIGNING_SECRET, timestamp=self.TIMESTAMP,
                raw_body=self.BODY + b" ",
                provided_signature=self._signature(), now=self.NOW,
            )

    def test_the_wrong_secret_fails(self):
        with pytest.raises(SignatureInvalidError, match="does not match"):
            verify_signature(
                signing_secret="a-different-secret-0123456789abcdef",
                timestamp=self.TIMESTAMP, raw_body=self.BODY,
                provided_signature=self._signature(), now=self.NOW,
            )

    def test_a_stale_timestamp_fails_even_with_a_valid_signature(self):
        """A signature never expires, so without the window a delivery
        captured once could be replayed forever."""
        stale = str(int(self.TIMESTAMP) - 3600)
        with pytest.raises(SignatureInvalidError, match="replay window"):
            verify_signature(
                signing_secret=SIGNING_SECRET, timestamp=stale,
                raw_body=self.BODY,
                provided_signature=self._signature(timestamp=stale),
                now=self.NOW,
            )

    def test_a_timestamp_from_the_future_fails_too(self):
        """Symmetric on purpose: a delivery stamped an hour ahead is
        either a forgery or a clock problem, and accepting it would
        widen the replay window to two hours."""
        ahead = str(int(self.TIMESTAMP) + 3600)
        with pytest.raises(SignatureInvalidError, match="replay window"):
            verify_signature(
                signing_secret=SIGNING_SECRET, timestamp=ahead,
                raw_body=self.BODY,
                provided_signature=self._signature(timestamp=ahead),
                now=self.NOW,
            )

    def test_the_window_edge_is_inclusive(self):
        edge = str(int(self.NOW) - DEFAULT_REPLAY_WINDOW_SECONDS)
        verify_signature(
            signing_secret=SIGNING_SECRET, timestamp=edge,
            raw_body=self.BODY,
            provided_signature=self._signature(timestamp=edge),
            now=self.NOW,
        )

    def test_the_window_is_configurable(self):
        edge = str(int(self.NOW) - 60)
        with pytest.raises(SignatureInvalidError, match="30s replay"):
            verify_signature(
                signing_secret=SIGNING_SECRET, timestamp=edge,
                raw_body=self.BODY,
                provided_signature=self._signature(timestamp=edge),
                now=self.NOW, replay_window_seconds=30,
            )

    def test_the_default_window_is_the_documented_five_minutes(self):
        assert DEFAULT_REPLAY_WINDOW_SECONDS == 300

    @pytest.mark.parametrize("timestamp", ["", "   ", None, "not-a-number"])
    def test_a_missing_timestamp_fails_closed(self, timestamp):
        with pytest.raises(SignatureInvalidError, match="age cannot be"):
            verify_signature(
                signing_secret=SIGNING_SECRET, timestamp=timestamp,
                raw_body=self.BODY, provided_signature=self._signature(),
                now=self.NOW,
            )

    def test_no_configured_secret_means_no_delivery_is_accepted(self):
        """Fails closed. Accepting an unverified delivery because
        nothing was configured would accept a forgery."""
        with pytest.raises(SignatureInvalidError, match="no delivery can"):
            verify_signature(
                signing_secret="", timestamp=self.TIMESTAMP,
                raw_body=self.BODY, provided_signature=self._signature(),
                now=self.NOW,
            )

    @pytest.mark.parametrize("provided", ["", None, "v0=", "garbage"])
    def test_a_malformed_signature_fails(self, provided):
        with pytest.raises(SignatureInvalidError):
            verify_signature(
                signing_secret=SIGNING_SECRET, timestamp=self.TIMESTAMP,
                raw_body=self.BODY, provided_signature=provided,
                now=self.NOW,
            )

    @pytest.mark.parametrize(
        "case",
        ["stale", "tampered", "wrong_secret", "no_secret", "bad_timestamp"],
    )
    def test_the_secret_never_appears_in_an_exception(self, case):
        """An exception message reaches logs, tracebacks and bug
        reports. A comparison printed alongside its inputs is an
        oracle."""
        arguments = {
            "signing_secret": SIGNING_SECRET,
            "timestamp": self.TIMESTAMP,
            "raw_body": self.BODY,
            "provided_signature": self._signature(),
            "now": self.NOW,
        }
        if case == "stale":
            arguments["now"] = self.NOW + 10_000
        elif case == "tampered":
            arguments["raw_body"] = b"{}"
        elif case == "wrong_secret":
            arguments["signing_secret"] = SIGNING_SECRET + "x"
        elif case == "no_secret":
            arguments["signing_secret"] = ""
        else:
            arguments["timestamp"] = "nope"

        with pytest.raises(SignatureInvalidError) as caught:
            verify_signature(**arguments)
        rendered = f"{caught.value}{caught.value.args}"
        assert SIGNING_SECRET not in rendered
        assert "slack-signing-secret" not in rendered
        # Nor the digests, which together with the body would let an
        # attacker confirm a guess at the secret.
        assert self._signature().split("=")[1] not in rendered

    def test_the_comparison_is_constant_time(self):
        """Checked against the source: ``==`` on a digest leaks how much
        of it matched, which is enough to forge one byte at a time."""
        tree = ast.parse(_SOURCE.read_text())
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "verify_signature"
        )
        compared = {
            node.func.attr for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        assert "compare_digest" in compared
        assert not any(
            isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
            and "signature" in ast.dump(node).lower()
            for node in ast.walk(function)
        )

    def test_the_timestamp_is_checked_before_the_hmac(self):
        """The documented order, and the cheap one: a request already
        known to be unacceptable should not cost an HMAC."""
        stale = str(int(self.TIMESTAMP) - 10_000)
        with pytest.raises(SignatureInvalidError, match="replay window"):
            verify_signature(
                signing_secret=SIGNING_SECRET, timestamp=stale,
                raw_body=self.BODY, provided_signature="not-even-a-signature",
                now=self.NOW,
            )

    def test_the_secret_is_never_logged(self):
        """Nothing in the module logs during verification at all, which
        is the simplest way to guarantee it."""
        tree = ast.parse(_SOURCE.read_text())
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "verify_signature"
        )
        assert not [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"debug", "info", "warning", "error"}
        ]
