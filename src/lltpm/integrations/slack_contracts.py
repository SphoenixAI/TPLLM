"""What the Slack normalizers actually read.

The same consumer-driven contract the GitHub side keeps, declared the
same way and for the same reason: Slack adding a field must not fail
the build, and Slack renaming ``thread_ts`` must. Recording the whole
payload shape and diffing it would invert both of those.

Declared here rather than inferred from the source, so the dependency
is a statement a reviewer can check against the normalizer and a future
change has to update deliberately. ``tests/test_slack_contracts.py``
walks the normalizer with an AST and fails when a field is read without
being declared.

Field paths use the notation ``contracts.resolve_path`` understands:
``message.edited.ts`` for nesting, ``messages[].ts`` for a field on each
element of a list.

Regenerating the shipped bundle, since there is no ``lltpm
integrations record`` path for Slack (there is nothing to record)::

    from lltpm.integrations.contracts import (
        build_contract_bundle, contract_bundle_path,
        serialize_contract_bundle,
    )
    from lltpm.integrations.slack_contracts import (
        SLACK_BUNDLE_NOTE, SLACK_CONTRACT_PATHS,
    )
    from tests import _slack_fixtures as fx

    bundle = build_contract_bundle(
        source_system="slack", sources=fx.contract_sources(),
        paths_by_kind=SLACK_CONTRACT_PATHS, note=SLACK_BUNDLE_NOTE,
    )
    contract_bundle_path("slack").write_text(
        serialize_contract_bundle(bundle)
    )

``test_slack_contracts.py`` asserts the shipped file is exactly what
that produces, so a fixture change committed without its contract fails
the build rather than leaving the installed contract describing
payloads nobody has.

A note on what these contracts are evidence *of*. Every Slack fixture in
this repository has provenance ``official_documentation`` — a payload
reproduced from Slack's own published examples, because slack.com is
unreachable from the environment the integration was built in and
labelling a reconstruction ``recorded`` would claim a live check that
never happened. So these contracts say "this is the documented shape",
which is worth more than a hand-written mock and strictly less than a
recording, and ``lltpm integrations verify`` reports them accordingly.
"""

from __future__ import annotations

#: One message object as ``conversations.history`` and
#: ``conversations.replies`` return it, and as the Events API nests it
#: under ``event``. ``channel`` is present in the Events API shape and
#: absent from a polled history item, which is why the normalizer takes
#: the channel as an argument as well as reading it.
SLACK_MESSAGE_FIELDS: tuple[str, ...] = (
    "type",
    "subtype",
    "user",
    "bot_id",
    "username",
    "text",
    "ts",
    "thread_ts",
    "parent_user_id",
    "channel",
    "channel_type",
    "team",
    "edited.ts",
    "edited.user",
)

#: A ``message_changed`` event. Two message objects, not one: the
#: post-edit text under ``message`` and the pre-edit text under
#: ``previous_message``. The edit's own time lives in
#: ``message.edited.ts`` and is the reality time of the edit, while
#: ``message.ts`` stays the identity of the message being edited.
SLACK_MESSAGE_CHANGED_FIELDS: tuple[str, ...] = (
    "type",
    "subtype",
    "hidden",
    "channel",
    "ts",
    "event_ts",
    "message.type",
    "message.user",
    "message.text",
    "message.ts",
    "message.thread_ts",
    "message.edited.ts",
    "message.edited.user",
    "previous_message.text",
    "previous_message.ts",
    "previous_message.user",
)

#: A ``message_deleted`` event. ``deleted_ts`` identifies the message
#: that went away; the outer ``ts``/``event_ts`` is when the deletion
#: happened. Confusing the two would date the deletion to the original
#: message, which is the one thing this event is about not doing.
SLACK_MESSAGE_DELETED_FIELDS: tuple[str, ...] = (
    "type",
    "subtype",
    "hidden",
    "channel",
    "ts",
    "event_ts",
    "deleted_ts",
    "previous_message.ts",
    "previous_message.user",
)

#: ``channel_topic`` and ``channel_purpose`` share one contract because
#: they are one organizational shape — someone changed the declared
#: scope of a channel — and differ only in which of the two fields
#: carries the new text. Each is therefore optional, and the contract
#: records that honestly rather than splitting one fact into two kinds.
SLACK_CHANNEL_SCOPE_FIELDS: tuple[str, ...] = (
    "type",
    "subtype",
    "channel",
    "ts",
    "user",
    "text",
    "topic",
    "purpose",
)

#: The Events API ``event_callback`` wrapper. ``event_id`` is the field
#: the whole idempotency story rests on: it is Slack's own identity for
#: one delivery and is stable across a retry, which is exactly what a
#: key has to mean. The deprecated verification ``token`` field is
#: deliberately *not* read and not declared — a credential has no
#: business reaching a normalizer.
SLACK_EVENT_FIELDS: tuple[str, ...] = (
    "type",
    "event_id",
    "event_time",
    "team_id",
    "api_app_id",
    "event.type",
    "event.subtype",
    "event.channel",
    "event.channel_type",
    "event.user",
    "event.text",
    "event.ts",
    "event.thread_ts",
    "event.event_ts",
)

#: The ``conversations.history`` response envelope, read by the reader
#: rather than by a normalizer. It is a contract all the same: Slack
#: signals failure with HTTP 200 and ``ok: false``, and it paginates
#: through ``response_metadata.next_cursor``. A reader that stopped
#: finding either would report success on an error or silently truncate
#: a channel, so both are pinned.
SLACK_HISTORY_FIELDS: tuple[str, ...] = (
    "ok",
    "has_more",
    "messages[].type",
    "messages[].ts",
    "response_metadata.next_cursor",
)

#: Every contract, by the payload kind the normalizer dispatches on.
#: ``webhook`` is absent on purpose: it is the ``event`` shape arriving
#: by a different route, not a different shape.
SLACK_CONTRACT_PATHS: dict[str, tuple[str, ...]] = {
    "channel_scope": SLACK_CHANNEL_SCOPE_FIELDS,
    "conversations_history": SLACK_HISTORY_FIELDS,
    "event": SLACK_EVENT_FIELDS,
    "message": SLACK_MESSAGE_FIELDS,
    "message_changed": SLACK_MESSAGE_CHANGED_FIELDS,
    "message_deleted": SLACK_MESSAGE_DELETED_FIELDS,
}

#: The note the shipped bundle carries, kept beside the paths it
#: describes so the generator and the test that checks the generator
#: agree on it by construction.
SLACK_BUNDLE_NOTE = (
    "The fields the Slack normalizers read, with the types each one was "
    "observed to take. Derived from the payloads under tests/fixtures/slack, "
    "every one of which has provenance 'official_documentation': a shape "
    "reproduced from Slack's own published API examples, with obviously "
    "synthetic workspace, channel and user ids. It is not a recording of a "
    "live response, so it is not evidence that api.slack.com still matches, "
    "and `lltpm integrations verify` reports it as unverifiable rather than "
    "passing."
)
