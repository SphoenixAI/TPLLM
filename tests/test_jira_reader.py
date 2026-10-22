"""Tests for the Jira reader and the webhook seam.

As with the GitHub reader, most of what is pinned here is what the
reader is not allowed to do: write to the provider, leak a credential,
call a single page the whole backlog, or let a body reach a parser
before its signature has been checked.

The webhook half is tested more heavily than its current use justifies,
because nothing hosts a receiver yet and the seam is therefore the only
thing standing between a future receiver and the obvious mistake. The
obvious mistake is not forgetting to verify — it is verifying *after*
parsing, which looks identical in a code review and means every
unauthenticated request has already been through a parser.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from tpllm.integrations.jira_reader import (
    BASIC_EMAIL_ENVIRONMENT_VARIABLE,
    BASIC_TOKEN_ENVIRONMENT_VARIABLE,
    BEARER_TOKEN_ENVIRONMENT_VARIABLES,
    DEFAULT_ISSUE_FIELDS,
    DEFAULT_MAX_PAGES,
    JIRA_ENDPOINTS,
    JIRA_READER_CAPABILITIES,
    JIRA_WEBHOOK_LIFECYCLE,
    MAX_BACKOFF_SECONDS,
    WEBHOOK_SECRET_ENVIRONMENT_VARIABLE,
    WEBHOOK_SIGNATURE_HEADER,
    CredentialMode,
    JiraReader,
    RecordedJiraReader,
    SourceUnavailableError,
    UrllibJiraReader,
    WebhookVerificationError,
    compute_webhook_signature,
    parse_verified_webhook,
    search_issues,
    search_path,
    verify_webhook_signature,
    webhook_secret_from_environment,
)
from tests import _jira_fixtures as fx

_SOURCE = pathlib.Path("src/tpllm/integrations/jira_reader.py")

ALL_CREDENTIAL_VARIABLES = (
    *BEARER_TOKEN_ENVIRONMENT_VARIABLES,
    BASIC_EMAIL_ENVIRONMENT_VARIABLE,
    BASIC_TOKEN_ENVIRONMENT_VARIABLE,
)


@pytest.fixture
def no_credentials(monkeypatch):
    for name in ALL_CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)


# ── Read-only by construction ────────────────────────────────────────


class TestTheReaderIsReadOnly:
    def test_only_get_is_ever_issued(self):
        """Enforced against the source, not promised in a docstring. In
        Jira a stray write is somebody's reopened ticket or deleted
        comment, in a system where our only job is to read."""
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

    def test_no_write_verb_appears_anywhere_in_the_module(self):
        text = _SOURCE.read_text()
        for verb in ("POST", "PATCH", "PUT", "DELETE"):
            assert verb not in text, (
                f"'{verb}' appears in the reader; a read-only port should "
                f"not be able to name a write"
            )

    def test_the_lifecycle_note_states_the_refresh_is_a_write(self):
        """Which is why it cannot live here. The note names the
        obligation and the component that has to carry it, rather than
        leaving a hosted receiver to discover it when deliveries stop."""
        assert JIRA_WEBHOOK_LIFECYCLE.refresh_is_a_write is True
        assert JIRA_READER_CAPABILITIES["webhook_refresh"] is False


# ── Credentials ──────────────────────────────────────────────────────


class TestCredentialsStayPrivate:
    def test_a_bearer_token_is_read_from_the_environment(
        self, monkeypatch, no_credentials,
    ):
        monkeypatch.setenv(BEARER_TOKEN_ENVIRONMENT_VARIABLES[0], "abc")
        reader = UrllibJiraReader(site=fx.SITE)
        assert reader.authenticated
        assert reader.credential_mode == CredentialMode.BEARER.value

    def test_basic_auth_is_supported_because_jira_cloud_needs_it(
        self, monkeypatch, no_credentials,
    ):
        """Atlassian does not issue personal bearer tokens: a person
        authenticates with their email and an API token. Supporting only
        OAuth would push a single-user deployment through an app
        registration it has no reason to make."""
        monkeypatch.setenv(BASIC_EMAIL_ENVIRONMENT_VARIABLE, "ops@example.com")
        monkeypatch.setenv(BASIC_TOKEN_ENVIRONMENT_VARIABLE, "api-token")
        reader = UrllibJiraReader(site=fx.SITE)
        assert reader.authenticated
        assert reader.credential_mode == CredentialMode.BASIC.value

    def test_half_of_basic_auth_is_not_a_credential(
        self, monkeypatch, no_credentials,
    ):
        """An email with no token would send unauthenticated requests
        that fail as 401s and look like a permissions problem."""
        monkeypatch.setenv(BASIC_EMAIL_ENVIRONMENT_VARIABLE, "ops@example.com")
        assert not UrllibJiraReader(site=fx.SITE).authenticated

    def test_a_bearer_token_wins_when_both_are_configured(
        self, monkeypatch, no_credentials,
    ):
        """It is the app-level credential; silently preferring the
        personal one would make a deployment act as an individual."""
        monkeypatch.setenv(BEARER_TOKEN_ENVIRONMENT_VARIABLES[0], "abc")
        monkeypatch.setenv(BASIC_EMAIL_ENVIRONMENT_VARIABLE, "ops@example.com")
        monkeypatch.setenv(BASIC_TOKEN_ENVIRONMENT_VARIABLE, "api-token")
        assert UrllibJiraReader(site=fx.SITE).credential_mode == (
            CredentialMode.BEARER.value
        )

    def test_no_credentials_is_a_stated_mode_not_a_crash(
        self, no_credentials,
    ):
        reader = UrllibJiraReader(site=fx.SITE)
        assert reader.authenticated is False
        assert reader.credential_mode == "unauthenticated"

    def test_a_blank_environment_value_is_not_a_credential(
        self, monkeypatch, no_credentials,
    ):
        monkeypatch.setenv(BEARER_TOKEN_ENVIRONMENT_VARIABLES[0], "   ")
        assert not UrllibJiraReader(site=fx.SITE).authenticated

    def test_there_is_no_accessor_for_the_credential(self, no_credentials):
        reader = UrllibJiraReader(
            site=fx.SITE, bearer_token="s3cret-token-value-1234567890",
        )
        assert reader.authenticated
        assert not [
            name for name in dir(reader)
            if name in {"token", "get_token", "credential", "authorization"}
        ]

    def test_the_credential_is_not_in_the_representation(
        self, no_credentials,
    ):
        """``repr`` ends up in tracebacks and debugger output, which is
        the least controlled place a credential can surface."""
        reader = UrllibJiraReader(
            site=fx.SITE, bearer_token="s3cret-token-value-1234567890",
        )
        assert "s3cret" not in repr(reader)
        assert "oauth_bearer" in repr(reader)

    def test_basic_auth_is_not_in_the_representation(self, no_credentials):
        reader = UrllibJiraReader(
            site=fx.SITE, email="ops@example.com", api_token="s3cret-token",
        )
        assert "s3cret" not in repr(reader)
        assert "ops@example.com" not in repr(reader)

    def test_no_log_call_in_the_reader_can_reach_the_headers(self):
        """The Authorization header is in scope at the log site, so the
        line logs the path and the status and nothing else."""
        tree = ast.parse(_SOURCE.read_text())
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"debug", "info", "warning", "error"}
            ):
                continue
            rendered = ast.dump(node)
            for forbidden in (
                "headers", "_authorization", "Authorization", "_token",
                "api_token", "secret",
            ):
                assert forbidden not in rendered, (
                    f"a log call in the reader names '{forbidden}'"
                )

    def test_the_webhook_secret_has_its_own_variable(self, monkeypatch):
        monkeypatch.delenv(WEBHOOK_SECRET_ENVIRONMENT_VARIABLE, raising=False)
        assert webhook_secret_from_environment() is None
        monkeypatch.setenv(WEBHOOK_SECRET_ENVIRONMENT_VARIABLE, "s")
        assert webhook_secret_from_environment() == "s"


# ── The recorded reader ──────────────────────────────────────────────


class TestRecordedReader:
    RESPONSES = {
        "search/jql?jql=project%3DABC": {"issues": [], "isLast": True},
        "/issue/10005": {"id": "10005"},
    }

    def test_it_satisfies_the_protocol(self):
        assert isinstance(RecordedJiraReader({}), JiraReader)

    def test_the_query_string_is_not_part_of_the_key(self):
        reader = RecordedJiraReader(self.RESPONSES)
        assert reader.get("search/jql?jql=anything&maxResults=5") == {
            "issues": [], "isLast": True,
        }

    def test_a_leading_slash_is_irrelevant(self):
        assert RecordedJiraReader(self.RESPONSES).get("issue/10005") == {
            "id": "10005"
        }

    def test_the_page_token_is_part_of_the_key(self):
        """Page two is a different response at the same path. A reader
        that dropped the token would replay page one forever, and a
        pagination test against it would pass on a single page."""
        reader = RecordedJiraReader({
            "search/jql": {"issues": [{"id": "1"}], "nextPageToken": "t2"},
            "search/jql@t2": {"issues": [{"id": "2"}], "isLast": True},
        })
        assert reader.get("search/jql?jql=x")["issues"] == [{"id": "1"}]
        assert reader.get("search/jql?jql=x&nextPageToken=t2")["issues"] == [
            {"id": "2"}
        ]

    def test_a_missing_recording_is_a_missing_fixture_not_an_outage(self):
        with pytest.raises(SourceUnavailableError, match="missing fixture"):
            RecordedJiraReader({}).get("issue/10005")

    def test_it_records_what_was_asked_for(self):
        reader = RecordedJiraReader(self.RESPONSES)
        reader.get("issue/10005?expand=changelog")
        assert reader.requested == ["issue/10005"]


# ── Pagination ───────────────────────────────────────────────────────


class TestSearchPagination:
    @pytest.fixture
    def pages(self):
        first = fx.search_page(first=True)
        second = fx.search_page(first=False)
        return {
            "search/jql": first,
            f"search/jql@{first['nextPageToken']}": second,
        }

    def test_the_declared_endpoint_asks_for_the_changelog(self):
        """Without ``expand=changelog`` the response is a snapshot with
        no history, and the changelog is where every real transition
        lives."""
        assert "expand=changelog" in JIRA_ENDPOINTS["search"]

    def test_the_jql_is_encoded_for_the_caller(self):
        """A query with a space or a quote in it is the normal case, and
        an unencoded one fails as a 400 that looks like a permissions
        problem."""
        path = search_path(jql='project = ABC AND status != "Done"')
        assert " " not in path
        assert "%22" in path

    def test_only_the_fields_the_normalizer_reads_are_requested(self):
        path = search_path(jql="project = ABC")
        for field in ("summary", "status", "issuelinks", "updated"):
            assert field in path
        assert "worklog" not in path, (
            "asking for a field the system refuses to interpret would "
            "pull an individual's logged hours across the wire for no "
            "reason"
        )
        assert "worklog" not in DEFAULT_ISSUE_FIELDS

    def test_both_pages_are_walked(self, pages):
        outcome = search_issues(
            reader=RecordedJiraReader(pages), jql="project = ABC",
        )
        assert outcome.pages_read == 2
        assert len(outcome.issues) == 3
        assert outcome.exhausted and not outcome.truncated

    def test_is_last_ends_the_walk(self, pages):
        outcome = search_issues(
            reader=RecordedJiraReader(pages), jql="project = ABC",
        )
        assert outcome.next_page_token == ""

    def test_a_missing_token_ends_the_walk_too(self):
        """Either signal is enough: ``isLast`` is authoritative when
        present, and no token is the end whether or not it is."""
        outcome = search_issues(
            reader=RecordedJiraReader({"search/jql": {"issues": []}}),
            jql="project = ABC",
        )
        assert outcome.exhausted and outcome.pages_read == 1

    def test_the_page_bound_is_a_stated_outcome(self, pages):
        """A caller that cannot tell 'this is everything' from 'this is
        the first page' will report a quiet project when it actually
        stopped reading."""
        outcome = search_issues(
            reader=RecordedJiraReader(pages), jql="project = ABC",
            max_pages=1,
        )
        assert outcome.pages_read == 1
        assert not outcome.exhausted
        assert outcome.truncated
        assert outcome.next_page_token

    def test_there_is_a_default_bound(self):
        """A JQL query matching a whole backlog would otherwise read a
        site's entire history in one pass."""
        assert DEFAULT_MAX_PAGES >= 1

    def test_a_partial_read_keeps_what_answered(self, pages):
        """An outage on page two should not discard page one."""
        token = fx.search_page(first=True)["nextPageToken"]
        del pages[f"search/jql@{token}"]
        outcome = search_issues(
            reader=RecordedJiraReader(pages), jql="project = ABC",
        )
        assert len(outcome.issues) == 2
        assert outcome.failure
        assert not outcome.exhausted
        assert not outcome.truncated, (
            "a failure is not a truncation; conflating them would report "
            "an outage as 'there is more to read'"
        )

    def test_the_deprecated_offset_shape_is_refused(self):
        """The old ``/search`` endpoint returns a different document, and
        a bare list is the shape of the one thing this reader must not
        quietly start accepting."""
        outcome = search_issues(
            reader=RecordedJiraReader({"search/jql": [{"id": "1"}]}),
            jql="project = ABC",
        )
        assert outcome.issues == []
        assert "deprecated offset endpoint" in outcome.failure

    def test_token_pagination_is_what_is_declared(self):
        assert JIRA_READER_CAPABILITIES["token_pagination"] is True
        assert JIRA_READER_CAPABILITIES["offset_pagination"] is False


# ── Rate limiting ────────────────────────────────────────────────────


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()
        self.status = 200

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class TestRateLimiting:
    def _reader(self, monkeypatch, *, behaviour, slept, **kwargs):
        import urllib.request

        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            return behaviour(calls["n"])

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        reader = UrllibJiraReader(
            site=fx.SITE, bearer_token="t", sleep=slept.append, **kwargs,
        )
        return reader, calls

    def test_a_rate_limit_is_retried_after_the_requested_delay(
        self, monkeypatch,
    ):
        """Atlassian's documented guidance is to obey ``Retry-After``
        rather than inventing a schedule."""
        import urllib.error

        slept: list[float] = []

        def behaviour(attempt):
            if attempt == 1:
                raise urllib.error.HTTPError(
                    "u", 429, "Too Many Requests",
                    {"Retry-After": "7"}, None,
                )
            return _Response({"issues": []})

        reader, calls = self._reader(
            monkeypatch, behaviour=behaviour, slept=slept,
        )
        assert reader.get("search/jql") == {"issues": []}
        assert slept == [7.0]
        assert calls["n"] == 2

    def test_the_backoff_is_bounded(self, monkeypatch):
        """A reader that slept for the four minutes Jira sometimes asks
        for would look hung, and an ingest pass holding the journal open
        behind it is worse than a read that reports itself unavailable."""
        import urllib.error

        slept: list[float] = []

        def behaviour(attempt):
            raise urllib.error.HTTPError(
                "u", 429, "Too Many Requests", {"Retry-After": "600"}, None,
            )

        reader, _ = self._reader(
            monkeypatch, behaviour=behaviour, slept=slept,
        )
        with pytest.raises(SourceUnavailableError):
            reader.get("search/jql")
        assert slept and max(slept) <= MAX_BACKOFF_SECONDS

    def test_the_attempts_are_bounded(self, monkeypatch):
        """Retrying until it works turns a rate limit into an outage of
        our own making."""
        import urllib.error

        slept: list[float] = []

        def behaviour(attempt):
            raise urllib.error.HTTPError(
                "u", 429, "Too Many Requests", {}, None,
            )

        reader, calls = self._reader(
            monkeypatch, behaviour=behaviour, slept=slept, max_attempts=3,
        )
        with pytest.raises(SourceUnavailableError) as raised:
            reader.get("search/jql")
        assert calls["n"] == 3
        assert raised.value.status == 429

    def test_a_date_formatted_retry_after_falls_back_to_the_schedule(
        self, monkeypatch,
    ):
        """A date is a legal ``Retry-After`` and is not worth parsing
        here; falling back beats crashing in the retry path."""
        import urllib.error

        slept: list[float] = []

        def behaviour(attempt):
            if attempt == 1:
                raise urllib.error.HTTPError(
                    "u", 503, "Unavailable",
                    {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, None,
                )
            return _Response({"ok": True})

        reader, _ = self._reader(
            monkeypatch, behaviour=behaviour, slept=slept,
        )
        assert reader.get("search/jql") == {"ok": True}
        assert slept == [1.0]

    def test_an_ordinary_client_error_is_not_retried(self, monkeypatch):
        """A 404 is our request being wrong. Repeating it would only be
        rude."""
        import urllib.error

        slept: list[float] = []

        def behaviour(attempt):
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

        reader, calls = self._reader(
            monkeypatch, behaviour=behaviour, slept=slept,
        )
        with pytest.raises(SourceUnavailableError):
            reader.get("issue/10005")
        assert calls["n"] == 1
        assert slept == []

    def test_an_unreachable_site_is_an_outage_not_a_breach(
        self, monkeypatch,
    ):
        """'We could not look' and 'what we saw was wrong' must stay
        distinguishable — which is this environment's situation exactly:
        atlassian.net is unreachable here, and that is not Atlassian
        having broken its API."""
        import urllib.error

        slept: list[float] = []

        def behaviour(attempt):
            raise urllib.error.URLError("no route to host")

        reader, _ = self._reader(
            monkeypatch, behaviour=behaviour, slept=slept,
        )
        with pytest.raises(SourceUnavailableError, match="could not be"):
            reader.get("issue/10005")

    def test_a_non_json_body_is_reported_as_unavailable(self, monkeypatch):
        import urllib.request

        class _Html:
            status = 200

            def read(self):
                return b"<html>sign in</html>"

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _Html(),
        )
        reader = UrllibJiraReader(site=fx.SITE, bearer_token="t")
        with pytest.raises(SourceUnavailableError, match="non-JSON"):
            reader.get("issue/10005")

    def test_the_error_never_quotes_the_query_string(self, monkeypatch):
        """A JQL query is not a secret, but a path with a token appended
        by a future caller would be, so the scope is always truncated at
        the '?'."""
        import urllib.error

        slept: list[float] = []

        def behaviour(attempt):
            raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

        reader, _ = self._reader(
            monkeypatch, behaviour=behaviour, slept=slept,
        )
        with pytest.raises(SourceUnavailableError) as raised:
            reader.get("search/jql?jql=secretish&nextPageToken=abc")
        assert "nextPageToken" not in str(raised.value)
        assert "search/jql" in str(raised.value)


# ── Webhook authenticity ─────────────────────────────────────────────


class TestWebhookVerification:
    BODY = json.dumps({"webhookEvent": "jira:issue_updated"}).encode()
    SECRET = "a-shared-secret-of-at-least-32-bytes-length"

    def _signature(self, body=None, secret=None):
        return compute_webhook_signature(
            raw_body=body if body is not None else self.BODY,
            secret=secret or self.SECRET,
        )

    def test_a_valid_signature_passes(self):
        assert verify_webhook_signature(
            raw_body=self.BODY, signature_header=self._signature(),
            secret=self.SECRET,
        )

    def test_a_tampered_body_fails(self):
        """The attack this exists to stop: a body someone else wrote,
        which would otherwise become journalled organizational fact."""
        tampered = json.dumps(
            {"webhookEvent": "jira:issue_updated", "injected": True}
        ).encode()
        assert not verify_webhook_signature(
            raw_body=tampered, signature_header=self._signature(),
            secret=self.SECRET,
        )

    def test_one_flipped_byte_fails(self):
        assert not verify_webhook_signature(
            raw_body=self.BODY + b" ",
            signature_header=self._signature(), secret=self.SECRET,
        )

    def test_the_wrong_secret_fails(self):
        assert not verify_webhook_signature(
            raw_body=self.BODY,
            signature_header=self._signature(secret="another-secret"),
            secret=self.SECRET,
        )

    def test_a_missing_secret_rejects_rather_than_passes(self):
        """A receiver that accepted unsigned deliveries because nobody
        set the variable would be the whole vulnerability."""
        assert not verify_webhook_signature(
            raw_body=self.BODY, signature_header=self._signature(),
            secret="",
        )

    def test_a_missing_signature_header_fails(self):
        assert not verify_webhook_signature(
            raw_body=self.BODY, signature_header="", secret=self.SECRET,
        )

    def test_an_empty_body_fails(self):
        assert not verify_webhook_signature(
            raw_body=b"", signature_header=self._signature(),
            secret=self.SECRET,
        )

    def test_the_prefix_is_optional_and_the_case_is_not_significant(self):
        digest = self._signature().removeprefix("sha256=")
        assert verify_webhook_signature(
            raw_body=self.BODY, signature_header=digest,
            secret=self.SECRET,
        )
        assert verify_webhook_signature(
            raw_body=self.BODY, signature_header=f"SHA256={digest.upper()}",
            secret=self.SECRET,
        )

    def test_the_comparison_is_timing_safe(self):
        """Asserted against the source: ``==`` on a digest returns early
        at the first differing byte, and that timing is enough to
        recover a valid signature one byte at a time."""
        tree = ast.parse(_SOURCE.read_text())
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "verify_webhook_signature"
        )
        calls = {
            ast.unparse(node.func) for node in ast.walk(function)
            if isinstance(node, ast.Call)
        }
        assert "hmac.compare_digest" in calls
        assert not any(
            isinstance(node, ast.Compare)
            and any(isinstance(op, ast.Eq) for op in node.ops)
            for node in ast.walk(function)
        ), "an equality comparison in the verifier is a timing oracle"

    def test_the_signature_is_over_the_raw_bytes(self):
        """Not over a re-serialized object: JSON round-tripping changes
        key order and whitespace, so a verifier checking a normalized
        form would fail honest deliveries and accept a form an attacker
        can also produce."""
        reordered = b'{"b": 2, "a": 1}'
        original = b'{"a": 1, "b": 2}'
        assert compute_webhook_signature(
            raw_body=original, secret=self.SECRET,
        ) != compute_webhook_signature(
            raw_body=reordered, secret=self.SECRET,
        )

    def test_verification_happens_before_parsing(self):
        """The order is the point. A body that reaches a parser before
        its signature is checked has already been processed by
        attacker-controlled input."""
        assert not verify_webhook_signature(
            raw_body=b"{{{ not json at all", signature_header="sha256=x",
            secret=self.SECRET,
        )
        with pytest.raises(WebhookVerificationError, match="not parsed"):
            parse_verified_webhook(
                raw_body=b"{{{ not json at all",
                signature_header="sha256=x", secret=self.SECRET,
            )

    def test_the_verifier_is_the_first_statement_of_the_parser(self):
        """Checked structurally, because a later refactor could move a
        ``json.loads`` above the guard and every behavioural test here
        would still pass."""
        tree = ast.parse(_SOURCE.read_text())
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "parse_verified_webhook"
        )
        body = [
            statement for statement in function.body
            if not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
            )
        ]
        first = ast.unparse(body[0])
        assert "verify_webhook_signature" in first
        assert "json.loads" not in first

    def test_an_authentic_delivery_parses(self):
        payload = parse_verified_webhook(
            raw_body=self.BODY, signature_header=self._signature(),
            secret=self.SECRET,
        )
        assert payload["webhookEvent"] == "jira:issue_updated"

    def test_an_authentic_but_malformed_body_is_distinguishable(self):
        body = b"not json"
        with pytest.raises(WebhookVerificationError, match="not JSON"):
            parse_verified_webhook(
                raw_body=body,
                signature_header=self._signature(body=body),
                secret=self.SECRET,
            )

    def test_a_json_array_is_not_a_delivery(self):
        body = b"[1, 2, 3]"
        with pytest.raises(WebhookVerificationError, match="JSON object"):
            parse_verified_webhook(
                raw_body=body,
                signature_header=self._signature(body=body),
                secret=self.SECRET,
            )

    def test_no_failure_message_leaks_the_secret_or_the_digest(self):
        """An exception message reaches logs, tickets and screenshots, and
        a comparison oracle is worth more to an attacker than the failure
        itself."""
        with pytest.raises(WebhookVerificationError) as raised:
            parse_verified_webhook(
                raw_body=b"{}", signature_header="sha256=deadbeef",
                secret=self.SECRET,
            )
        message = str(raised.value)
        assert self.SECRET not in message
        assert "deadbeef" not in message
        expected = self._signature(body=b"{}").removeprefix("sha256=")
        assert expected not in message

    def test_the_real_fixture_body_verifies_end_to_end(self):
        """A delivery shaped like the documented one, signed and checked
        the way a receiver would. The fixtures carry no signature: a
        committed fixture must not carry a secret, so the signature is
        computed here."""
        body = json.dumps(
            fx.webhook("jira:issue_updated"), sort_keys=True,
        ).encode()
        payload = parse_verified_webhook(
            raw_body=body,
            signature_header=compute_webhook_signature(
                raw_body=body, secret=self.SECRET,
            ),
            secret=self.SECRET,
        )
        assert payload["issue"]["key"] == fx.DONE_ISSUE

    def test_the_header_name_is_declared(self):
        assert WEBHOOK_SIGNATURE_HEADER == "X-Hub-Signature"


# ── The lifecycle note ───────────────────────────────────────────────


class TestTheWebhookLifecycleIsWrittenDown:
    """The failure mode is silent. A dynamic webhook that expires stops
    delivering with no error anywhere, and TPLLM would then report a
    quiet organization instead of a broken integration — the worst class
    of bug this project can ship."""

    def test_the_expiry_is_stated(self):
        assert JIRA_WEBHOOK_LIFECYCLE.expires_after_days == 30
        assert JIRA_WEBHOOK_LIFECYCLE.requires_periodic_refresh is True

    def test_the_refresh_margin_is_inside_the_window(self):
        """A refresh job that only runs on day 29 has one chance, and an
        outage that day costs the registration."""
        assert 0 < JIRA_WEBHOOK_LIFECYCLE.refresh_before_days < (
            JIRA_WEBHOOK_LIFECYCLE.expires_after_days
        )

    def test_the_batch_limit_is_stated(self):
        assert JIRA_WEBHOOK_LIFECYCLE.maximum_ids_per_refresh == 100

    def test_the_obligation_names_who_owns_it(self):
        obligation = JIRA_WEBHOOK_LIFECYCLE.obligation
        assert "hosted receiver" in obligation
        assert "reconcil" in obligation, (
            "a missed delivery is never redelivered once the "
            "failed-deliveries window has passed, so the note has to say "
            "that a gap is closed by a JQL search rather than by waiting"
        )

    def test_the_note_is_immutable(self):
        with pytest.raises(Exception):
            JIRA_WEBHOOK_LIFECYCLE.expires_after_days = 365

    def test_the_capabilities_do_not_overclaim(self):
        """No recording was taken from a live site, and the declaration
        says so rather than letting a reader assume otherwise."""
        assert JIRA_READER_CAPABILITIES["live_recording_available"] is False
        assert JIRA_READER_CAPABILITIES["read_only"] is True
        assert JIRA_READER_CAPABILITIES[
            "webhook_signature_verification"
        ] is True
