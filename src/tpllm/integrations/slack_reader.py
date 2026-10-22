"""Reading Slack, behind a port.

The same shape as the GitHub reader and for the same reason: one
protocol with a live implementation and a replay implementation, so the
offline suite and a future live check drive the same code rather than
two paths that merely agree.

Standard library only. The ingest path should be able to read a channel
without an extra install, and the methods needed here are plain
authenticated GETs.

Read-only by construction: the only HTTP verb this module can name is
GET. An integration that ingests evidence has no business writing to
the system it reads, and a test parses this file to enforce it rather
than trusting the sentence you are reading.

Three things Slack makes harder than GitHub does, each handled
explicitly because the failure mode of handling it implicitly is a
silent wrong answer.

**Slack reports failure with HTTP 200.** The body carries ``ok: false``
and an ``error`` string. A client that only checked the status code
would read an authentication failure as an empty channel — which is
indistinguishable, downstream, from a channel where nobody said
anything. So ``ok`` is checked on every response and a false one raises.

**Slack rate-limits per method, and says for how long.** A 429 carries
``Retry-After``. The policy here waits what Slack asked for, bounded;
retries a bounded number of times; and when the budget is exhausted
raises rather than returning what it managed to collect. A rate limit
must not spin, must not silently truncate, and must not report success
— the three things a naive retry loop does.

**Slack paginates with an opaque cursor.** A page boundary is not a
stopping point, and a reader that stopped at the first page would
report a quiet channel. So pagination follows ``next_cursor`` up to a
declared maximum, and if the maximum is hit while a cursor remains, the
result says so and carries the cursor. Truncation is reported, never
assumed to be the end.

This module also holds the webhook signature seam. Nothing in the
project hosts webhooks yet; the verification is here anyway, because
the alternative is that the first thing to accept a Slack delivery
either invents its own check or skips one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from tpllm.integrations.github_reader import SourceUnavailableError

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"

#: Environment variables a token is read from, in order. The
#: TPLLM-specific name wins so a deployment can scope this integration's
#: token separately from whatever else on the host reads
#: ``SLACK_BOT_TOKEN``. Never logged, never journalled, never placed in
#: evidence metadata.
TOKEN_ENVIRONMENT_VARIABLES = ("TPLLM_SLACK_TOKEN", "SLACK_BOT_TOKEN")

#: Where the signing secret is read from. Separate from the token: one
#: authenticates us to Slack, the other authenticates Slack to us, and
#: a deployment that conflated them would accept forged deliveries.
SIGNING_SECRET_ENVIRONMENT_VARIABLES = (
    "TPLLM_SLACK_SIGNING_SECRET", "SLACK_SIGNING_SECRET",
)

DEFAULT_USER_AGENT = "tpllm"
DEFAULT_TIMEOUT_SECONDS = 20.0

#: Query parameters that identify *what* the provider returned, as
#: opposed to *how* a caller asked. Only these take part in a recorded
#: reader's key, for the reason the GitHub reader strips the query
#: entirely: a recording keyed on ``limit`` would miss on a harmless
#: page-size change, while one that ignored ``channel`` would serve one
#: channel's messages for another's.
IDENTIFYING_QUERY_PARAMETERS = ("channel", "cursor", "ts", "oldest", "latest")


class SlackApiError(SourceUnavailableError):
    """Slack answered, and the answer was an error.

    A subclass of ``SourceUnavailableError`` so a caller that only
    wants "we could not look" keeps working, and its own type so a
    caller that can act on ``ratelimited`` or ``channel_not_found``
    can. The Slack error code is carried; the token never is.
    """

    def __init__(
        self,
        message: str,
        *,
        error: str = "",
        status: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message, status=status)
        self.error = error
        #: What the provider asked us to wait, when it said. ``None``
        #: means it did not, which is different from zero.
        self.retry_after = retry_after

    @property
    def rate_limited(self) -> bool:
        return self.error == "ratelimited"


class SlackRateLimitPolicy(BaseModel):
    """What this reader is willing to do about a 429.

    Configurable because the right answer depends on the deployment: a
    nightly backfill can afford to wait, an interactive command cannot.
    Every bound exists so that the reader fails loudly instead of
    becoming a slow infinite loop.
    """

    #: Total attempts per request, including the first. One means never
    #: retry, which is a legitimate choice for an interactive command.
    max_attempts: int = Field(default=4, ge=1, le=20)

    #: The longest single wait honoured. Slack has been known to ask for
    #: minutes on a heavily throttled method; an interactive caller
    #: should fail rather than appear to hang, so a longer request is
    #: refused rather than clamped-and-waited.
    max_wait_seconds: float = Field(default=30.0, gt=0)

    #: Used when a 429 arrives with no usable ``Retry-After``. Slack's
    #: documentation says the header is sent; this is what happens when
    #: it is not, rather than retrying instantly in a tight loop.
    default_retry_after_seconds: float = Field(default=1.0, gt=0)

    #: The most pages one paginated read will follow. A channel with a
    #: decade of history is not a reason to make an unbounded number of
    #: calls, and hitting this bound is reported rather than silently
    #: treated as the end of the channel.
    max_pages: int = Field(default=20, ge=1, le=1000)

    model_config = {"frozen": True}


@runtime_checkable
class SlackReader(Protocol):
    """A read-only window onto one Slack workspace."""

    def get(self, path: str) -> Any:
        """Fetch a JSON document from an API method path such as
        ``conversations.history?channel=C0ABCDEF&limit=100``."""


class UrllibSlackReader:
    """The live reader. GET only, standard library only."""

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = SLACK_API_BASE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
        rate_limit_policy: SlackRateLimitPolicy | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self._token = token or _token_from_environment()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._user_agent = user_agent
        self.policy = rate_limit_policy or SlackRateLimitPolicy()
        # Injected so the tests can prove the backoff waited the right
        # amount without actually waiting it.
        self._sleep = sleep or time.sleep

    @property
    def authenticated(self) -> bool:
        """Whether a token was found. The token itself is not exposed —
        no accessor, no repr, nothing to accidentally log."""
        return bool(self._token)

    def __repr__(self) -> str:
        # Written out rather than defaulted, so a future field cannot
        # start appearing in a traceback alongside the token.
        return (
            f"UrllibSlackReader(base_url={self._base_url!r}, "
            f"authenticated={self.authenticated})"
        )

    def get(self, path: str) -> Any:
        method = path.split("?")[0].lstrip("/")
        waited = 0.0
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return self._attempt(path, method=method)
            except SlackApiError as error:
                if not error.rate_limited:
                    raise
                retry_after = (
                    error.retry_after
                    if error.retry_after is not None
                    else self.policy.default_retry_after_seconds
                )
                if attempt >= self.policy.max_attempts:
                    # The budget is spent. Raising is the point: a
                    # caller handed a partial answer labelled success
                    # would act on it.
                    raise
                if retry_after > self.policy.max_wait_seconds:
                    raise SlackApiError(
                        f"Slack rate-limited '{method}' and asked for "
                        f"{retry_after:.0f}s, beyond the "
                        f"{self.policy.max_wait_seconds:.0f}s this reader "
                        f"will wait; the read was not completed.",
                        error="ratelimited", status=429,
                    ) from None
                logger.warning(
                    "source system rate-limited the read; waiting as asked",
                    extra={
                        "source_system": "slack",
                        "external_scope": method,
                        "outcome": "ratelimited",
                        "duration_ms": int(retry_after * 1000),
                    },
                )
                self._sleep(retry_after)
                waited += retry_after
        # Unreachable: the loop either returns or raises. Kept as a
        # guard so a future edit to the bounds cannot fall through to
        # returning None, which would read as an empty channel.
        raise SlackApiError(
            f"Slack rate-limited '{method}' for {waited:.0f}s and the read "
            f"was not completed.",
            error="ratelimited", status=429,
        )

    def _attempt(self, path: str, *, method: str) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout,
            ) as response:
                body = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            if error.code == 429:
                raise SlackApiError(
                    f"Slack rate-limited '{method}'.",
                    error="ratelimited", status=429,
                    retry_after=_retry_after_seconds(error),
                ) from None
            raise SourceUnavailableError(
                f"Slack returned {error.code} for '{method}'.",
                status=error.code,
            ) from None
        except urllib.error.URLError as error:
            raise SourceUnavailableError(
                f"Slack could not be reached: {error.reason}."
            ) from None

        # The method and the status, never the headers: the Authorization
        # header is in scope here and must not reach a log line.
        logger.debug(
            "read from source system",
            extra={
                "source_system": "slack",
                "external_scope": method,
                "outcome": str(status),
            },
        )
        try:
            document = json.loads(body)
        except json.JSONDecodeError:
            raise SourceUnavailableError(
                f"Slack returned a non-JSON body for '{method}'."
            ) from None
        return check_slack_response(document, method=method)


def _retry_after_seconds(error: Any) -> float | None:
    """The ``Retry-After`` a 429 carried, or ``None`` if it carried none.

    ``None`` and ``0`` are different answers — "Slack did not say" and
    "Slack said go ahead" — so the absent case is not defaulted here.
    The policy's default is applied at the retry site, where it is
    visible beside the bound it is subject to.
    """
    try:
        raw = str(error.headers.get("Retry-After") or "").strip()
    except AttributeError:
        return None
    if not raw:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        # Slack documents an integer-seconds header. The HTTP-date form
        # the RFC also permits is not parsed, because guessing a wait
        # from a format we have never observed is worse than falling
        # back to the declared default.
        return None


DEFAULT_RATE_LIMIT_POLICY = SlackRateLimitPolicy()


def check_slack_response(document: Any, *, method: str) -> Any:
    """The response, or an error if Slack said ``ok: false``.

    Slack signals failure inside a 200 body. A client that trusted the
    status code would read ``invalid_auth`` as an empty channel, and an
    empty channel is a real and meaningful answer — so the two must
    never be conflated.
    """
    if not isinstance(document, dict):
        raise SourceUnavailableError(
            f"Slack returned a {type(document).__name__} for '{method}', "
            f"not an object."
        )
    if document.get("ok") is True:
        return document
    error = str(document.get("error") or "unknown_error")
    status = 429 if error == "ratelimited" else None
    raise SlackApiError(
        f"Slack refused '{method}': {error}.", error=error, status=status,
    )


def _token_from_environment() -> str | None:
    for name in TOKEN_ENVIRONMENT_VARIABLES:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def signing_secret_from_environment() -> str | None:
    """The signing secret, or ``None``. Returned rather than stored on a
    reader, so nothing long-lived holds it."""
    for name in SIGNING_SECRET_ENVIRONMENT_VARIABLES:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


class RecordedSlackReader:
    """Replays recorded responses. The offline half of the fake.

    Keyed by method name plus only the parameters that identify what
    Slack returned — the channel, the cursor, the message ts, the time
    window. ``limit`` and the rest describe how a caller asked and are
    ignored, so a recording does not miss on a harmless page-size
    change.
    """

    def __init__(self, responses: dict[str, Any]):
        self._responses = {
            recording_key(path): value for path, value in responses.items()
        }
        self.requested: list[str] = []

    def get(self, path: str) -> Any:
        key = recording_key(path)
        self.requested.append(key)
        if key not in self._responses:
            raise SourceUnavailableError(
                f"No recorded response for '{key}'. A recorded reader "
                f"never reaches the network, so a missing recording is a "
                f"missing fixture rather than an outage."
            )
        document = self._responses[key]
        return check_slack_response(
            document, method=key.split("?")[0],
        ) if isinstance(document, dict) else document


def recording_key(path: str) -> str:
    """A stable key for one recorded response.

    The method, then the identifying parameters in a fixed order, so
    ``channel=C1&limit=5`` and ``limit=200&channel=C1`` are one key.
    """
    method, _, query = path.lstrip("/").partition("?")
    parsed = urllib.parse.parse_qs(query, keep_blank_values=False)
    parts = [
        f"{name}={parsed[name][0]}"
        for name in IDENTIFYING_QUERY_PARAMETERS
        if parsed.get(name)
    ]
    return f"{method}?{'&'.join(parts)}" if parts else method


# ── The methods the ingest path reads ────────────────────────────────

#: Declared with their queries, so a reviewer can see exactly what is
#: pulled. Read-only methods only; there is no entry here that could
#: change anything in a workspace even if a write verb existed.
SLACK_METHODS: dict[str, str] = {
    "history": "conversations.history?channel={channel}&limit={limit}",
    "replies": (
        "conversations.replies?channel={channel}&ts={ts}&limit={limit}"
    ),
    "channel": "conversations.info?channel={channel}",
}

DEFAULT_PAGE_LIMIT = 100


def method_path(
    name: str,
    *,
    channel: str,
    limit: int = DEFAULT_PAGE_LIMIT,
    ts: str = "",
    cursor: str = "",
) -> str:
    path = SLACK_METHODS[name].format(channel=channel, limit=limit, ts=ts)
    if cursor:
        path = f"{path}&cursor={urllib.parse.quote(cursor, safe='')}"
    return path


class PaginatedRead(BaseModel):
    """Everything one paginated read collected, and whether it is all.

    ``truncated`` and ``next_cursor`` exist so a bounded read cannot be
    mistaken for a complete one. A reader that returned the pages it
    managed and said nothing would turn a long channel into a short one,
    and every count derived from it would be wrong in a direction
    nobody could see.
    """

    items: list[dict[str, Any]] = Field(default_factory=list)
    pages_read: int = 0
    truncated: bool = False
    next_cursor: str = ""

    @property
    def complete(self) -> bool:
        return not self.truncated


def read_paginated(
    *,
    reader: SlackReader,
    method: str,
    channel: str,
    ts: str = "",
    limit: int = DEFAULT_PAGE_LIMIT,
    item_key: str = "messages",
    policy: SlackRateLimitPolicy | None = None,
) -> PaginatedRead:
    """Follow Slack's cursor to the end, or to the declared bound.

    Stopping early is a real outcome and is returned as one rather than
    raised: a caller that got nineteen pages of a twenty-page channel
    should be able to use them *and* know what it is missing. What is
    never returned is a truncated read that claims to be complete.
    """
    policy = policy or DEFAULT_RATE_LIMIT_POLICY
    collected: list[dict[str, Any]] = []
    cursor = ""
    pages = 0

    while pages < policy.max_pages:
        document = reader.get(method_path(
            method, channel=channel, limit=limit, ts=ts, cursor=cursor,
        ))
        pages += 1
        items = document.get(item_key) if isinstance(document, dict) else None
        if isinstance(items, list):
            collected.extend(
                item for item in items if isinstance(item, dict)
            )
        metadata = (
            document.get("response_metadata")
            if isinstance(document, dict) else None
        )
        cursor = str(
            (metadata or {}).get("next_cursor") or ""
        ).strip() if isinstance(metadata, dict) else ""
        if not cursor:
            return PaginatedRead(
                items=collected, pages_read=pages, truncated=False,
            )

    logger.warning(
        "paginated read stopped at the declared page bound",
        extra={
            "source_system": "slack",
            "external_scope": channel,
            "evidence_count": len(collected),
            "complete": False,
        },
    )
    return PaginatedRead(
        items=collected, pages_read=pages, truncated=True,
        next_cursor=cursor,
    )


# ── Webhook signature verification ──────────────────────────────────
#
# A seam, built before anything hosts webhooks, because the first thing
# that accepts a Slack delivery will otherwise either invent its own
# check or skip one. Slack's documented v0 scheme: HMAC-SHA256 over
# "v0:{timestamp}:{raw_body}" with the app's signing secret, compared
# in constant time, with a stale timestamp rejected to bound replay.

SIGNATURE_VERSION = "v0"

#: How old a delivery may be. Slack's documented recommendation is five
#: minutes. The window is what makes a captured delivery useless to
#: replay later: the signature stays valid forever, so the timestamp is
#: the only thing that expires.
DEFAULT_REPLAY_WINDOW_SECONDS = 300


class SignatureInvalidError(ValueError):
    """A delivery did not prove it came from Slack.

    Every message this is raised with is a fixed string. The signing
    secret, the computed digest and the provided digest are all absent
    on purpose: an exception message reaches logs, tracebacks and bug
    reports, and a comparison printed alongside its inputs is an oracle.
    """


def signature_base_string(*, timestamp: str, raw_body: bytes) -> bytes:
    """The exact bytes Slack signs.

    The raw body, not a re-serialization of a parsed body: any
    re-encoding — key order, whitespace, unicode escaping — changes the
    bytes and breaks a signature that was perfectly valid. This is why
    verification has to happen before parsing, and why ``raw_body`` is
    ``bytes`` rather than ``str`` or ``dict``.
    """
    return b":".join([
        SIGNATURE_VERSION.encode(),
        str(timestamp).encode(),
        raw_body,
    ])


def compute_signature(
    *, signing_secret: str, timestamp: str, raw_body: bytes,
) -> str:
    """The ``v0=<hex>`` signature for one delivery."""
    digest = hmac.new(
        signing_secret.encode(),
        signature_base_string(timestamp=timestamp, raw_body=raw_body),
        hashlib.sha256,
    ).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


def verify_signature(
    *,
    signing_secret: str,
    timestamp: Any,
    raw_body: bytes,
    provided_signature: str,
    now: float,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
) -> None:
    """Raise unless this delivery provably came from Slack, recently.

    The timestamp is checked first, in the documented order and for the
    documented reason: a signature never expires, so without the window
    a delivery captured once could be replayed forever, and checking it
    second would mean doing the HMAC work on a request already known to
    be unacceptable.

    ``now`` is a parameter rather than a call to the clock so the replay
    window is testable at all. A window that could only be exercised by
    waiting five minutes would not be exercised.
    """
    if not signing_secret:
        raise SignatureInvalidError(
            "No Slack signing secret is configured, so no delivery can be "
            "verified; accepting one unverified would accept a forgery."
        )

    try:
        sent_at = float(str(timestamp).strip())
    except (TypeError, ValueError):
        raise SignatureInvalidError(
            "The delivery carried no usable X-Slack-Request-Timestamp, so "
            "its age cannot be bounded."
        ) from None

    if abs(now - sent_at) > replay_window_seconds:
        raise SignatureInvalidError(
            f"The delivery's timestamp is outside the "
            f"{replay_window_seconds}s replay window."
        )

    expected = compute_signature(
        signing_secret=signing_secret, timestamp=str(timestamp).strip(),
        raw_body=raw_body,
    )
    if not hmac.compare_digest(expected, str(provided_signature or "")):
        # compare_digest rather than ==, so the time taken does not
        # depend on how much of the digest matched.
        raise SignatureInvalidError(
            "The delivery's signature does not match; it was not signed "
            "with this app's signing secret, or the body was altered in "
            "transit."
        )
