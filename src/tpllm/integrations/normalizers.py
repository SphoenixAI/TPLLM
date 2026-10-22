"""Source normalizers — the anti-corruption layer.

Every adapter in this package returned its own shapes: ``PRSummary``,
``BuildStatus``, ``SlackMessage``. The journal and the orchestrator
accept exactly one shape, ``EvidenceEnvelope``. ``WrappedToolAdapter``
has always been the seam between the two and took a ``normalize``
callable — and no production code ever supplied one. So real GitHub,
Jira and Slack data could reach the LLM tool loop and could not reach
a single engine. This module is the missing translation.

It is an anti-corruption layer in Evans' sense: a provider's model is
translated at the boundary so that its vocabulary never leaks inward.
When GitHub renames a field, the change lands here and nowhere else.

Five rules, each of which exists because the alternative is a specific
kind of lie.

**A key never depends on our clock.** The published guidance on webhook
delivery is unanimous: key on the provider's own event id, not on a
payload hash and not on a timestamp, because the same delivery can
legitimately arrive with a different timestamp. So each shape declares
its own stable identity — a webhook delivery id, an Events API event id,
or, for a polled REST resource, the resource's identity plus its
version (``node_id`` + ``updated_at``). Re-normalizing the same payload
an hour later produces the same key, and a test proves it.

**Reality time is the time of the fact, not of the fetch.** A pull
request merged on February 25th and polled today occurred in February.
Envelopes carry ``observed_at`` (when it happened) separately from
``received_at`` (when we learned), which is Snodgrass' valid time and
transaction time at the ingest boundary. CloudEvents has a single
``time`` and permits a producer to substitute the current time when the
occurrence time is unknown; here that substitution would corrupt every
downstream temporal claim, so a payload with no usable timestamp is
rejected rather than stamped ``now``.

**A project is never guessed.** A normalizer cannot know which TPLLM
project a repository belongs to. Bindings are declared; a payload with
no binding is reported as unbound, not quietly filed under a plausible
project. This is the identity invariant applied to scope: an ambiguous
link is left for a person to settle.

**An uninterpretable event produces nothing.** There is a fixed
vocabulary of evidence classes the stack understands. A GitHub event
with no honest mapping — a push, a comment thread, a review that only
voiced an opinion — is reported as uninterpreted rather than coerced
into the nearest-looking class. Inventing an event class is how a
system starts reporting organizational facts that never happened.

**Identity is proposed, never asserted.** An author's handle becomes an
actor reference carrying the external handle, which the identity layer
then resolves under its own conservative policy. Nothing here claims two
handles are one person, and nothing here records what anyone did with
their time: the actor reference says who holds a dependency or an
authority, which is organizational mechanics, not activity tracking.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from tpllm.coordination.models import OrganizationalActorRef
from tpllm.integrations.envelopes import EvidenceEnvelope, make_envelope

logger = logging.getLogger(__name__)

#: How much source prose an envelope carries. Evidence is a pointer plus
#: an excerpt; the source system remains the system of record, and a
#: journal that grew to hold every pull request body would be a copy of
#: GitHub rather than a record of the organization.
CONTENT_EXCERPT_LIMIT = 2000

#: Labels that make an issue a declared blocker rather than ordinary
#: work. Configurable per deployment; the default is deliberately short
#: because a label vocabulary nobody declared is a guess.
DEFAULT_BLOCKING_LABELS = frozenset({"blocked", "blocker", "blocking"})


# ── Time ─────────────────────────────────────────────────────────────


class TimestampMissingError(ValueError):
    """A payload carried no timestamp the normalizer could trust.

    Raised rather than defaulted. CloudEvents permits a producer to
    substitute the current time when an occurrence time is unknown; the
    bitemporal layer here would then record a February merge as having
    happened today, and every scenario rooted in it would inherit the
    error.
    """


def to_naive_utc(value: Any) -> datetime:
    """Parse a provider timestamp into the naive-UTC the stack uses.

    The runtime's clock is naive UTC throughout (``_now()`` in the CLI
    strips the zone), while every provider emits RFC 3339 with a ``Z``.
    Mixing the two raises ``TypeError`` on the first comparison, which
    in this codebase means inside the orchestrator's temporal ordering —
    far from the cause. So conversion happens once, here, at the edge.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        # fromisoformat only learned to parse a trailing 'Z' in 3.11;
        # being explicit costs nothing and survives a backport.
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as error:
            raise TimestampMissingError(
                f"Could not parse '{value}' as a timestamp."
            ) from error
    else:
        raise TimestampMissingError(
            "A payload needs a timestamp; the current time is not a "
            "substitute for when something actually happened."
        )

    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _first_timestamp(payload: dict[str, Any], *fields: str) -> datetime:
    """The first present timestamp among ``fields``, in order of
    preference. Order is the normalizer's claim about which moment the
    fact belongs to, so it is always stated explicitly at the call."""
    for field in fields:
        value = payload.get(field)
        if value:
            return to_naive_utc(value)
    raise TimestampMissingError(
        f"None of {list(fields)} carried a usable timestamp."
    )


# ── Bindings ─────────────────────────────────────────────────────────


class SourceBinding(BaseModel):
    """A declared link from a source-system scope to TPLLM projects.

    ``external_scope`` is whatever the provider calls the container a
    payload lives in: ``owner/repo`` for GitHub, a project key for Jira,
    a channel id for Slack.
    """
    source_system: str
    external_scope: str
    project_ids: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class ProjectBindings(BaseModel):
    """The declared mapping, and nothing inferred.

    Lookup is exact and case-insensitive on the scope, because provider
    casing is inconsistent (``Owner/Repo`` and ``owner/repo`` are the
    same repository) while a near-match is not the same thing at all.
    """
    bindings: list[SourceBinding] = Field(default_factory=list)

    def projects_for(
        self, *, source_system: str, external_scope: str,
    ) -> list[str] | None:
        """Declared projects, or ``None`` when nothing is bound.

        ``None`` and ``[]`` are different answers: nothing is bound
        versus bound to no project on purpose.
        """
        wanted = (external_scope or "").strip().lower()
        for binding in self.bindings:
            if binding.source_system != source_system:
                continue
            if binding.external_scope.strip().lower() == wanted:
                return list(binding.project_ids)
        return None

    @classmethod
    def from_mapping(
        cls, *, source_system: str, mapping: dict[str, list[str]],
    ) -> "ProjectBindings":
        return cls(bindings=[
            SourceBinding(
                source_system=source_system,
                external_scope=scope,
                project_ids=list(projects),
            )
            for scope, projects in sorted(mapping.items())
        ])


# ── Outcome ──────────────────────────────────────────────────────────


class SkippedPayload(BaseModel):
    """One payload that produced no envelope, and why.

    Reported rather than dropped. A normalizer that silently discards
    what it does not understand makes "no evidence" and "evidence we
    could not read" indistinguishable.
    """
    source_system: str
    external_scope: str = ""
    payload_kind: str = ""
    payload_id: str = ""
    reason: str


class NormalizationOutcome(BaseModel):
    """What a batch of raw payloads became."""
    envelopes: list[EvidenceEnvelope] = Field(default_factory=list)
    unbound: list[SkippedPayload] = Field(default_factory=list)
    uninterpreted: list[SkippedPayload] = Field(default_factory=list)
    rejected: list[SkippedPayload] = Field(default_factory=list)

    @property
    def skipped_count(self) -> int:
        return len(self.unbound) + len(self.uninterpreted) + len(self.rejected)

    @property
    def needs_attention(self) -> bool:
        """An unbound scope or a rejected payload is a person's problem.

        Uninterpreted events are not: declining to interpret a push is
        the designed behaviour, not a gap.
        """
        return bool(self.unbound or self.rejected)

    def merge(self, other: "NormalizationOutcome") -> "NormalizationOutcome":
        return NormalizationOutcome(
            envelopes=[*self.envelopes, *other.envelopes],
            unbound=[*self.unbound, *other.unbound],
            uninterpreted=[*self.uninterpreted, *other.uninterpreted],
            rejected=[*self.rejected, *other.rejected],
        )


# ── GitHub ───────────────────────────────────────────────────────────

GITHUB = "github"

#: Evidence classes the stack recognizes, from ``runtime.sources`` and
#: ``integrations.runtime``. A mapping target outside this set would
#: reach the temporal layer as an unrecognized string and silently
#: degrade to generic observed evidence, so the set is asserted.
RECOGNIZED_EVENT_CLASSES = frozenset({
    "action_requested",
    "action_resolved",
    "architecture_evidence",
    "blocker_opened",
    "blocker_resolved",
    "chat_message",
    "decision_conclusive",
    "decision_proposed",
    "decision_reopened",
    "deployment",
    "document_updated",
    "kpi_observation",
    "meeting_transcript",
    "metric_observation",
    "outcome_observed",
    "pr_merged",
    "supporting_evidence",
    "ticket_completed",
    "ticket_updated",
})

#: Review states, and what each one is actually evidence of. The
#: rationale is kept beside the mapping because these are the
#: interpretation guardrails the project states elsewhere in prose: an
#: opinion is not a decision, and a requested change is a real blocker.
_REVIEW_STATE_MAP: dict[str, tuple[str, str]] = {
    "approved": (
        "supporting_evidence",
        "an approval is a gate passed — evidence supporting readiness, "
        "not itself a decision",
    ),
    "changes_requested": (
        "blocker_opened",
        "the change cannot proceed until the requested changes land",
    ),
}

#: Review states deliberately left uninterpreted, with the reason.
_REVIEW_STATE_IGNORED: dict[str, str] = {
    "commented": (
        "a review comment is an opinion; an opinion is not a decision"
    ),
    "dismissed": (
        "a dismissed review asserts nothing about the change's state"
    ),
    "pending": "an unsubmitted review has not been said to anyone yet",
}

#: Events API types this normalizer interprets. Everything else is
#: reported as uninterpreted with the reason below.
_EVENT_TYPE_IGNORED: dict[str, str] = {
    "PushEvent": (
        "a push is not an organizational fact; merged code is technical "
        "evidence and is read from the pull request instead"
    ),
    "CreateEvent": "creating a branch or tag asserts nothing organizational",
    "DeleteEvent": "deleting a branch or tag asserts nothing organizational",
    "ForkEvent": "a fork is not a fact about this organization's work",
    "WatchEvent": "a star is not a fact about this organization's work",
    "MemberEvent": (
        "membership changes belong to the identity layer, which resolves "
        "them under its own policy rather than as evidence"
    ),
}


def _repository_of(payload: dict[str, Any]) -> str:
    """``owner/name`` from whichever shape the payload uses.

    The REST resource nests it under ``repository.full_name``; the
    Events API uses ``repo.name``, which already contains the owner.
    """
    repository = payload.get("repository") or {}
    if isinstance(repository, dict) and repository.get("full_name"):
        return str(repository["full_name"])
    repo = payload.get("repo") or {}
    if isinstance(repo, dict) and repo.get("name"):
        return str(repo["name"])
    return ""


def _actor_refs(payload: dict[str, Any], *keys: str) -> list[
    OrganizationalActorRef
]:
    """Actor references for the handles a payload names.

    A handle is proposed to the identity layer, never resolved here.
    ``canonical_entity_id`` is deliberately left unset: deciding that
    two handles are one person is the identity engine's job, under a
    policy that refuses a mere name resemblance.
    """
    refs: list[OrganizationalActorRef] = []
    seen: set[str] = set()
    for key in keys:
        candidate = payload.get(key) or {}
        if not isinstance(candidate, dict):
            continue
        login = str(candidate.get("login") or "").strip()
        if not login or login in seen:
            continue
        seen.add(login)
        refs.append(OrganizationalActorRef(
            actor_id=login,
            display_name=str(candidate.get("name") or login),
        ))
    return refs


def _excerpt(*parts: Any) -> tuple[str | None, bool]:
    """Joined prose, truncated to an excerpt. Returns (text, truncated)."""
    text = "\n\n".join(
        str(part).strip() for part in parts if part and str(part).strip()
    )
    if not text:
        return None, False
    if len(text) <= CONTENT_EXCERPT_LIMIT:
        return text, False
    return text[:CONTENT_EXCERPT_LIMIT], True


def _metadata(
    *,
    repository: str,
    extra: dict[str, Any] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"repository": repository}
    if truncated:
        # Stated, so nothing downstream reads an excerpt as the whole
        # text and concludes something the full body contradicts.
        metadata["content_truncated"] = True
    metadata.update(extra or {})
    return metadata


def _labels(payload: dict[str, Any]) -> list[str]:
    return sorted({
        str(label.get("name") or "").strip().lower()
        for label in (payload.get("labels") or [])
        if isinstance(label, dict) and label.get("name")
    } - {""})


def normalize_github_pull_request(
    pull_request: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    repository: str = "",
    action: str | None = None,
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """One pull request, polled or delivered, as evidence.

    ``action`` is the webhook's action when there is one; a polled
    resource has none and the state is derived from the object itself.
    ``delivery_id`` is the provider's delivery identity, which takes
    precedence as the idempotency key when present.

    The three interpretations:

    - merged → ``pr_merged``, at ``merged_at``. Merged code is technical
      evidence, never feature completion; the guardrail lives in the
      refresh-scope policy and this mapping respects it.
    - closed unmerged → ``action_resolved``, at ``closed_at``: the review
      that was outstanding no longer is.
    - open and ready → ``action_requested``, at ``created_at``: a review
      is being asked of someone.

    A draft is uninterpreted. It asks nothing of anyone yet, and
    reporting it as a request for review would manufacture an
    obligation that does not exist.
    """
    outcome = NormalizationOutcome()
    repository = repository or _repository_of(pull_request)
    number = pull_request.get("number")
    payload_id = f"pull/{number}" if number is not None else "pull/?"

    node_id = str(pull_request.get("node_id") or "")
    if not node_id or not repository:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="pull_request", payload_id=payload_id,
            reason=(
                "a pull request needs both a node_id and a repository to be "
                "identified; neither is inferable"
            ),
        ))
        return outcome

    projects = bindings.projects_for(
        source_system=GITHUB, external_scope=repository,
    )
    if projects is None:
        outcome.unbound.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="pull_request", payload_id=payload_id,
            reason=(
                f"no project is bound to repository '{repository}'; the "
                f"evidence is real but which project it belongs to is a "
                f"declaration, not a guess"
            ),
        ))
        return outcome

    if pull_request.get("draft") and not pull_request.get("merged_at"):
        outcome.uninterpreted.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="pull_request", payload_id=payload_id,
            reason=(
                "a draft pull request requests nothing of anyone yet"
            ),
        ))
        return outcome

    try:
        if pull_request.get("merged_at"):
            event_class = "pr_merged"
            observed_at = to_naive_utc(pull_request["merged_at"])
        elif str(pull_request.get("state") or "").lower() == "closed":
            event_class = "action_resolved"
            observed_at = _first_timestamp(
                pull_request, "closed_at", "updated_at",
            )
        else:
            event_class = "action_requested"
            observed_at = _first_timestamp(
                pull_request, "created_at", "updated_at",
            )
    except TimestampMissingError as error:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="pull_request", payload_id=payload_id,
            reason=str(error),
        ))
        return outcome

    content, truncated = _excerpt(
        pull_request.get("title"), pull_request.get("body"),
    )
    base = pull_request.get("base") or {}
    outcome.envelopes.append(make_envelope(
        source_system=GITHUB,
        source_type="pull_request",
        source_id=f"{repository}#{number}",
        event_type=event_class,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=str(pull_request.get("title") or "") or None,
        content=content,
        actor_refs=_actor_refs(pull_request, "user", "merged_by"),
        source_pointer=str(pull_request.get("html_url") or "") or None,
        metadata=_metadata(
            repository=repository,
            truncated=truncated,
            extra={
                "pull_request_number": number,
                "pull_request_state": pull_request.get("state"),
                "labels": _labels(pull_request),
                "base_ref": base.get("ref") if isinstance(base, dict) else None,
                "github_action": action,
            },
        ),
        idempotency_key=github_pull_request_key(
            node_id=node_id,
            updated_at=pull_request.get("updated_at"),
            event_class=event_class,
            delivery_id=delivery_id,
        ),
    ))
    return outcome


def github_pull_request_key(
    *,
    node_id: str,
    updated_at: Any,
    event_class: str,
    delivery_id: str | None = None,
) -> str:
    """The stable identity of one pull-request observation.

    A webhook delivery id wins when present: it is the provider's own
    event identity and stays the same across a redelivery, which is
    exactly what the key has to mean.

    Polling has no delivery id, so identity is the resource plus its
    version — ``node_id`` and ``updated_at``. Re-polling an unchanged
    pull request yields the same key and deduplicates; a genuine change
    bumps ``updated_at`` and is a new fact. The process clock appears
    nowhere, because a key that moved with the clock would turn one
    delivery into an unbounded stream of duplicates.

    The event class is part of the key: one pull request legitimately
    produces a request-for-review and later a merge, and those are two
    organizational facts rather than two versions of one.
    """
    if delivery_id:
        return f"github:delivery:{delivery_id}:{event_class}"
    version = str(updated_at or "").strip() or "unversioned"
    return f"github:pull_request:{node_id}:{version}:{event_class}"


def normalize_github_review(
    review: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    repository: str,
    pull_request_number: Any = None,
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """One submitted review.

    An approval is evidence supporting readiness; requested changes open
    a blocker. A comment is neither, and is reported as uninterpreted —
    the project's own guardrail is that an opinion is not a decision, and
    this is where that guardrail has to actually hold.
    """
    outcome = NormalizationOutcome()
    node_id = str(review.get("node_id") or review.get("id") or "")
    payload_id = f"review/{node_id or '?'}"
    state = str(review.get("state") or "").strip().lower()

    if not node_id or not repository:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="review", payload_id=payload_id,
            reason="a review needs an id and a repository to be identified",
        ))
        return outcome

    projects = bindings.projects_for(
        source_system=GITHUB, external_scope=repository,
    )
    if projects is None:
        outcome.unbound.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="review", payload_id=payload_id,
            reason=f"no project is bound to repository '{repository}'",
        ))
        return outcome

    if state not in _REVIEW_STATE_MAP:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="review", payload_id=payload_id,
            reason=_REVIEW_STATE_IGNORED.get(
                state, f"review state '{state}' has no honest mapping",
            ),
        ))
        return outcome

    event_class, rationale = _REVIEW_STATE_MAP[state]
    try:
        observed_at = _first_timestamp(review, "submitted_at", "updated_at")
    except TimestampMissingError as error:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="review", payload_id=payload_id,
            reason=str(error),
        ))
        return outcome

    content, truncated = _excerpt(review.get("body"))
    outcome.envelopes.append(make_envelope(
        source_system=GITHUB,
        source_type="pull_request_review",
        source_id=f"{repository}#{pull_request_number}:review:{node_id}",
        event_type=event_class,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=f"Review {state} on #{pull_request_number}",
        content=content,
        actor_refs=_actor_refs(review, "user"),
        source_pointer=str(review.get("html_url") or "") or None,
        metadata=_metadata(
            repository=repository,
            truncated=truncated,
            extra={
                "pull_request_number": pull_request_number,
                "review_state": state,
                "interpretation": rationale,
            },
        ),
        # A submitted review is immutable, so its own id is its version.
        idempotency_key=(
            f"github:delivery:{delivery_id}:{event_class}" if delivery_id
            else f"github:review:{node_id}:{event_class}"
        ),
    ))
    return outcome


def normalize_github_issue(
    issue: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    repository: str = "",
    action: str | None = None,
    delivery_id: str | None = None,
    blocking_labels: frozenset[str] = DEFAULT_BLOCKING_LABELS,
) -> NormalizationOutcome:
    """One issue.

    A labelled blocker opens and resolves a blocker; anything else is
    ordinary ticket movement. The label vocabulary is a parameter
    because it is a per-organization declaration — inferring "this looks
    like a blocker" from a title would be exactly the guess this layer
    exists to refuse.

    GitHub's issues endpoint also returns pull requests. Those are
    skipped here and read by the pull-request normalizer, so one merge
    does not arrive twice as two different kinds of fact.
    """
    outcome = NormalizationOutcome()
    repository = repository or _repository_of(issue)
    number = issue.get("number")
    payload_id = f"issue/{number}" if number is not None else "issue/?"

    if issue.get("pull_request"):
        outcome.uninterpreted.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="issue", payload_id=payload_id,
            reason=(
                "this issue is a pull request; it is normalized as a pull "
                "request so the same fact is not counted twice"
            ),
        ))
        return outcome

    node_id = str(issue.get("node_id") or "")
    if not node_id or not repository:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="issue", payload_id=payload_id,
            reason="an issue needs a node_id and a repository",
        ))
        return outcome

    projects = bindings.projects_for(
        source_system=GITHUB, external_scope=repository,
    )
    if projects is None:
        outcome.unbound.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="issue", payload_id=payload_id,
            reason=f"no project is bound to repository '{repository}'",
        ))
        return outcome

    labels = _labels(issue)
    blocking = bool(set(labels) & set(blocking_labels))
    closed = str(issue.get("state") or "").lower() == "closed"
    if blocking:
        event_class = "blocker_resolved" if closed else "blocker_opened"
    else:
        event_class = "ticket_completed" if closed else "ticket_updated"

    try:
        observed_at = (
            _first_timestamp(issue, "closed_at", "updated_at") if closed
            else _first_timestamp(issue, "updated_at", "created_at")
        )
    except TimestampMissingError as error:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="issue", payload_id=payload_id,
            reason=str(error),
        ))
        return outcome

    content, truncated = _excerpt(issue.get("title"), issue.get("body"))
    outcome.envelopes.append(make_envelope(
        source_system=GITHUB,
        source_type="issue",
        source_id=f"{repository}!{number}",
        event_type=event_class,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=str(issue.get("title") or "") or None,
        content=content,
        actor_refs=_actor_refs(issue, "user", "closed_by"),
        source_pointer=str(issue.get("html_url") or "") or None,
        metadata=_metadata(
            repository=repository,
            truncated=truncated,
            extra={
                "issue_number": number,
                "issue_state": issue.get("state"),
                "labels": labels,
                "declared_blocker": blocking,
                "github_action": action,
            },
        ),
        idempotency_key=(
            f"github:delivery:{delivery_id}:{event_class}" if delivery_id
            else (
                f"github:issue:{node_id}:"
                f"{str(issue.get('updated_at') or 'unversioned')}:"
                f"{event_class}"
            )
        ),
    ))
    return outcome


def normalize_github_event(
    event: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
) -> NormalizationOutcome:
    """One object from the repository Events API.

    The Events API carries the same payload shapes a webhook delivers,
    with the event's own stable ``id``, so it is both a real source and
    the recordable stand-in for webhook payloads that cannot be fetched
    without an installation.
    """
    outcome = NormalizationOutcome()
    event_type = str(event.get("type") or "")
    event_id = str(event.get("id") or "")
    repository = _repository_of(event)
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    if not event_id:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="event", payload_id=event_type or "?",
            reason=(
                "an event with no id has no stable identity, so it cannot "
                "be deduplicated and must not be journalled"
            ),
        ))
        return outcome

    if event_type in _EVENT_TYPE_IGNORED:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="event", payload_id=f"{event_type}/{event_id}",
            reason=_EVENT_TYPE_IGNORED[event_type],
        ))
        return outcome

    action = str(payload.get("action") or "") or None

    if event_type == "PullRequestEvent" and payload.get("pull_request"):
        return normalize_github_pull_request(
            payload["pull_request"], bindings=bindings,
            received_at=received_at, repository=repository, action=action,
            delivery_id=f"event-{event_id}",
        )

    if event_type == "PullRequestReviewEvent" and payload.get("review"):
        pull_request = payload.get("pull_request") or {}
        return normalize_github_review(
            payload["review"], bindings=bindings, received_at=received_at,
            repository=repository,
            pull_request_number=pull_request.get("number"),
            delivery_id=f"event-{event_id}",
        )

    if event_type == "IssuesEvent" and payload.get("issue"):
        return normalize_github_issue(
            payload["issue"], bindings=bindings, received_at=received_at,
            repository=repository, action=action,
            delivery_id=f"event-{event_id}",
        )

    if event_type == "IssueCommentEvent" and payload.get("comment"):
        return _normalize_github_comment(
            event=event, payload=payload, bindings=bindings,
            received_at=received_at, repository=repository,
            event_id=event_id,
        )

    outcome.uninterpreted.append(SkippedPayload(
        source_system=GITHUB, external_scope=repository,
        payload_kind="event", payload_id=f"{event_type}/{event_id}",
        reason=(
            f"'{event_type}' has no declared mapping; an event class is "
            f"never invented to make a payload fit"
        ),
    ))
    return outcome


def _normalize_github_comment(
    *,
    event: dict[str, Any],
    payload: dict[str, Any],
    bindings: ProjectBindings,
    received_at: datetime,
    repository: str,
    event_id: str,
) -> NormalizationOutcome:
    """A comment thread is discussion, which is evidence and no more.

    It maps to ``chat_message``, whose refresh scope is evidence-only —
    so a comment can inform understanding and can never, on its own,
    move a decision or a readiness assessment.
    """
    outcome = NormalizationOutcome()
    comment = payload.get("comment") or {}
    issue = payload.get("issue") or {}
    payload_id = f"comment/{comment.get('id') or '?'}"

    projects = bindings.projects_for(
        source_system=GITHUB, external_scope=repository,
    )
    if projects is None:
        outcome.unbound.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="comment", payload_id=payload_id,
            reason=f"no project is bound to repository '{repository}'",
        ))
        return outcome

    try:
        observed_at = _first_timestamp(
            comment, "created_at", "updated_at",
        ) if comment.get("created_at") or comment.get("updated_at") else (
            _first_timestamp(event, "created_at")
        )
    except TimestampMissingError as error:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="comment", payload_id=payload_id,
            reason=str(error),
        ))
        return outcome

    content, truncated = _excerpt(comment.get("body"))
    outcome.envelopes.append(make_envelope(
        source_system=GITHUB,
        source_type="comment",
        source_id=f"{repository}:comment:{comment.get('id')}",
        event_type="chat_message",
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=(
            f"Comment on #{issue.get('number')}" if issue.get("number")
            else "Comment"
        ),
        content=content,
        actor_refs=_actor_refs(comment, "user"),
        source_pointer=str(comment.get("html_url") or "") or None,
        metadata=_metadata(
            repository=repository,
            truncated=truncated,
            extra={"issue_number": issue.get("number")},
        ),
        idempotency_key=f"github:event:{event_id}:chat_message",
    ))
    return outcome


def normalize_github_webhook(
    *,
    event_name: str,
    delivery_id: str,
    payload: dict[str, Any],
    bindings: ProjectBindings,
    received_at: datetime,
) -> NormalizationOutcome:
    """One webhook delivery.

    ``delivery_id`` is GitHub's ``X-GitHub-Delivery`` header, which is
    stable across a redelivery of the same event. Published guidance is
    consistent that this — the provider's own event id — is the
    idempotency key, in preference to a payload hash or a timestamp, and
    it is threaded through to the key here.

    Deduplication alone is not sufficient and is not claimed to be: the
    journal append that follows is itself idempotent and transactional,
    so a crash between recognizing a delivery and acting on it cannot
    leave the delivery marked seen but unprocessed.
    """
    outcome = NormalizationOutcome()
    repository = _repository_of(payload)
    if not delivery_id:
        outcome.rejected.append(SkippedPayload(
            source_system=GITHUB, external_scope=repository,
            payload_kind="webhook", payload_id=event_name,
            reason=(
                "a delivery with no id cannot be deduplicated; the "
                "X-GitHub-Delivery header is the key and is not optional"
            ),
        ))
        return outcome

    action = str(payload.get("action") or "") or None

    if event_name == "pull_request" and payload.get("pull_request"):
        return normalize_github_pull_request(
            payload["pull_request"], bindings=bindings,
            received_at=received_at, repository=repository, action=action,
            delivery_id=delivery_id,
        )
    if event_name == "pull_request_review" and payload.get("review"):
        pull_request = payload.get("pull_request") or {}
        return normalize_github_review(
            payload["review"], bindings=bindings, received_at=received_at,
            repository=repository,
            pull_request_number=pull_request.get("number"),
            delivery_id=delivery_id,
        )
    if event_name == "issues" and payload.get("issue"):
        return normalize_github_issue(
            payload["issue"], bindings=bindings, received_at=received_at,
            repository=repository, action=action, delivery_id=delivery_id,
        )

    outcome.uninterpreted.append(SkippedPayload(
        source_system=GITHUB, external_scope=repository,
        payload_kind="webhook", payload_id=f"{event_name}/{delivery_id}",
        reason=(
            f"webhook event '{event_name}' has no declared mapping"
        ),
    ))
    return outcome


def normalize_github_payloads(
    *,
    events: list[dict[str, Any]] | None = None,
    pull_requests: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
    bindings: ProjectBindings,
    received_at: datetime,
    repository: str = "",
) -> NormalizationOutcome:
    """Normalize a mixed batch and report everything that was skipped."""
    outcome = NormalizationOutcome()
    for event in events or []:
        outcome = outcome.merge(normalize_github_event(
            event, bindings=bindings, received_at=received_at,
        ))
    for pull_request in pull_requests or []:
        outcome = outcome.merge(normalize_github_pull_request(
            pull_request, bindings=bindings, received_at=received_at,
            repository=repository,
        ))
    for issue in issues or []:
        outcome = outcome.merge(normalize_github_issue(
            issue, bindings=bindings, received_at=received_at,
            repository=repository,
        ))

    logger.info(
        "normalized source payloads into evidence",
        extra={
            "source_system": GITHUB,
            "evidence_count": len(outcome.envelopes),
            "skipped_count": outcome.skipped_count,
            "unbound_count": len(outcome.unbound),
            "rejected_count": len(outcome.rejected),
        },
    )
    return outcome


def normalize_payload(
    *,
    payload_kind: str,
    payload: dict[str, Any],
    bindings: ProjectBindings,
    received_at: datetime,
    scope: str = "",
    delivery_id: str | None = None,
    event_name: str = "",
) -> NormalizationOutcome:
    """GitHub's uniform entry point for the provider registry.

    Every provider exposes this same signature so the CLI and the
    cross-provider tests can treat providers interchangeably instead of
    carrying a conditional per provider. ``scope`` is whatever the
    provider calls the container a payload lives in — here, a
    repository.

    An unknown kind is reported rather than guessed at, for the same
    reason an unmapped event class is: a dispatcher that fell back to
    "probably a pull request" would invent a fact.
    """
    if payload_kind == "pull_request":
        return normalize_github_pull_request(
            payload, bindings=bindings, received_at=received_at,
            repository=scope, delivery_id=delivery_id,
        )
    if payload_kind == "issue":
        return normalize_github_issue(
            payload, bindings=bindings, received_at=received_at,
            repository=scope, delivery_id=delivery_id,
        )
    if payload_kind == "review":
        return normalize_github_review(
            payload, bindings=bindings, received_at=received_at,
            repository=scope, delivery_id=delivery_id,
        )
    if payload_kind == "event":
        return normalize_github_event(
            payload, bindings=bindings, received_at=received_at,
        )
    if payload_kind == "webhook":
        return normalize_github_webhook(
            event_name=event_name, delivery_id=delivery_id or "",
            payload=payload, bindings=bindings, received_at=received_at,
        )

    outcome = NormalizationOutcome()
    outcome.rejected.append(SkippedPayload(
        source_system=GITHUB, external_scope=scope,
        payload_kind=payload_kind, payload_id="?",
        reason=(
            f"'{payload_kind}' is not a GitHub payload kind this "
            f"normalizer handles; a dispatcher that guessed would invent "
            f"a fact"
        ),
    ))
    return outcome
