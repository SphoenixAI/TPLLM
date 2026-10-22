"""Shared access to the Slack fixtures.

Not a test module. The normalizer tests, the contract tests and the
runtime-path test all read the same files, and all three need to know
what those files *are* — so the provenance index is loaded here once
rather than parsed in three places with three different ideas of what
``official_documentation`` means.

That value is the whole reason this module reads the way it does.
slack.com, api.slack.com and www.slack.com are unreachable from the
environment this integration was built in, so no live Slack response
could be captured. Every fixture here is therefore a shape reproduced
from Slack's own published documentation examples, and
``recorded_names()`` returns nothing on purpose. A fixture labelled
``recorded`` would claim a verification that never happened, which is
the single thing the provenance vocabulary exists to prevent.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "slack"

#: The synthetic workspace and channel every fixture uses, and the
#: project they are declared to belong to in the tests.
WORKSPACE = "T0ABCDEF"
CHANNEL = "C0ABCDEF"
PROJECT = "atlas"
SCOPE = f"{WORKSPACE}/{CHANNEL}"

#: The synthetic ids the fixtures name, so a test can assert that a
#: fixture names nobody real by checking against this closed set.
SYNTHETIC_IDS = frozenset({
    "T0ABCDEF", "C0ABCDEF", "A0ABCDEF", "B0ABCDEF",
    "U0ABCDEF", "U0ABCDE1", "U0ABCDE2", "U0ABCDE9",
    "Ev0ABCDEF1", "Ev0ABCDEF2", "Ev0ABCDEF3", "Ev0ABCDEF4",
})

#: What a fixture file may claim to be. ``recorded`` is the only value
#: that would make a file evidence about the live API, and no Slack
#: fixture claims it.
PROVENANCE_VALUES = frozenset({
    "recorded", "official_documentation", "derived", "hand_written",
})


def index() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "index.json").read_text())


def load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def fixture_names() -> list[str]:
    return sorted(index()["fixtures"])


def provenance(name: str) -> str:
    return str(index()["fixtures"][name]["provenance"])


def recorded_names() -> list[str]:
    """Empty, and that is the honest answer. Kept as a function rather
    than a constant so the reason lives beside the fact."""
    return [n for n in fixture_names() if provenance(n) == "recorded"]


def documented_names() -> list[str]:
    return [
        n for n in fixture_names()
        if provenance(n) == "official_documentation"
    ]


def bindings():
    """Bindings that declare the fixture channel to be one project."""
    from lltpm.integrations.slack_normalizer import slack_bindings

    return slack_bindings(mapping={(WORKSPACE, CHANNEL): [PROJECT]})


def unbound_bindings():
    """A declared binding for a *different* channel in the same
    workspace, so an unbound-channel test cannot pass merely because
    nothing at all is declared."""
    from lltpm.integrations.slack_normalizer import slack_bindings

    return slack_bindings(mapping={(WORKSPACE, "C0OTHER1"): ["payments"]})


# ── Named fixtures, by what each one exists to exercise ──────────────


def thread_root() -> dict[str, Any]:
    for message in load("messages"):
        if not message.get("parent_user_id"):
            return message
    raise AssertionError("no thread root in the fixtures")


def thread_reply() -> dict[str, Any]:
    for message in load("messages"):
        if message.get("parent_user_id"):
            return message
    raise AssertionError("no thread reply in the fixtures")


def declined(subtype: str) -> dict[str, Any]:
    for message in load("declined_messages"):
        if message.get("subtype") == subtype:
            return message
    raise AssertionError(f"no declined message with subtype {subtype}")


def message_changed() -> dict[str, Any]:
    return load("message_changed")[0]


def message_deleted() -> dict[str, Any]:
    return load("message_deleted")[0]


def channel_scope(subtype: str) -> dict[str, Any]:
    for payload in load("channel_scope"):
        if payload.get("subtype") == subtype:
            return payload
    raise AssertionError(f"no channel scope change with subtype {subtype}")


def event(inner_type: str) -> dict[str, Any]:
    for callback in load("events"):
        if (callback.get("event") or {}).get("type") == inner_type:
            return callback
    raise AssertionError(f"no event callback wrapping {inner_type}")


def history_response() -> dict[str, Any]:
    return load("conversations_history")[0]


def contract_sources() -> list:
    """Every fixture file as a contract source, with its provenance.

    The bundle shipped in the package is derived from exactly this, and
    a test asserts the two agree — so a fixture change that is not
    committed alongside its contract fails the build rather than leaving
    the installed contract describing payloads nobody has.
    """
    from lltpm.integrations.contracts import ContractSource

    entries = index()["fixtures"]
    return [
        ContractSource(
            payload_kind=entries[name]["payload_kind"],
            payloads=load(name),
            provenance=entries[name]["provenance"],
            recorded_at=entries[name]["recorded_at"],
        )
        for name in fixture_names()
    ]


def slack_ids_in(payload: Any) -> set[str]:
    """Every Slack-shaped id a payload mentions, anywhere in it.

    Collected by shape rather than by key, because Slack ids turn up in
    half a dozen fields and inside message text as ``<@U0ABCDEF>``
    mentions — and a check that only looked at ``user`` would pass a
    fixture that names somebody in its prose.
    """
    import re

    pattern = re.compile(r"\b(?:Ev|[TCDGUWBAEF])0[A-Z0-9]{6,}\b")
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            found.update(pattern.findall(node))

    walk(payload)
    return found
