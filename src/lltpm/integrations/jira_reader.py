"""Reading Jira Cloud, behind a port, plus the webhook seam.

The Jira counterpart of ``github_reader``: one protocol, a live
standard-library implementation, and a recorded implementation that
replays fixtures. Everything above it — normalizing, contract checking,
ingesting — is written against the protocol, so the offline suite and a
live check drive the same code rather than two paths that merely agree.

Read-only by construction. The only HTTP verb this module can name is
GET, asserted against the source rather than promised here. An
integration that ingests evidence has no business writing to the system
it reads, and Jira is a system where a stray write is someone's
reopened ticket.

**Pagination: token, not offset.** Jira Cloud offers both, and this
reader uses the token-paginated enhanced JQL search
(``GET /rest/api/3/search/jql``, ``nextPageToken`` + ``isLast``) rather
than the offset-paginated ``GET /rest/api/3/search``
(``startAt``/``maxResults``). Two reasons, and the second is the real
one:

1. the offset endpoint is the deprecated one on Jira Cloud, so writing
   new code against it would be writing code with a removal date;
2. offset pagination over a result set that is being edited while you
   read it silently skips and repeats rows. ``ORDER BY updated`` is
   exactly the query an ingest path wants, and it is exactly the query
   whose row order changes under you — an issue updated between page one
   and page two moves to the front and pushes an unread issue past the
   offset. A page token is a cursor over a stable snapshot, which is
   what a reader that must not lose evidence needs.

   The deduplication at the journal boundary would catch the repeats. It
   cannot catch the skips, because nothing downstream knows the row
   existed.

**Webhook authenticity.** Nothing in this repository hosts a webhook
receiver yet, so the verification here is a seam rather than a live
path. It exists now because the alternative is a receiver written under
deadline that parses first and verifies later — and a body that reaches
a JSON parser before its signature is checked has already been
processed by attacker-controlled input. ``verify_webhook_signature``
compares an HMAC-SHA256 over the **raw bytes** with
``hmac.compare_digest``, and ``parse_verified_webhook`` refuses before
it parses.

**The webhook lifecycle is a trap, so it is written down.** Jira Cloud
has two kinds of webhook. One is registered by hand in the site's
administration UI and persists. The other — the one an app registers
through the REST API, which is what a hosted multi-tenant LLTPM would
use — **expires about 30 days after it is registered** and must be
extended before then, in batches, through the refresh endpoint. There is
no notification: deliveries simply stop, and the system looks like a
quiet organization rather than a broken integration. That refresh is a
write call, which this read-only port deliberately cannot make, so
:data:`JIRA_WEBHOOK_LIFECYCLE` states the obligation and names the
component that has to carry it.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from enum import Enum
from hashlib import sha256
from typing import Any, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: Jira Cloud's v3 REST root, per site. v3 and not v2: v3 is the current
#: Cloud API and the one that returns Atlassian Document Format, which
#: the normalizer is written against.
JIRA_API_PATH = "/rest/api/3"

#: An OAuth 2.0 (3LO) or Connect bearer token. First choice for an app.
BEARER_TOKEN_ENVIRONMENT_VARIABLES = ("LLTPM_JIRA_TOKEN",)

#: Basic auth with an email address and a personal API token. This is
#: how a person authenticates to Jira Cloud — Atlassian does not issue
#: personal bearer tokens — so both modes are supported rather than
#: forcing a single-user deployment through an OAuth app registration.
BASIC_EMAIL_ENVIRONMENT_VARIABLE = "LLTPM_JIRA_EMAIL"
BASIC_TOKEN_ENVIRONMENT_VARIABLE = "LLTPM_JIRA_API_TOKEN"

#: The shared secret a dynamic webhook registration configures, used to
#: sign deliveries. Never logged, never journalled, never in evidence.
WEBHOOK_SECRET_ENVIRONMENT_VARIABLE = "LLTPM_JIRA_WEBHOOK_SECRET"

DEFAULT_USER_AGENT = "lltpm"
DEFAULT_TIMEOUT_SECONDS = 20.0

#: Attempts per request, including the first. Bounded: a reader that
#: retried until it succeeded would turn a rate limit into an outage of
#: its own making.
DEFAULT_MAX_ATTEMPTS = 3

#: The longest this reader will ever wait on one ``Retry-After``. Jira
#: can ask for minutes; an ingest pass that blocked that long would hold
#: the journal open behind it, so the wait is capped and the read is
#: reported unavailable instead.
MAX_BACKOFF_SECONDS = 30.0

#: Pages one search will walk. A JQL query matching the whole backlog
#: would otherwise read a site's entire history in one pass, and an
#: ingest that cannot finish is worse than one that says it stopped.
DEFAULT_MAX_PAGES = 10

DEFAULT_PAGE_SIZE = 50

#: Status codes worth trying again. 429 is the rate limit; 503 is Jira
#: shedding load. A 4xx that is not 429 is our request being wrong, and
#: repeating it would only be rude.
_RETRYABLE_STATUS = frozenset({429, 503})


class SourceUnavailableError(RuntimeError):
    """Jira could not be read.

    Distinct from a contract breach: this is "we could not look", not
    "what we saw was wrong". Conflating them would let an outage read as
    Atlassian having broken its API.
    """

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class WebhookVerificationError(RuntimeError):
    """A delivery's authenticity could not be established.

    Raised before the body is parsed. The message names what failed and
    never the secret, the computed digest or the claimed one: an
    exception string reaches logs, tickets and screenshots, and a
    comparison oracle is worth more to an attacker than the failure
    itself.
    """


class CredentialMode(str, Enum):
    """Which credential this reader is using, as a label.

    A label and not the credential. An operator needs to know *how* the
    reader authenticated to debug a 401; nobody needs the token, so
    there is no accessor that returns one.
    """

    NONE = "unauthenticated"
    BEARER = "oauth_bearer"
    BASIC = "basic_api_token"


@runtime_checkable
class JiraReader(Protocol):
    """A read-only window onto one Jira Cloud site."""

    def get(self, path: str) -> Any:
        """Fetch a JSON document from an API path such as
        ``search/jql?jql=project%3DABC&maxResults=50``."""


class UrllibJiraReader:
    """The live reader. GET only, standard library only.

    No new dependency: ``atlassian-python-api`` and ``jira`` both exist,
    and both would make the ingest path unable to read a site without an
    extra install for the sake of two authenticated GETs.
    """

    def __init__(
        self,
        *,
        site: str,
        bearer_token: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_backoff_seconds: float = MAX_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._site = (site or "").strip().lower().removeprefix(
            "https://"
        ).rstrip("/")
        bearer = bearer_token or _first_environment_value(
            BEARER_TOKEN_ENVIRONMENT_VARIABLES
        )
        basic_email = email or _environment_value(
            BASIC_EMAIL_ENVIRONMENT_VARIABLE
        )
        basic_token = api_token or _environment_value(
            BASIC_TOKEN_ENVIRONMENT_VARIABLE
        )

        # A bearer token wins when both are configured: it is the
        # app-level credential, and silently preferring the personal one
        # would make a deployment act as an individual.
        if bearer:
            self._mode = CredentialMode.BEARER
            self._authorization = f"Bearer {bearer}"
        elif basic_email and basic_token:
            self._mode = CredentialMode.BASIC
            encoded = b64encode(
                f"{basic_email}:{basic_token}".encode()
            ).decode()
            self._authorization = f"Basic {encoded}"
        else:
            self._mode = CredentialMode.NONE
            self._authorization = ""

        self._timeout = timeout_seconds
        self._user_agent = user_agent
        self._max_attempts = max(1, int(max_attempts))
        self._max_backoff = max(0.0, float(max_backoff_seconds))
        self._sleep = sleep

    @property
    def site(self) -> str:
        return self._site

    @property
    def authenticated(self) -> bool:
        """Whether a usable credential was found. The credential itself
        is not exposed — no accessor, no repr, nothing to log by
        accident."""
        return self._mode is not CredentialMode.NONE

    @property
    def credential_mode(self) -> str:
        """How this reader authenticates, as a label."""
        return self._mode.value

    def __repr__(self) -> str:
        """Deliberately narrow: the site and the mode, never the header.

        ``repr`` ends up in tracebacks and debugger output, which is the
        least controlled place a credential can surface.
        """
        return (
            f"UrllibJiraReader(site={self._site!r}, "
            f"credential_mode={self._mode.value!r})"
        )

    def get(self, path: str) -> Any:
        url = f"https://{self._site}{JIRA_API_PATH}/{path.lstrip('/')}"
        scope = path.split("?")[0]
        headers = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        if self._authorization:
            headers["Authorization"] = self._authorization

        last_status: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            request = urllib.request.Request(
                url, headers=headers, method="GET",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self._timeout,
                ) as response:
                    body = response.read()
                    # The path and the status, never the headers: the
                    # Authorization header is in scope right here and
                    # must not reach a log line.
                    logger.debug(
                        "read from source system",
                        extra={
                            "source_system": "jira",
                            "external_scope": scope,
                            "outcome": str(response.status),
                        },
                    )
                    return json.loads(body)
            except urllib.error.HTTPError as error:
                last_status = error.code
                if (
                    error.code in _RETRYABLE_STATUS
                    and attempt < self._max_attempts
                ):
                    self._sleep(self._backoff_for(error, attempt=attempt))
                    continue
                raise SourceUnavailableError(
                    f"Jira returned {error.code} for '{scope}'.",
                    status=error.code,
                ) from None
            except urllib.error.URLError as error:
                raise SourceUnavailableError(
                    f"Jira could not be reached: {error.reason}."
                ) from None
            except json.JSONDecodeError:
                raise SourceUnavailableError(
                    f"Jira returned a non-JSON body for '{scope}'."
                ) from None

        raise SourceUnavailableError(
            f"Jira returned {last_status} for '{scope}' on every one of "
            f"{self._max_attempts} attempts.",
            status=last_status,
        )

    def _backoff_for(self, error: Any, *, attempt: int) -> float:
        """How long to wait, honouring ``Retry-After`` within the cap.

        Jira's documented guidance is to obey ``Retry-After`` rather than
        inventing a schedule, so it is read first and only clamped. The
        clamp is the honest part: a reader that slept for the four
        minutes Jira sometimes asks for would look hung, so it waits up
        to the cap and otherwise reports the source unavailable.
        """
        retry_after = ""
        headers = getattr(error, "headers", None)
        if headers is not None:
            try:
                retry_after = str(headers.get("Retry-After") or "").strip()
            except Exception:  # pragma: no cover - defensive
                retry_after = ""
        if retry_after:
            try:
                return min(float(retry_after), self._max_backoff)
            except ValueError:
                # A date-formatted Retry-After is legal and not worth
                # parsing here; fall through to the bounded schedule.
                pass
        return min(float(2 ** (attempt - 1)), self._max_backoff)


def _environment_value(name: str) -> str | None:
    value = os.getenv(name)
    if value and value.strip():
        return value.strip()
    return None


def _first_environment_value(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = _environment_value(name)
        if value:
            return value
    return None


class RecordedJiraReader:
    """Replays recorded responses. The offline half of the fake.

    Keyed by API path with the query string stripped — like the GitHub
    recorded reader, because the query is how a caller asked rather than
    what the provider returned — **except** for ``nextPageToken``, which
    is kept. Page two of a search is a different response at the same
    path, so a reader that dropped the token would replay page one
    forever and a pagination test would pass against a single page.
    """

    def __init__(self, responses: dict[str, Any]):
        self._responses = {
            _recording_key(path): value
            for path, value in responses.items()
        }
        self.requested: list[str] = []

    def get(self, path: str) -> Any:
        key = _recording_key(path)
        self.requested.append(key)
        if key not in self._responses:
            raise SourceUnavailableError(
                f"No recorded response for '{key}'. A recorded reader "
                f"never reaches the network, so a missing recording is a "
                f"missing fixture rather than an outage."
            )
        return self._responses[key]


def _recording_key(path: str) -> str:
    """``path`` without its query, plus the page token if there is one."""
    base, _, query = path.lstrip("/").partition("?")
    token = ""
    for key, values in urllib.parse.parse_qs(query).items():
        if key == "nextPageToken" and values:
            token = values[0]
    return f"{base}@{token}" if token else base


# ── The endpoints the ingest path reads ──────────────────────────────

#: Declared with their queries, so a reviewer can see exactly what is
#: pulled. ``expand=changelog`` is the important one: without it the
#: response is a snapshot with no history, and the changelog is where
#: every real transition lives.
JIRA_ENDPOINTS: dict[str, str] = {
    "search": (
        "search/jql?jql={jql}&maxResults={page_size}&expand=changelog"
        "&fields={fields}"
    ),
    "issue": "issue/{issue_id}?expand=changelog&fields={fields}",
    "comments": "issue/{issue_id}/comment?maxResults={page_size}",
    "changelog": "issue/{issue_id}/changelog?maxResults={page_size}",
}

#: The fields the normalizer reads, asked for by name. Jira returns
#: every field by default, which on a site with three hundred custom
#: fields is a payload two orders of magnitude larger than what is used
#: — and most of those fields are things this system deliberately does
#: not read.
DEFAULT_ISSUE_FIELDS: tuple[str, ...] = (
    "summary", "description", "status", "priority", "resolution",
    "duedate", "created", "updated", "project", "issuetype",
    "assignee", "reporter", "creator", "issuelinks", "comment", "parent",
)


def search_path(
    *,
    jql: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    fields: tuple[str, ...] = DEFAULT_ISSUE_FIELDS,
    next_page_token: str = "",
) -> str:
    """One page of a JQL search, as a path.

    The JQL is percent-encoded here rather than by the caller: a query
    with a space or a quote in it is the normal case, and an unencoded
    one fails as a 400 that looks like a permissions problem.
    """
    path = JIRA_ENDPOINTS["search"].format(
        jql=urllib.parse.quote(jql, safe=""),
        page_size=page_size,
        fields=",".join(fields),
    )
    if next_page_token:
        path = (
            f"{path}&nextPageToken="
            f"{urllib.parse.quote(next_page_token, safe='')}"
        )
    return path


class SearchOutcome(BaseModel):
    """The issues a search returned, and whether that is all of them.

    ``exhausted`` is the field that matters. A caller that cannot tell
    "this is the whole result" from "this is the first ten pages" will
    report a quiet project when it actually stopped reading, so the page
    bound being hit is a stated outcome rather than a silent truncation.
    """

    issues: list[dict[str, Any]] = Field(default_factory=list)
    pages_read: int = 0
    exhausted: bool = False
    next_page_token: str = ""
    failure: str = ""

    @property
    def truncated(self) -> bool:
        return not self.exhausted and not self.failure


def search_issues(
    *,
    reader: JiraReader,
    jql: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    fields: tuple[str, ...] = DEFAULT_ISSUE_FIELDS,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> SearchOutcome:
    """Walk a JQL search to its end, or to the page bound.

    Token pagination, for the reasons in the module docstring. The loop
    ends on three conditions and says which: no further token, the
    provider's ``isLast``, or the page bound. A partial read is returned
    rather than raised — an outage on page three should not discard the
    two pages that answered — with the failure attached.
    """
    outcome = SearchOutcome()
    token = ""
    for page in range(1, max(1, max_pages) + 1):
        try:
            response = reader.get(search_path(
                jql=jql, page_size=page_size, fields=fields,
                next_page_token=token,
            ))
        except SourceUnavailableError as error:
            outcome.failure = str(error)
            break

        if not isinstance(response, dict):
            outcome.failure = (
                "a JQL search must return an object with an 'issues' list; "
                "a bare list is the deprecated offset endpoint's shape"
            )
            break

        issues = response.get("issues")
        outcome.issues.extend(
            item for item in (issues or []) if isinstance(item, dict)
        )
        outcome.pages_read = page

        token = str(response.get("nextPageToken") or "")
        # Either signal ends the walk. `isLast` is authoritative when
        # present; a missing token is the end whether or not it is.
        if response.get("isLast") is True or not token:
            outcome.exhausted = True
            outcome.next_page_token = ""
            break
        outcome.next_page_token = token

    logger.info(
        "fetched source payloads",
        extra={
            "source_system": "jira",
            "payload_kind": "issue",
            "evidence_count": len(outcome.issues),
            "failure_count": 1 if outcome.failure else 0,
            "complete": outcome.exhausted,
        },
    )
    return outcome


# ── Webhook authenticity ─────────────────────────────────────────────

#: The header a signed Jira delivery carries. Jira signs the raw body
#: with the secret configured at registration and sends the digest as
#: ``sha256=<hex>``.
WEBHOOK_SIGNATURE_HEADER = "X-Hub-Signature"

_SIGNATURE_PREFIX = "sha256="


def compute_webhook_signature(*, raw_body: bytes, secret: str) -> str:
    """The expected ``sha256=<hex>`` digest for a body.

    Over the raw bytes, never over a re-serialized object: JSON
    round-tripping changes key order and whitespace, so a signature
    checked against re-encoded content would fail on honest deliveries
    and — worse — tempt someone into checking a normalized form that an
    attacker can also produce.
    """
    digest = hmac.new(
        secret.encode(), msg=raw_body, digestmod=sha256,
    ).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def verify_webhook_signature(
    *, raw_body: bytes, signature_header: str, secret: str,
) -> bool:
    """Whether this body was signed with this secret.

    ``hmac.compare_digest`` and not ``==``: a byte-by-byte comparison
    that returns early leaks the position of the first difference
    through its timing, which is enough to recover a valid signature one
    byte at a time.

    A missing secret is a configuration error and returns ``False``
    rather than passing everything through — a receiver that accepted
    unsigned deliveries because nobody set the variable would be the
    whole vulnerability.
    """
    if not secret or not raw_body or not signature_header:
        return False
    claimed = signature_header.strip()
    if claimed.lower().startswith(_SIGNATURE_PREFIX):
        claimed = claimed[len(_SIGNATURE_PREFIX):]
    expected = compute_webhook_signature(
        raw_body=raw_body, secret=secret,
    )[len(_SIGNATURE_PREFIX):]
    return hmac.compare_digest(claimed.lower(), expected)


def parse_verified_webhook(
    *, raw_body: bytes, signature_header: str, secret: str,
) -> dict[str, Any]:
    """The delivery's body, parsed only after it is proven authentic.

    The order is the point. Parsing first and verifying after means
    attacker-controlled bytes have already been through a parser, and
    whatever the parser did — allocated, recursed, raised — happened on
    an unauthenticated request. So verification is the first statement
    and there is no path to ``json.loads`` that skips it.
    """
    if not verify_webhook_signature(
        raw_body=raw_body, signature_header=signature_header, secret=secret,
    ):
        raise WebhookVerificationError(
            "The delivery's signature did not match. The body was not "
            "parsed. (Neither the expected nor the claimed digest is "
            "reported: an exception message reaches logs and tickets, and "
            "a comparison oracle is worth more than the failure.)"
        )
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise WebhookVerificationError(
            f"The delivery was authentic but its body is not JSON: "
            f"{error.msg}."
        ) from None
    if not isinstance(parsed, dict):
        raise WebhookVerificationError(
            "A Jira webhook body is a JSON object; this one is not."
        )
    return parsed


def webhook_secret_from_environment() -> str | None:
    return _environment_value(WEBHOOK_SECRET_ENVIRONMENT_VARIABLE)


class WebhookLifecycle(BaseModel):
    """What a hosted receiver must do to keep deliveries arriving.

    Declared data rather than a paragraph in a design document, because
    the failure mode is silent: a dynamic webhook that expires stops
    delivering with no error anywhere, and the system then reports a
    quiet organization instead of a broken integration. That is the
    worst class of bug this project can ship — a confident report built
    on evidence that stopped arriving.
    """

    registration_path: str
    refresh_path: str
    failed_deliveries_path: str
    expires_after_days: int
    refresh_before_days: int
    requires_periodic_refresh: bool
    refresh_is_a_write: bool
    maximum_ids_per_refresh: int
    obligation: str

    model_config = {"frozen": True}


#: The lifecycle, stated once.
JIRA_WEBHOOK_LIFECYCLE = WebhookLifecycle(
    registration_path=f"{JIRA_API_PATH}/webhook",
    refresh_path=f"{JIRA_API_PATH}/webhook/refresh",
    failed_deliveries_path=f"{JIRA_API_PATH}/webhook/failed",
    # Atlassian documents dynamic webhooks as expiring 30 days after
    # registration unless extended.
    expires_after_days=30,
    # Refresh well inside the window: a refresh job that only runs on
    # day 29 has one chance, and an outage that day costs the
    # registration.
    refresh_before_days=7,
    requires_periodic_refresh=True,
    # Which is why it cannot live here. This port can only read, by
    # construction and by test, so the obligation belongs to the hosted
    # receiver that registered the webhook in the first place.
    refresh_is_a_write=True,
    maximum_ids_per_refresh=100,
    obligation=(
        "A webhook registered through the REST API expires about 30 days "
        "after registration and must be extended before then, in batches "
        "of at most 100 ids, through the refresh endpoint. Nothing warns "
        "you: deliveries simply stop, and LLTPM would report a quiet "
        "organization rather than a dead integration. Extending is a "
        "write, which this read-only port cannot and must not perform, so "
        "the hosted receiver that registers a webhook owns refreshing it "
        "— and owns reconciling with a JQL search over `updated` after "
        "any gap, because a missed delivery is never redelivered once the "
        "failed-deliveries window has passed."
    ),
)


#: What this integration can and cannot do, for the provider registry
#: and for anyone deciding how far to trust its output.
JIRA_READER_CAPABILITIES: dict[str, bool] = {
    "read_only": True,
    "token_pagination": True,
    "offset_pagination": False,
    "rate_limit_aware": True,
    "webhook_signature_verification": True,
    "webhook_registration": False,
    "webhook_refresh": False,
    "live_recording_available": False,
}
