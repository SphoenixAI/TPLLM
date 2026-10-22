"""Slack payloads to ``EvidenceEnvelope`` — the anti-corruption layer
for a chat workspace.

The five rules stated in ``normalizers``' module docstring hold here
unchanged, and the shared machinery is imported rather than copied so a
fix to ``ProjectBindings`` or ``to_naive_utc`` lands once. What follows
is only what is *particular* to Slack, because each particular is a new
way the same five rules could be broken.

**A channel name is never a project.** GitHub at least has one
repository per payload; Slack has a channel whose name somebody typed.
``#atlas-launch`` looks like it belongs to the Atlas project and may
well be where the Payments team argues about something else. Bindings
are declared on ``(workspace_id, channel_id)`` — the two opaque ids
Slack guarantees are stable — and an unbound channel is reported, never
filed under the project its name resembles. Renaming a channel changes
nothing, which is the point.

**A Slack timestamp is a ``ts``, and a ``ts`` is not a clock reading of
ours.** ``"1740462339.123456"`` is seconds since the epoch with six
further digits that Slack uses to keep messages unique within a second.
The documented convention is to read them as microseconds, and that is
what this module does; the integer part alone would collapse two
messages posted in the same second into one reality time. A payload
whose ``ts`` is missing, zero or unparseable is rejected, never stamped
``now``.

**An edit is a new fact, not a correction of an old one.** The journal
is append-only and bitemporal, so "v2 is approved" at 09:00 and "v3 is
approved" at 09:20 are two facts with two reality times, and a question
asked as of 09:10 must still see v2. This is the opposite of what a
chat client does and the only behaviour a temporal store can support:
overwriting would make every historical answer silently change.

**A deletion removes a source, not a piece of history.** Somebody
deleting a message in Slack cannot un-say what the organization already
acted on. So a deletion appends evidence of its own — "this message
existed, and its source is no longer retrievable" — carrying the
availability change in metadata for the durability seam to act on. The
original evidence record is never touched. The concept and its
vocabulary are ``persistence.models.SourceAvailability`` and
``runtime.durability.append_source_availability_changed``; neither is
imported, because ``integrations`` is a domain and no domain depends on
``persistence``. The values are mirrored below and a test asserts the
mirror has not drifted.

**A thread is a conversation, and losing its shape loses the argument.**
A reply carries its thread root, its own ts, its channel and its
author, so downstream reasoning can follow proposal → disagreement →
clarification → conclusion. Flattening a thread into independent
messages is how a system concludes that four people agreed when three
of them were objecting to the first one.

**Identity is proposed.** The Slack user id becomes an actor reference
and nothing more. ``canonical_entity_id`` is never set here, and a
display name is never used as identity: display names are
self-service, collide freely, and change without notice.

What this module deliberately declines to interpret is listed in
``_DECLINED_SUBTYPES``, ``_DECLINED_CHANNEL_TYPES`` and
``_DECLINED_EVENT_TYPES``, each with the reason, and every decline is
reported rather than dropped. The reactions entry is worth reading: a
reaction count is the beginning of a responsiveness metric, and this
system does not measure people.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from tpllm.coordination.models import OrganizationalActorRef
from tpllm.integrations.envelopes import make_envelope
from tpllm.integrations.normalizers import (
    CONTENT_EXCERPT_LIMIT,
    NormalizationOutcome,
    ProjectBindings,
    SkippedPayload,
    SourceBinding,
    TimestampMissingError,
)

logger = logging.getLogger(__name__)

SLACK = "slack"

#: Payload kinds ``normalize_payload`` dispatches on. ``webhook`` is the
#: Events API shape arriving by delivery rather than by poll, so it
#: shares the ``event`` contract; the other five are distinct shapes.
SLACK_PAYLOAD_KINDS: tuple[str, ...] = (
    "message",
    "message_changed",
    "message_deleted",
    "channel_scope",
    "event",
    "webhook",
)

#: Mirrors ``persistence.models.SourceAvailability.DELETED`` by value.
#: Not imported: ``integrations`` is a domain and
#: ``test_architecture_boundaries`` asserts no domain depends on
#: ``persistence``. ``test_slack_normalizer`` imports both and asserts
#: the two agree, so the mirror cannot drift unnoticed.
SOURCE_AVAILABILITY_DELETED = "deleted"

#: The epoch Slack's ``ts`` counts from, as a naive-UTC datetime so the
#: arithmetic never touches the process clock or a local zone.
_EPOCH = datetime(1970, 1, 1)


# ── Event classes ────────────────────────────────────────────────────
#
# Every value below is in the closed vocabulary the stack maps
# (`runtime.sources.EVENT_CLASS_TO_TEMPORAL_TYPE` and
# `integrations.runtime._EVENT_SCOPE_MAP`). A class outside it would
# reach the temporal layer as an unrecognized string and degrade
# silently to generic observed evidence; a test asserts the membership.

#: A message in a channel is discussion. ``chat_message``'s refresh
#: scope is evidence-only, which is exactly the guardrail wanted here:
#: somebody typing "ok ship it" is evidence about a decision and is
#: never, on its own, the decision.
MESSAGE_EVENT_CLASS = "chat_message"

#: A deletion is also an observation about a chat channel, so it takes
#: the same evidence-only class. It must not be able to move anything:
#: the fact it carries is about a *source*, and the organizational
#: reading lives in the availability metadata rather than in a class
#: that would trigger a recompute.
AVAILABILITY_EVENT_CLASS = "chat_message"

#: A channel topic or purpose is the one piece of text in a channel
#: that the whole channel shares and that somebody deliberately edited
#: — "Release 4.2, code freeze Nov 3" is a statement of scope, not one
#: person's opinion. ``document_updated`` is the only class in the
#: vocabulary that means that, and its refresh scope (evidence plus
#: project understanding) is the right reach: a declared scope change
#: should inform what we think the project is, and should not by itself
#: move a KPI or a launch gate.
CHANNEL_SCOPE_EVENT_CLASS = "document_updated"


# ── Declines, each with its reason ───────────────────────────────────

#: Message subtypes this normalizer refuses to interpret. Reported, not
#: dropped: "no evidence" and "evidence we could not read" must stay
#: distinguishable.
_DECLINED_SUBTYPES: dict[str, str] = {
    "bot_message": (
        "a bot posting into a channel is an echo of a fact that already "
        "has a source — a build, a deploy, a ticket transition — and "
        "ingesting the echo would count the same fact twice, with Slack "
        "as its provenance instead of the system it actually happened in"
    ),
    "channel_join": (
        "who is in a channel belongs to the identity layer, which "
        "resolves membership under its own conservative policy rather "
        "than treating it as evidence about the work"
    ),
    "channel_leave": (
        "who is in a channel belongs to the identity layer, not to "
        "evidence about the work"
    ),
    "channel_name": (
        "renaming a channel changes no organizational fact; bindings key "
        "on the channel id precisely so a rename is a non-event"
    ),
    "channel_archive": (
        "archiving a channel is a workspace-administration act, not a "
        "statement about the work the channel was about"
    ),
    "channel_unarchive": (
        "unarchiving a channel is a workspace-administration act"
    ),
    "thread_broadcast": (
        "a broadcast is an existing thread reply shown again in the "
        "channel; it is normalized from the thread so one utterance does "
        "not become two facts"
    ),
    "message_replied": (
        "a deprecated bookkeeping echo that a thread gained a reply; the "
        "reply itself is the fact and is normalized from the thread"
    ),
    "file_share": (
        "a file upload asserts nothing on its own, and the accompanying "
        "comment arrives as an ordinary message"
    ),
    "tombstone": (
        "a tombstone is Slack's placeholder for content it has already "
        "removed; there is no fact left in it to read"
    ),
}

#: Conversation types this normalizer refuses to read at all, whatever
#: the bindings say.
_DECLINED_CHANNEL_TYPES: dict[str, str] = {
    "im": (
        "a direct message is private correspondence between two people; "
        "an agent that journalled it would be surveilling them, and no "
        "organizational question needs it"
    ),
    "mpim": (
        "a group direct message is private correspondence; the same "
        "reasoning as a one-to-one applies"
    ),
}

#: Events API event types with no honest mapping.
_DECLINED_EVENT_TYPES: dict[str, str] = {
    "reaction_added": (
        "a reaction is not organizational truth. It has no text, so it "
        "asserts nothing checkable, and the only thing it can be turned "
        "into is a count per person — which is a responsiveness metric, "
        "and this system does not measure people. Treating a thumbs-up "
        "as an approval would also manufacture a decision out of a "
        "gesture nobody was asked to make"
    ),
    "reaction_removed": (
        "the same reasoning as a reaction added, and removing one "
        "asserts even less"
    ),
    "member_joined_channel": (
        "channel membership belongs to the identity layer"
    ),
    "member_left_channel": (
        "channel membership belongs to the identity layer"
    ),
    "channel_created": (
        "a new channel is not bound to a project until somebody declares "
        "the binding; creating it asserts nothing"
    ),
    "channel_rename": (
        "bindings key on the channel id, so a rename is a non-event"
    ),
    "pin_added": (
        "a pin is a reading aid; the pinned message is the fact and is "
        "normalized on its own"
    ),
    "pin_removed": "unpinning a message asserts nothing about the work",
    "user_change": (
        "a profile change belongs to the identity layer, which decides "
        "under its own policy whether two handles are one person"
    ),
    "app_mention": (
        "an app mention is the same message the channel already "
        "delivered; normalizing both would double one utterance"
    ),
}


# ── Scope and bindings ───────────────────────────────────────────────


def slack_scope(*, workspace_id: str, channel_id: str) -> str:
    """The external scope string for one channel.

    ``workspace_id`` is included because channel ids are only unique
    within a workspace, and a multi-tenant deployment that keyed on the
    channel alone would eventually bind one organization's channel to
    another's project.
    """
    return f"{workspace_id.strip()}/{channel_id.strip()}"


def slack_binding(
    *, workspace_id: str, channel_id: str, project_ids: list[str],
) -> SourceBinding:
    """One declared ``(workspace, channel) -> projects`` link.

    A typed constructor rather than a bare string, so a caller cannot
    accidentally declare a binding on a channel *name* — which is the
    one thing this layer refuses to read.
    """
    return SourceBinding(
        source_system=SLACK,
        external_scope=slack_scope(
            workspace_id=workspace_id, channel_id=channel_id,
        ),
        project_ids=list(project_ids),
    )


def slack_bindings(
    *, mapping: dict[tuple[str, str], list[str]],
) -> ProjectBindings:
    """Declared Slack bindings, built on the shared machinery.

    Many-to-many in both directions and deliberately so: one channel
    legitimately serves several projects, and one project is discussed
    in several channels. Neither is an error to be collapsed.
    """
    return ProjectBindings(bindings=[
        slack_binding(
            workspace_id=workspace, channel_id=channel,
            project_ids=list(projects),
        )
        for (workspace, channel), projects in sorted(mapping.items())
    ])


class ScopeConflictError(ValueError):
    """The payload and the caller disagreed about which channel this is.

    Raised rather than resolved. Preferring either one would be a guess
    about which is right, and the consequence of guessing wrong is
    evidence filed under the wrong project.
    """


def _scope_parts(
    payload: dict[str, Any],
    *,
    workspace_id: str = "",
    channel_id: str = "",
    scope: str = "",
) -> tuple[str, str]:
    """``(workspace_id, channel_id)`` from the payload and the caller.

    A polled ``conversations.history`` item carries no channel at all —
    the caller knows which channel it asked for — while an Events API
    payload carries both. So the two sources are combined, with the
    payload authoritative about itself, and a genuine disagreement
    raises instead of picking a winner.
    """
    asked_workspace = workspace_id.strip()
    asked_channel = channel_id.strip()
    if scope and "/" in scope:
        head, _, tail = scope.partition("/")
        asked_workspace = asked_workspace or head.strip()
        asked_channel = asked_channel or tail.strip()
    elif scope.strip():
        asked_channel = asked_channel or scope.strip()

    stated_workspace = str(
        payload.get("team") or payload.get("team_id") or ""
    ).strip()
    stated_channel = str(payload.get("channel") or "").strip()

    for stated, asked, what in (
        (stated_workspace, asked_workspace, "workspace"),
        (stated_channel, asked_channel, "channel"),
    ):
        if stated and asked and stated != asked:
            raise ScopeConflictError(
                f"the payload names {what} '{stated}' and the caller asked "
                f"for '{asked}'; a normalizer does not choose between them"
            )

    return (
        stated_workspace or asked_workspace,
        stated_channel or asked_channel,
    )


# ── Time ─────────────────────────────────────────────────────────────


def slack_ts_to_naive_utc(value: Any) -> datetime:
    """A Slack ``ts`` as the naive UTC the stack uses throughout.

    ``"1740462339.123456"`` is epoch seconds plus six digits Slack uses
    to disambiguate messages within the same second; the documented
    convention reads them as microseconds, and dropping them would give
    two distinct messages one reality time.

    Zero, blank and unparseable are all rejected. A ``ts`` of ``"0"``
    would otherwise date a fact to 1970 and quietly anchor every
    scenario built on it.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text = f"{float(value):.6f}"
    elif isinstance(value, str) and value.strip():
        text = value.strip()
    else:
        raise TimestampMissingError(
            "A Slack payload needs a ts; the current time is not a "
            "substitute for when something was actually said."
        )

    whole, _, fraction = text.partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        raise TimestampMissingError(
            f"Could not read '{value}' as a Slack ts; a Slack timestamp is "
            f"epoch seconds with a six-digit suffix."
        )

    # Built from the two integer parts rather than through a float: a
    # float cannot hold ten digits of seconds plus six of fraction and
    # keep the last one, so two messages a microsecond apart would
    # collapse into one reality time.
    epoch_seconds = int(whole)
    microseconds = int(fraction.ljust(6, "0")[:6]) if fraction else 0
    if epoch_seconds <= 0:
        raise TimestampMissingError(
            f"'{value}' is not a usable Slack ts; a non-positive epoch is "
            f"not a time anything happened."
        )
    try:
        return _EPOCH + timedelta(
            seconds=epoch_seconds, microseconds=microseconds,
        )
    except OverflowError:
        raise TimestampMissingError(
            f"'{value}' is outside the range of representable times."
        ) from None


def _require_positive_ts(value: Any, *, what: str) -> datetime:
    text = str(value or "").strip()
    if not text or text.startswith("0.") or text in {"0", "0.0"}:
        raise TimestampMissingError(
            f"{what} carried no usable ts, and the fetch time is not a "
            f"substitute for when the fact occurred."
        )
    return slack_ts_to_naive_utc(text)


# ── Idempotency ──────────────────────────────────────────────────────


def slack_message_key(
    *,
    workspace_id: str,
    channel_id: str,
    ts: str,
    event_class: str,
    edit_marker: str = "",
    delivery_id: str | None = None,
) -> str:
    """The stable identity of one message observation.

    Slack's own event id wins when there is one: it is unchanged across
    a retry of the same delivery, which is what the key must mean.

    A polled message has no delivery id, so identity is the message
    plus its version — ``(workspace, channel, ts)`` identifies the
    message for as long as it exists, and ``edited.ts`` is the only
    thing that changes when its text does. Re-reading an unedited
    message therefore yields the same key and appends nothing, while an
    edit is a new fact with a new key. Our clock appears nowhere: a key
    that moved with it would turn one message into an unbounded stream
    of messages, and ``received_at`` is deliberately not a parameter.

    The payload text is not in the key either. Hashing it would make
    two people who said the same sentence one fact, and would make a
    whitespace fix a new one.
    """
    if delivery_id:
        return f"slack:event:{delivery_id}:{event_class}"
    version = (edit_marker or "").strip() or "v0"
    return (
        f"slack:message:{workspace_id}/{channel_id}:{ts}:{version}:"
        f"{event_class}"
    )


def slack_availability_key(
    *, workspace_id: str, channel_id: str, deleted_ts: str,
) -> str:
    """The identity of "this message is gone".

    The one key that deliberately does not prefer the delivery id. For
    every other shape the delivery identifies the fact; here the fact is
    *about a subject* — message X no longer exists — and a subject
    cannot be deleted twice. Keying on the deleted message therefore
    makes the key the identity of the fact rather than of the
    notification, so two deliveries, or a delivery and a later
    reconciliation pass, collapse to one availability change instead of
    two.
    """
    return (
        f"slack:availability:{workspace_id}/{channel_id}:{deleted_ts}:"
        f"{SOURCE_AVAILABILITY_DELETED}"
    )


def slack_message_source_id(
    *, workspace_id: str, channel_id: str, ts: str,
) -> str:
    """A pointer to one message, independent of what it currently says.

    Stable across edits on purpose: the edit is a new fact about the
    same message, and a source id that moved would sever the two.
    """
    return f"{workspace_id}/{channel_id}:{ts}"


# ── Shared pieces ────────────────────────────────────────────────────


def _excerpt(*parts: Any) -> tuple[str | None, bool]:
    """Joined prose, truncated to an excerpt. Returns (text, truncated).

    The journal holds a pointer plus an excerpt. Slack stays the system
    of record; a journal that grew to hold every message would be a
    copy of the workspace rather than a record of the organization.
    """
    text = "\n\n".join(
        str(part).strip() for part in parts if part and str(part).strip()
    )
    if not text:
        return None, False
    if len(text) <= CONTENT_EXCERPT_LIMIT:
        return text, False
    return text[:CONTENT_EXCERPT_LIMIT], True


def _actor_refs(*user_ids: Any) -> list[OrganizationalActorRef]:
    """Actor references for the Slack user ids a payload names.

    The id and nothing else. A Slack display name is self-service,
    collides freely and changes without notice, so it is never carried
    as identity; ``canonical_entity_id`` is left unset because deciding
    that a Slack id and a GitHub handle are one person is the identity
    engine's job, under a policy that refuses a mere resemblance.
    """
    refs: list[OrganizationalActorRef] = []
    seen: set[str] = set()
    for candidate in user_ids:
        actor_id = str(candidate or "").strip()
        if not actor_id or actor_id in seen:
            continue
        seen.add(actor_id)
        refs.append(OrganizationalActorRef(actor_id=actor_id))
    return refs


def _metadata(
    *,
    workspace_id: str,
    channel_id: str,
    extra: dict[str, Any] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "external_scope": slack_scope(
            workspace_id=workspace_id, channel_id=channel_id,
        ),
        "workspace_id": workspace_id,
        "channel_id": channel_id,
    }
    if truncated:
        # Said out loud, so nothing downstream reads an excerpt as the
        # whole message and concludes something the full text contradicts.
        metadata["content_truncated"] = True
    metadata.update(extra or {})
    return metadata


def _skip(
    *,
    bucket: list[SkippedPayload],
    workspace_id: str,
    channel_id: str,
    payload_kind: str,
    payload_id: str,
    reason: str,
) -> None:
    bucket.append(SkippedPayload(
        source_system=SLACK,
        external_scope=slack_scope(
            workspace_id=workspace_id, channel_id=channel_id,
        ),
        payload_kind=payload_kind,
        payload_id=payload_id,
        reason=reason,
    ))


def _resolve(
    payload: dict[str, Any],
    *,
    outcome: NormalizationOutcome,
    bindings: ProjectBindings,
    payload_kind: str,
    payload_id: str,
    workspace_id: str,
    channel_id: str,
    scope: str,
) -> tuple[str, str, list[str]] | None:
    """Scope plus declared projects, or ``None`` having reported why.

    Three refusals in one place because all three are the same refusal:
    an unidentifiable channel, a conflicting one and an unbound one each
    mean the payload cannot be filed without a guess.
    """
    try:
        workspace, channel = _scope_parts(
            payload, workspace_id=workspace_id, channel_id=channel_id,
            scope=scope,
        )
    except ScopeConflictError as error:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace_id,
            channel_id=channel_id, payload_kind=payload_kind,
            payload_id=payload_id, reason=str(error),
        )
        return None

    if not workspace or not channel:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace,
            channel_id=channel, payload_kind=payload_kind,
            payload_id=payload_id,
            reason=(
                "a Slack payload needs both a workspace id and a channel "
                "id to be placed; a channel id is only unique within a "
                "workspace and neither is inferable from the message"
            ),
        )
        return None

    projects = bindings.projects_for(
        source_system=SLACK,
        external_scope=slack_scope(
            workspace_id=workspace, channel_id=channel,
        ),
    )
    if projects is None:
        _skip(
            bucket=outcome.unbound, workspace_id=workspace,
            channel_id=channel, payload_kind=payload_kind,
            payload_id=payload_id,
            reason=(
                f"no project is bound to channel "
                f"'{workspace}/{channel}'; the evidence is real but which "
                f"project it belongs to is a declaration, and a channel's "
                f"*name* is never allowed to supply it"
            ),
        )
        return None

    return workspace, channel, projects


def _declined_for(
    message: dict[str, Any],
) -> str:
    """The reason this message is not interpreted, or ``""``."""
    subtype = str(message.get("subtype") or "").strip()
    if subtype in _DECLINED_SUBTYPES:
        return _DECLINED_SUBTYPES[subtype]
    channel_type = str(message.get("channel_type") or "").strip()
    if channel_type in _DECLINED_CHANNEL_TYPES:
        return _DECLINED_CHANNEL_TYPES[channel_type]
    if not str(message.get("user") or "").strip() and str(
        message.get("bot_id") or ""
    ).strip():
        # A bot post with no declared subtype. Same reasoning as
        # ``bot_message``: the fact has a real source elsewhere.
        return _DECLINED_SUBTYPES["bot_message"]
    return ""


# ── Messages and thread replies ──────────────────────────────────────


def normalize_slack_message(
    message: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    workspace_id: str = "",
    channel_id: str = "",
    scope: str = "",
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """One channel message or thread reply, polled or delivered.

    Mapped to ``chat_message`` — discussion, evidence and nothing more.
    The refresh-scope policy makes that evidence-only, which is the
    guardrail that matters: somebody typing "approved" is evidence
    about a decision and never the decision itself.

    A reply is the same class as a top-level message and differs only in
    what it carries: the thread root's ts, its own ts, the channel and
    the author, plus a pointer to the root's source id. That is what
    lets downstream reasoning read a thread as proposal → disagreement →
    clarification → conclusion instead of as four unrelated opinions.

    Reality time is the message's own ``ts``. An already-edited message
    read from history also carries ``edited.ts``, which becomes the
    version in the key so the edit is a new fact rather than a silent
    replacement of the original.
    """
    outcome = NormalizationOutcome()
    ts = str(message.get("ts") or "").strip()
    payload_id = f"message/{ts or '?'}"

    resolved = _resolve(
        message, outcome=outcome, bindings=bindings,
        payload_kind="message", payload_id=payload_id,
        workspace_id=workspace_id, channel_id=channel_id, scope=scope,
    )
    if resolved is None:
        return outcome
    workspace, channel, projects = resolved

    if str(message.get("type") or "message") != "message":
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="message",
            payload_id=payload_id,
            reason=(
                f"'{message.get('type')}' is not a message; an event class "
                f"is never invented to make a payload fit"
            ),
        )
        return outcome

    declined = _declined_for(message)
    if declined:
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="message",
            payload_id=payload_id, reason=declined,
        )
        return outcome

    # The two scope subtypes are a different fact and have their own
    # normalizer; routing them here keeps one entry point per shape.
    subtype = str(message.get("subtype") or "").strip()
    if subtype in {"channel_topic", "channel_purpose"}:
        return normalize_slack_channel_scope(
            message, bindings=bindings, received_at=received_at,
            workspace_id=workspace, channel_id=channel,
            delivery_id=delivery_id,
        )

    if subtype:
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="message",
            payload_id=payload_id,
            reason=(
                f"message subtype '{subtype}' has no declared mapping; "
                f"reporting it is how an unknown Slack shape becomes "
                f"visible instead of becoming a plausible-looking envelope"
            ),
        )
        return outcome

    try:
        observed_at = _require_positive_ts(ts, what="a message")
    except TimestampMissingError as error:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace,
            channel_id=channel, payload_kind="message",
            payload_id=payload_id, reason=str(error),
        )
        return outcome

    text = message.get("text")
    if not str(text or "").strip():
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="message",
            payload_id=payload_id,
            reason=(
                "a message with no text asserts nothing readable; its "
                "attachments and blocks are not evidence this layer "
                "claims to interpret"
            ),
        )
        return outcome

    edited = message.get("edited") or {}
    if not isinstance(edited, dict):
        edited = {}
    edit_marker = str(edited.get("ts") or "").strip()
    thread_ts = str(message.get("thread_ts") or "").strip()
    is_reply = bool(thread_ts and thread_ts != ts)

    content, truncated = _excerpt(text)
    outcome.envelopes.append(make_envelope(
        source_system=SLACK,
        source_type="chat_message",
        source_id=slack_message_source_id(
            workspace_id=workspace, channel_id=channel, ts=ts,
        ),
        event_type=MESSAGE_EVENT_CLASS,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=(
            f"Thread reply in {channel}" if is_reply
            else f"Message in {channel}"
        ),
        content=content,
        actor_refs=_actor_refs(
            message.get("user"), message.get("parent_user_id"),
        ),
        source_pointer=_permalink(
            workspace_id=workspace, channel_id=channel, ts=ts,
            thread_ts=thread_ts,
        ),
        metadata=_metadata(
            workspace_id=workspace, channel_id=channel, truncated=truncated,
            extra={
                "message_ts": ts,
                "thread_ts": thread_ts or None,
                "is_thread_reply": is_reply,
                # The root's source id, so a reply can be joined to the
                # proposal it is answering without re-deriving it.
                "thread_root_source_id": (
                    slack_message_source_id(
                        workspace_id=workspace, channel_id=channel,
                        ts=thread_ts,
                    ) if is_reply else None
                ),
                "slack_user_id": str(message.get("user") or "") or None,
                "parent_user_id": (
                    str(message.get("parent_user_id") or "") or None
                ),
                "channel_type": (
                    str(message.get("channel_type") or "") or None
                ),
                "edited_ts": edit_marker or None,
                "edited_by": str(edited.get("user") or "") or None,
                "message_version": edit_marker or "v0",
            },
        ),
        idempotency_key=slack_message_key(
            workspace_id=workspace, channel_id=channel, ts=ts,
            event_class=MESSAGE_EVENT_CLASS, edit_marker=edit_marker,
            delivery_id=delivery_id,
        ),
    ))
    return outcome


def _permalink(
    *, workspace_id: str, channel_id: str, ts: str, thread_ts: str = "",
) -> str | None:
    """A pointer back to the message, in Slack's documented archive form.

    A pointer, not a resolved URL: the workspace subdomain is not in the
    payload and is not guessed, so the pointer is an opaque reference
    the operator's own tooling can expand. Inventing
    ``https://acme.slack.com/...`` from a team id would produce a link
    that looks authoritative and does not work.
    """
    if not ts:
        return None
    flat = ts.replace(".", "")
    suffix = f"?thread_ts={thread_ts}" if thread_ts and thread_ts != ts else ""
    return f"slack://{workspace_id}/archives/{channel_id}/p{flat}{suffix}"


# ── Edits ────────────────────────────────────────────────────────────


def normalize_slack_message_changed(
    payload: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    workspace_id: str = "",
    channel_id: str = "",
    scope: str = "",
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """A ``message_changed`` event — new versioned evidence.

    The whole point of this function is what it does *not* do. A chat
    client replaces the old text with the new one. A bitemporal,
    append-only journal cannot: "v2 is approved" at 09:00 and "v3 is
    approved" at 09:20 are two facts, and a question asked as of 09:10
    must still see v2. Overwriting would make every historical answer
    silently change the next time somebody fixed a typo.

    So the edit becomes its own envelope: same message identity (the
    source id is ``message.ts``, unchanged), new reality time
    (``message.edited.ts`` — the edit happened when the edit happened),
    new idempotency key (the edit ts is the version). The pre-edit text
    is *not* carried: it is already in the journal as its own fact, and
    copying it here would duplicate evidence and invite a reader to
    treat one utterance as two.
    """
    outcome = NormalizationOutcome()
    message = payload.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    message_ts = str(message.get("ts") or "").strip()
    payload_id = f"message_changed/{message_ts or '?'}"

    resolved = _resolve(
        payload, outcome=outcome, bindings=bindings,
        payload_kind="message_changed", payload_id=payload_id,
        workspace_id=workspace_id, channel_id=channel_id, scope=scope,
    )
    if resolved is None:
        return outcome
    workspace, channel, projects = resolved

    if not message_ts:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace,
            channel_id=channel, payload_kind="message_changed",
            payload_id=payload_id,
            reason=(
                "an edit with no message.ts names no message, so it cannot "
                "be attached to the fact it revises"
            ),
        )
        return outcome

    declined = _declined_for(message)
    if declined:
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="message_changed",
            payload_id=payload_id, reason=declined,
        )
        return outcome

    edited = message.get("edited") or {}
    if not isinstance(edited, dict):
        edited = {}
    # Preference order is the normalizer's claim about which moment the
    # fact belongs to, so it is stated here rather than buried: the
    # provider's own edit timestamp, then the event's time. The original
    # message's ts is never a candidate — that is when the *first*
    # version was said.
    try:
        edit_ts = str(
            edited.get("ts") or payload.get("event_ts") or payload.get("ts")
            or ""
        ).strip()
        observed_at = _require_positive_ts(edit_ts, what="an edit")
    except TimestampMissingError as error:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace,
            channel_id=channel, payload_kind="message_changed",
            payload_id=payload_id, reason=str(error),
        )
        return outcome

    text = message.get("text")
    if not str(text or "").strip():
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="message_changed",
            payload_id=payload_id,
            reason=(
                "an edit to empty text asserts nothing; the message it "
                "revises remains in the journal as it was said"
            ),
        )
        return outcome

    previous = payload.get("previous_message") or {}
    if not isinstance(previous, dict):
        previous = {}
    thread_ts = str(message.get("thread_ts") or "").strip()
    is_reply = bool(thread_ts and thread_ts != message_ts)

    content, truncated = _excerpt(text)
    outcome.envelopes.append(make_envelope(
        source_system=SLACK,
        source_type="chat_message",
        source_id=slack_message_source_id(
            workspace_id=workspace, channel_id=channel, ts=message_ts,
        ),
        event_type=MESSAGE_EVENT_CLASS,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=f"Edited message in {channel}",
        content=content,
        actor_refs=_actor_refs(message.get("user"), edited.get("user")),
        source_pointer=_permalink(
            workspace_id=workspace, channel_id=channel, ts=message_ts,
            thread_ts=thread_ts,
        ),
        metadata=_metadata(
            workspace_id=workspace, channel_id=channel, truncated=truncated,
            extra={
                "message_ts": message_ts,
                "thread_ts": thread_ts or None,
                "is_thread_reply": is_reply,
                "thread_root_source_id": (
                    slack_message_source_id(
                        workspace_id=workspace, channel_id=channel,
                        ts=thread_ts,
                    ) if is_reply else None
                ),
                "slack_user_id": str(message.get("user") or "") or None,
                "edited_ts": edit_ts,
                "edited_by": str(edited.get("user") or "") or None,
                "message_version": edit_ts,
                "is_revision": True,
                # Stated rather than copied: a reader can see there is an
                # earlier version in the journal without this envelope
                # restating its text as if it were said twice.
                "previous_version_ts": (
                    str(previous.get("ts") or "") or None
                ),
                "interpretation": (
                    "a new version of an existing message, not a "
                    "correction of the journalled original"
                ),
            },
        ),
        idempotency_key=slack_message_key(
            workspace_id=workspace, channel_id=channel, ts=message_ts,
            event_class=MESSAGE_EVENT_CLASS, edit_marker=edit_ts,
            delivery_id=delivery_id,
        ),
    ))
    return outcome


# ── Deletions ────────────────────────────────────────────────────────


def normalize_slack_message_deleted(
    payload: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    workspace_id: str = "",
    channel_id: str = "",
    scope: str = "",
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """A ``message_deleted`` event — a source went away.

    Deleting a message in Slack cannot un-say what the organization
    already read and acted on, so nothing durable is erased. What is
    appended is evidence of its own: this message existed, and its
    source is no longer retrievable. History can then answer honestly
    instead of appearing to have never seen it.

    Two deliberate choices. The event class is the evidence-only
    ``chat_message``, because a deletion must not be able to move a
    decision or a readiness assessment — the organizational reading
    lives in the availability metadata, which the durability seam turns
    into a ``SOURCE_AVAILABILITY_CHANGED`` record against the original
    evidence. And no text is carried: Slack puts the deleted message
    under ``previous_message``, and a layer whose job is to respect a
    deletion has no business re-journalling the text as new content. The
    original fact is already in the journal; this envelope says its
    source is gone, not what it said.
    """
    outcome = NormalizationOutcome()
    deleted_ts = str(payload.get("deleted_ts") or "").strip()
    payload_id = f"message_deleted/{deleted_ts or '?'}"

    resolved = _resolve(
        payload, outcome=outcome, bindings=bindings,
        payload_kind="message_deleted", payload_id=payload_id,
        workspace_id=workspace_id, channel_id=channel_id, scope=scope,
    )
    if resolved is None:
        return outcome
    workspace, channel, projects = resolved

    if not deleted_ts:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace,
            channel_id=channel, payload_kind="message_deleted",
            payload_id=payload_id,
            reason=(
                "a deletion with no deleted_ts names no message, so there "
                "is no evidence record whose availability it describes"
            ),
        )
        return outcome

    # When the deletion happened — the outer event time, never the
    # deleted message's ts, which is when the message was *said*.
    try:
        observed_at = _require_positive_ts(
            str(payload.get("ts") or payload.get("event_ts") or "").strip(),
            what="a deletion",
        )
    except TimestampMissingError as error:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace,
            channel_id=channel, payload_kind="message_deleted",
            payload_id=payload_id, reason=str(error),
        )
        return outcome

    previous = payload.get("previous_message") or {}
    if not isinstance(previous, dict):
        previous = {}

    gone_source_id = slack_message_source_id(
        workspace_id=workspace, channel_id=channel, ts=deleted_ts,
    )
    outcome.envelopes.append(make_envelope(
        source_system=SLACK,
        # A distinct source type, so a consumer can tell an utterance
        # from a statement about an utterance without parsing metadata.
        source_type="source_availability",
        source_id=f"{gone_source_id}:{SOURCE_AVAILABILITY_DELETED}",
        event_type=AVAILABILITY_EVENT_CLASS,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=f"Message deleted in {channel}",
        # No content. The text is in the journal already, as the fact it
        # was; restating it here would make a deletion a second copy.
        content=None,
        actor_refs=_actor_refs(previous.get("user")),
        source_pointer=_permalink(
            workspace_id=workspace, channel_id=channel, ts=deleted_ts,
        ),
        metadata=_metadata(
            workspace_id=workspace, channel_id=channel,
            extra={
                "source_availability": SOURCE_AVAILABILITY_DELETED,
                "message_ts": deleted_ts,
                "deleted_message_ts": deleted_ts,
                # The seam: the durability layer appends a
                # SOURCE_AVAILABILITY_CHANGED record against the evidence
                # whose source_id this is, leaving that record untouched.
                "unavailable_source_id": gone_source_id,
                "slack_user_id": str(previous.get("user") or "") or None,
                "availability_reason": (
                    "the message was deleted in Slack; the evidence it "
                    "produced stays in the journal and its source is no "
                    "longer retrievable"
                ),
                "interpretation": (
                    "a source-availability change, not a retraction of "
                    "what the organization already read"
                ),
            },
        ),
        idempotency_key=slack_availability_key(
            workspace_id=workspace, channel_id=channel,
            deleted_ts=deleted_ts,
        ),
    ))
    return outcome


# ── Channel topic and purpose ────────────────────────────────────────


def normalize_slack_channel_scope(
    payload: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    workspace_id: str = "",
    channel_id: str = "",
    scope: str = "",
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """A ``channel_topic`` or ``channel_purpose`` change.

    Supported, unlike most subtypes, because a channel topic is not an
    utterance: it is the one piece of text the whole channel shares and
    that somebody deliberately replaced, and in practice it carries
    declarations — "Release 4.2, code freeze Nov 3", "owner: platform
    team". ``document_updated`` is the only class in the vocabulary that
    means "a shared statement changed", and its refresh scope reaches
    project understanding without touching a KPI or a launch gate,
    which is the right reach for a declared scope change.

    The channel *name* remains off limits. A topic is text someone
    wrote; a name is a label a project is never inferred from.
    """
    outcome = NormalizationOutcome()
    ts = str(payload.get("ts") or "").strip()
    subtype = str(payload.get("subtype") or "").strip()
    payload_id = f"{subtype or 'channel_scope'}/{ts or '?'}"

    resolved = _resolve(
        payload, outcome=outcome, bindings=bindings,
        payload_kind="channel_scope", payload_id=payload_id,
        workspace_id=workspace_id, channel_id=channel_id, scope=scope,
    )
    if resolved is None:
        return outcome
    workspace, channel, projects = resolved

    if subtype not in {"channel_topic", "channel_purpose"}:
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="channel_scope",
            payload_id=payload_id,
            reason=(
                f"'{subtype}' is not a channel scope change; only a topic "
                f"or a purpose is read as one"
            ),
        )
        return outcome

    declared = payload.get("topic") if subtype == "channel_topic" else (
        payload.get("purpose")
    )
    if not str(declared or "").strip():
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="channel_scope",
            payload_id=payload_id,
            reason=(
                f"the {subtype} was cleared rather than restated; an empty "
                f"declaration asserts nothing to reason about"
            ),
        )
        return outcome

    try:
        observed_at = _require_positive_ts(ts, what="a channel scope change")
    except TimestampMissingError as error:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace,
            channel_id=channel, payload_kind="channel_scope",
            payload_id=payload_id, reason=str(error),
        )
        return outcome

    content, truncated = _excerpt(declared)
    outcome.envelopes.append(make_envelope(
        source_system=SLACK,
        source_type="document_updated",
        source_id=f"{workspace}/{channel}:{subtype}:{ts}",
        event_type=CHANNEL_SCOPE_EVENT_CLASS,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=(
            f"Channel topic set in {channel}" if subtype == "channel_topic"
            else f"Channel purpose set in {channel}"
        ),
        content=content,
        actor_refs=_actor_refs(payload.get("user")),
        source_pointer=_permalink(
            workspace_id=workspace, channel_id=channel, ts=ts,
        ),
        metadata=_metadata(
            workspace_id=workspace, channel_id=channel, truncated=truncated,
            extra={
                "message_ts": ts,
                "slack_subtype": subtype,
                "slack_user_id": str(payload.get("user") or "") or None,
                "interpretation": (
                    "a declared statement of what this channel is for, "
                    "not one person's opinion in it"
                ),
            },
        ),
        idempotency_key=slack_message_key(
            workspace_id=workspace, channel_id=channel, ts=ts,
            event_class=CHANNEL_SCOPE_EVENT_CLASS,
            edit_marker=subtype, delivery_id=delivery_id,
        ),
    ))
    return outcome


# ── The Events API wrapper ───────────────────────────────────────────


def normalize_slack_event(
    envelope: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """One Events API ``event_callback``.

    ``event_id`` is the identity this whole path rests on: Slack's own
    id for the delivery, unchanged across a retry, which is what the
    idempotency key has to mean. A callback without one is rejected
    rather than keyed on anything else, because the alternatives — our
    receive time, a hash of the body — would each turn one retried
    delivery into several facts.

    The wrapper's deprecated verification ``token`` field is never read
    and never carried. Authenticity is established by the signature
    seam in ``slack_reader``, before anything here is called.
    """
    outcome = NormalizationOutcome()
    inner = envelope.get("event") or {}
    if not isinstance(inner, dict):
        inner = {}
    event_type = str(inner.get("type") or "").strip()
    event_id = delivery_id or str(envelope.get("event_id") or "").strip()
    workspace = str(envelope.get("team_id") or "").strip()
    channel = str(inner.get("channel") or "").strip()
    payload_id = f"{event_type or '?'}/{event_id or '?'}"

    if not event_id:
        _skip(
            bucket=outcome.rejected, workspace_id=workspace,
            channel_id=channel, payload_kind="event",
            payload_id=payload_id,
            reason=(
                "an Events API callback with no event_id has no stable "
                "identity, so it cannot be deduplicated and must not be "
                "journalled"
            ),
        )
        return outcome

    if str(envelope.get("type") or "event_callback") != "event_callback":
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="event",
            payload_id=payload_id,
            reason=(
                f"'{envelope.get('type')}' is not an event callback; a URL "
                f"verification or a rate-limit notice is not evidence"
            ),
        )
        return outcome

    if event_type in _DECLINED_EVENT_TYPES:
        _skip(
            bucket=outcome.uninterpreted, workspace_id=workspace,
            channel_id=channel, payload_kind="event",
            payload_id=payload_id, reason=_DECLINED_EVENT_TYPES[event_type],
        )
        return outcome

    if event_type == "message":
        subtype = str(inner.get("subtype") or "").strip()
        route = {
            "message_changed": normalize_slack_message_changed,
            "message_deleted": normalize_slack_message_deleted,
            "channel_topic": normalize_slack_channel_scope,
            "channel_purpose": normalize_slack_channel_scope,
        }.get(subtype, normalize_slack_message)
        return route(
            inner, bindings=bindings, received_at=received_at,
            workspace_id=workspace, channel_id=channel,
            delivery_id=event_id,
        )

    _skip(
        bucket=outcome.uninterpreted, workspace_id=workspace,
        channel_id=channel, payload_kind="event", payload_id=payload_id,
        reason=(
            f"Slack event type '{event_type}' has no declared mapping; an "
            f"event class is never invented to make a payload fit"
        ),
    )
    return outcome


# ── Batches and the registry entry point ─────────────────────────────


def normalize_slack_payloads(
    *,
    messages: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    bindings: ProjectBindings,
    received_at: datetime,
    workspace_id: str = "",
    channel_id: str = "",
) -> NormalizationOutcome:
    """Normalize a mixed batch and report everything that was skipped.

    ``messages`` are polled ``conversations.history`` or
    ``conversations.replies`` items, which carry no channel of their
    own; ``events`` are Events API callbacks, which carry both ids.
    """
    outcome = NormalizationOutcome()
    for message in messages or []:
        subtype = str(message.get("subtype") or "").strip()
        route = {
            "message_changed": normalize_slack_message_changed,
            "message_deleted": normalize_slack_message_deleted,
        }.get(subtype, normalize_slack_message)
        outcome = outcome.merge(route(
            message, bindings=bindings, received_at=received_at,
            workspace_id=workspace_id, channel_id=channel_id,
        ))
    for event in events or []:
        outcome = outcome.merge(normalize_slack_event(
            event, bindings=bindings, received_at=received_at,
        ))

    logger.info(
        "normalized source payloads into evidence",
        extra={
            "source_system": SLACK,
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
    """Slack's uniform entry point for the provider registry.

    The same signature every provider exposes, so the CLI and the
    cross-provider tests can treat providers interchangeably instead of
    carrying a conditional per provider. ``scope`` is whatever the
    provider calls the container a payload lives in — here
    ``"{workspace_id}/{channel_id}"``, the two opaque ids, never a
    channel name.

    ``event_name`` is accepted for signature uniformity and used only to
    report a webhook whose declared event name is not one this
    normalizer maps. Slack's own ``event.type`` is authoritative, and
    reading a header in preference to the payload would let a
    mislabelled delivery choose its own interpretation.

    An unknown kind is reported rather than guessed at, for the same
    reason an unmapped event class is: a dispatcher that fell back to
    "probably a message" would invent a fact.
    """
    if payload_kind == "message":
        return normalize_slack_message(
            payload, bindings=bindings, received_at=received_at,
            scope=scope, delivery_id=delivery_id,
        )
    if payload_kind == "message_changed":
        return normalize_slack_message_changed(
            payload, bindings=bindings, received_at=received_at,
            scope=scope, delivery_id=delivery_id,
        )
    if payload_kind == "message_deleted":
        return normalize_slack_message_deleted(
            payload, bindings=bindings, received_at=received_at,
            scope=scope, delivery_id=delivery_id,
        )
    if payload_kind == "channel_scope":
        return normalize_slack_channel_scope(
            payload, bindings=bindings, received_at=received_at,
            scope=scope, delivery_id=delivery_id,
        )
    if payload_kind == "event":
        return normalize_slack_event(
            payload, bindings=bindings, received_at=received_at,
        )
    if payload_kind == "webhook":
        outcome = NormalizationOutcome()
        if not delivery_id:
            _skip(
                bucket=outcome.rejected, workspace_id="", channel_id="",
                payload_kind="webhook", payload_id=event_name or "?",
                reason=(
                    "a delivery with no id cannot be deduplicated; Slack's "
                    "event_id is the key and is not optional"
                ),
            )
            return outcome
        return normalize_slack_event(
            payload, bindings=bindings, received_at=received_at,
            delivery_id=delivery_id,
        )

    outcome = NormalizationOutcome()
    outcome.rejected.append(SkippedPayload(
        source_system=SLACK,
        # The scope is still worth reporting even when the kind is not:
        # whoever reads the report needs to know which channel it was.
        external_scope=scope,
        payload_kind=payload_kind,
        payload_id="?",
        reason=(
            f"'{payload_kind}' is not a Slack payload kind this normalizer "
            f"handles; a dispatcher that guessed would invent a fact"
        ),
    ))
    return outcome
