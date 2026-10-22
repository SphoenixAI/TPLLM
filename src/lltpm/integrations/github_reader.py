"""Reading GitHub, behind a port.

Two implementations and one protocol, because the two halves of a
self-initializing fake need the same interface: one talks to the live
API, the other replays what was recorded from it. Everything above this
module — normalizing, contract checking, ingesting — is written against
the protocol, so the offline suite and the live check exercise the same
code path rather than two paths that merely agree.

The live reader is standard library only. PyGithub is already an
optional dependency for the agent's tool loop, but making the ingest
path depend on it would mean the organizational stack could not read a
repository without an extra install, and the three endpoints needed here
are plain authenticated GETs.

Read-only by construction: the only HTTP verb this module can issue is
GET. An integration that ingests evidence has no business writing to the
system it is reading, and that is enforced here rather than promised in
a docstring.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

#: Environment variables a token is read from, in order. Never logged,
#: never journalled, never placed in evidence metadata.
TOKEN_ENVIRONMENT_VARIABLES = ("LLTPM_GITHUB_TOKEN", "GITHUB_TOKEN")

DEFAULT_USER_AGENT = "lltpm"
DEFAULT_TIMEOUT_SECONDS = 20.0


class SourceUnavailableError(RuntimeError):
    """The source system could not be read.

    Distinct from a contract breach: this is "we could not look", not
    "what we saw was wrong". Conflating them would let an outage read as
    a provider having broken its API.
    """

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@runtime_checkable
class GitHubReader(Protocol):
    """A read-only window onto one GitHub installation."""

    def get(self, path: str) -> Any:
        """Fetch a JSON document from an API path such as
        ``repos/acme/atlas/pulls?state=all``."""


class UrllibGitHubReader:
    """The live reader. GET only, standard library only."""

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = GITHUB_API_BASE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self._token = token or _token_from_environment()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    @property
    def authenticated(self) -> bool:
        """Whether a token was found. The token itself is not exposed —
        no accessor, no repr, nothing to accidentally log."""
        return bool(self._token)

    def get(self, path: str) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": self._user_agent,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as (
                response
            ):
                body = response.read()
                # The path, never the headers: the Authorization header
                # is in scope here and must not reach a log line.
                logger.debug(
                    "read from source system",
                    extra={
                        "source_system": "github",
                        "external_scope": path.split("?")[0],
                        "outcome": str(response.status),
                    },
                )
                return json.loads(body)
        except urllib.error.HTTPError as error:
            raise SourceUnavailableError(
                f"GitHub returned {error.code} for '{path.split('?')[0]}'.",
                status=error.code,
            ) from None
        except urllib.error.URLError as error:
            raise SourceUnavailableError(
                f"GitHub could not be reached: {error.reason}."
            ) from None
        except json.JSONDecodeError:
            raise SourceUnavailableError(
                f"GitHub returned a non-JSON body for "
                f"'{path.split('?')[0]}'."
            ) from None


def _token_from_environment() -> str | None:
    for name in TOKEN_ENVIRONMENT_VARIABLES:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


class RecordedGitHubReader:
    """Replays recorded responses. The offline half of the fake.

    Keyed by API path with the query string stripped, because the query
    is how a caller asked rather than what the provider returned, and a
    recording keyed on it would miss on a harmless ``per_page`` change.
    """

    def __init__(self, responses: dict[str, Any]):
        self._responses = {
            path.split("?")[0].lstrip("/"): value
            for path, value in responses.items()
        }
        self.requested: list[str] = []

    def get(self, path: str) -> Any:
        key = path.split("?")[0].lstrip("/")
        self.requested.append(key)
        if key not in self._responses:
            raise SourceUnavailableError(
                f"No recorded response for '{key}'. A recorded reader "
                f"never reaches the network, so a missing recording is a "
                f"missing fixture rather than an outage."
            )
        return self._responses[key]


# ── The endpoints the ingest path reads ──────────────────────────────

#: Query strings are part of the declaration: a reviewer can see exactly
#: what is pulled, and `lltpm integrations record` records the same.
GITHUB_ENDPOINTS: dict[str, str] = {
    "pull_requests": "repos/{repository}/pulls?state=all&per_page={limit}",
    "issues": "repos/{repository}/issues?state=all&per_page={limit}",
    "events": "repos/{repository}/events?per_page={limit}",
}

DEFAULT_PAGE_LIMIT = 30


def endpoint_paths(
    *, repository: str, limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, str]:
    return {
        kind: template.format(repository=repository, limit=limit)
        for kind, template in GITHUB_ENDPOINTS.items()
    }


class FetchedPayloads(dict):
    """Raw payloads by kind, plus which reads failed.

    A dict so callers can treat it as one, with the failures attached
    rather than raised: one unavailable endpoint should not discard the
    two that answered, and the caller needs to know which it was.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.failures: dict[str, str] = {}


def fetch_github_payloads(
    *,
    reader: GitHubReader,
    repository: str,
    limit: int = DEFAULT_PAGE_LIMIT,
    kinds: tuple[str, ...] = tuple(GITHUB_ENDPOINTS),
) -> FetchedPayloads:
    """Read the declared endpoints, reporting any that failed.

    Partial success is a real outcome and is returned as one. Raising on
    the first failure would mean a rate limit on the events endpoint
    threw away the pull requests that came back fine.
    """
    fetched = FetchedPayloads()
    paths = endpoint_paths(repository=repository, limit=limit)
    for kind in kinds:
        try:
            payload = reader.get(paths[kind])
        except SourceUnavailableError as error:
            fetched.failures[kind] = str(error)
            continue
        fetched[kind] = payload if isinstance(payload, list) else [payload]

    logger.info(
        "fetched source payloads",
        extra={
            "source_system": "github",
            "external_scope": repository,
            "evidence_count": sum(len(v) for v in fetched.values()),
            "failure_count": len(fetched.failures),
        },
    )
    return fetched
