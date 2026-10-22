"""Reading GitHub behind a port.

The reader exists so that the offline suite and the live check drive the
same code. What is pinned here is therefore mostly about what the reader
is *not* allowed to do: write to the provider, leak a token, or let one
failed endpoint discard the ones that answered.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from lltpm.integrations.github_reader import (
    DEFAULT_PAGE_LIMIT,
    GITHUB_ENDPOINTS,
    TOKEN_ENVIRONMENT_VARIABLES,
    GitHubReader,
    RecordedGitHubReader,
    SourceUnavailableError,
    UrllibGitHubReader,
    endpoint_paths,
    fetch_github_payloads,
)

_SOURCE = pathlib.Path("src/lltpm/integrations/github_reader.py")


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

    def test_no_write_verb_appears_anywhere_in_the_module(self):
        text = _SOURCE.read_text()
        for verb in ("POST", "PATCH", "PUT", "DELETE"):
            assert verb not in text, (
                f"'{verb}' appears in the reader; a read-only port should "
                f"not be able to name a write"
            )


class TestTheTokenStaysPrivate:
    def test_the_token_is_not_an_attribute_anyone_can_read(self):
        reader = UrllibGitHubReader(token="t" * 40)
        assert reader.authenticated is True
        assert not any(
            name for name in dir(reader)
            if name == "token" or name == "get_token"
        )

    def test_the_token_is_not_in_the_representation(self):
        reader = UrllibGitHubReader(token="s3cret-token-value-1234567890")
        assert "s3cret" not in repr(reader)

    def test_a_token_is_read_from_the_environment(self, monkeypatch):
        for name in TOKEN_ENVIRONMENT_VARIABLES:
            monkeypatch.delenv(name, raising=False)
        assert UrllibGitHubReader().authenticated is False
        monkeypatch.setenv(TOKEN_ENVIRONMENT_VARIABLES[0], "abc")
        assert UrllibGitHubReader().authenticated is True

    def test_the_lltpm_specific_variable_wins(self, monkeypatch):
        """So a deployment can scope LLTPM's token separately from
        whatever else on the host reads GITHUB_TOKEN."""
        monkeypatch.setenv("GITHUB_TOKEN", "shared")
        monkeypatch.setenv("LLTPM_GITHUB_TOKEN", "scoped")
        assert TOKEN_ENVIRONMENT_VARIABLES[0] == "LLTPM_GITHUB_TOKEN"
        assert UrllibGitHubReader().authenticated

    def test_a_blank_environment_token_is_not_a_token(self, monkeypatch):
        for name in TOKEN_ENVIRONMENT_VARIABLES:
            monkeypatch.setenv(name, "   ")
        assert UrllibGitHubReader().authenticated is False

    def test_no_log_call_in_the_reader_mentions_a_header(self):
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
            assert "headers" not in rendered
            assert "_token" not in rendered


class TestRecordedReader:
    RESPONSES = {
        "repos/acme/atlas/pulls?state=all": [{"number": 1}],
        "/repos/acme/atlas/events": [{"id": "e1"}],
    }

    def test_it_satisfies_the_protocol(self):
        assert isinstance(RecordedGitHubReader({}), GitHubReader)

    def test_the_query_string_is_not_part_of_the_key(self):
        """A recording keyed on the query would miss on a harmless
        per_page change — the query is how a caller asked, not what the
        provider returned."""
        reader = RecordedGitHubReader(self.RESPONSES)
        assert reader.get("repos/acme/atlas/pulls?state=open&per_page=5") == [
            {"number": 1}
        ]

    def test_a_leading_slash_is_irrelevant(self):
        reader = RecordedGitHubReader(self.RESPONSES)
        assert reader.get("repos/acme/atlas/events") == [{"id": "e1"}]

    def test_a_missing_recording_is_a_missing_fixture_not_an_outage(self):
        reader = RecordedGitHubReader({})
        with pytest.raises(SourceUnavailableError, match="missing fixture"):
            reader.get("repos/acme/atlas/pulls")

    def test_it_records_what_was_asked_for(self):
        reader = RecordedGitHubReader(self.RESPONSES)
        reader.get("repos/acme/atlas/pulls?state=all")
        assert reader.requested == ["repos/acme/atlas/pulls"]


class TestEndpointDeclaration:
    def test_the_endpoints_are_declared_with_their_queries(self):
        paths = endpoint_paths(repository="acme/atlas", limit=5)
        assert paths["pull_requests"] == (
            "repos/acme/atlas/pulls?state=all&per_page=5"
        )
        assert set(paths) == set(GITHUB_ENDPOINTS)

    def test_the_default_limit_is_applied(self):
        paths = endpoint_paths(repository="acme/atlas")
        assert f"per_page={DEFAULT_PAGE_LIMIT}" in paths["events"]


class TestFetching:
    @pytest.fixture
    def responses(self):
        return {
            "repos/acme/atlas/pulls": [{"number": 1}, {"number": 2}],
            "repos/acme/atlas/issues": [{"number": 3}],
            "repos/acme/atlas/events": [{"id": "e1"}],
        }

    def test_every_declared_endpoint_is_read(self, responses):
        fetched = fetch_github_payloads(
            reader=RecordedGitHubReader(responses), repository="acme/atlas",
        )
        assert set(fetched) == set(GITHUB_ENDPOINTS)
        assert not fetched.failures

    def test_a_single_object_is_wrapped_in_a_list(self):
        fetched = fetch_github_payloads(
            reader=RecordedGitHubReader(
                {"repos/acme/atlas/pulls": {"number": 1}}
            ),
            repository="acme/atlas", kinds=("pull_requests",),
        )
        assert fetched["pull_requests"] == [{"number": 1}]

    def test_one_failed_endpoint_does_not_discard_the_others(
        self, responses,
    ):
        """A rate limit on events should not throw away the pull requests
        that came back fine."""
        responses.pop("repos/acme/atlas/events")
        fetched = fetch_github_payloads(
            reader=RecordedGitHubReader(responses), repository="acme/atlas",
        )
        assert "pull_requests" in fetched
        assert "events" not in fetched
        assert "events" in fetched.failures

    def test_total_failure_is_distinguishable_from_no_activity(self):
        """An empty result and an outage must not look the same."""
        fetched = fetch_github_payloads(
            reader=RecordedGitHubReader({}), repository="acme/atlas",
        )
        assert not fetched
        assert len(fetched.failures) == len(GITHUB_ENDPOINTS)

    def test_a_repository_with_nothing_in_it_is_not_a_failure(self):
        fetched = fetch_github_payloads(
            reader=RecordedGitHubReader({
                "repos/acme/atlas/pulls": [],
                "repos/acme/atlas/issues": [],
                "repos/acme/atlas/events": [],
            }),
            repository="acme/atlas",
        )
        assert fetched == {
            "pull_requests": [], "issues": [], "events": [],
        }
        assert not fetched.failures

    def test_only_the_requested_kinds_are_read(self, responses):
        reader = RecordedGitHubReader(responses)
        fetch_github_payloads(
            reader=reader, repository="acme/atlas", kinds=("issues",),
        )
        assert reader.requested == ["repos/acme/atlas/issues"]
