"""Shared access to the Jira fixtures.

Not a test module. The normalizer tests, the reader tests, the contract
tests and the journal-path test all read the same files, and all four
need to know what those files are worth — so the provenance index is
loaded here once rather than parsed in four places with four different
ideas of what ``official_documentation`` means.

**What these fixtures are.** Payloads following the shapes Atlassian
publishes for Jira Cloud REST v3, with synthetic values. Not recordings.
``api.atlassian.com``, ``developer.atlassian.com`` and every
``*.atlassian.net`` host are unreachable from this environment, so no
live response could be captured, and labelling one ``recorded`` would
claim a check that never happened. ``official_documentation`` sits
between a recording and a hand-written mock in ``PROVENANCE_RANK``, and
that is exactly where their evidential weight sits: the provider
published the shape, and documentation drifts.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "jira"

#: The synthetic site and project every fixture uses.
SITE = "acme.atlassian.net"
PROJECT_KEY = "ABC"
PROJECT = "checkout"

#: The only provenance the Jira fixtures may claim. Narrower than the
#: shared vocabulary on purpose: there is no network from which a
#: recording could be taken, so a file here claiming ``recorded`` would
#: be a mislabel rather than a new capability.
ALLOWED_PROVENANCE = frozenset({"official_documentation"})

#: Issue keys, by what each sample exists to exercise.
IN_PROGRESS_ISSUE = "ABC-42"
DONE_ISSUE = "ABC-43"
MINIMAL_ISSUE = "ABC-7"

#: Changelog entry ids, by what each one exercises.
STATUS_TRANSITION = "10300"
PRIORITY_AND_DUEDATE = "10301"
ASSIGNEE_CHANGE = "10302"
LINK_CHANGE = "10303"
WORKLOG_CHANGE = "10304"
SPRINT_CHANGE = "10305"
RESOLUTION_CHANGE = "10306"

#: Issue link ids, by what each one exercises.
BLOCKED_BY_OPEN_LINK = "10200"
RELATES_LINK = "10201"
BLOCKS_OUTWARD_LINK = "10202"
BLOCKED_BY_DONE_LINK = "10203"
DUPLICATE_LINK = "10204"

#: Comment ids.
PLAIN_COMMENT = "10100"
EDITED_COMMENT = "10101"
RESTRICTED_COMMENT = "10102"


def index() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "index.json").read_text())


def load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def fixture_names() -> list[str]:
    return sorted(index()["fixtures"])


def provenance(name: str) -> str:
    return str(index()["fixtures"][name]["provenance"])


def bindings():
    """Bindings that bind the fixture site and project to one project."""
    from lltpm.integrations.jira_normalizer import jira_project_bindings

    return jira_project_bindings(
        mapping={(SITE, PROJECT_KEY): [PROJECT]},
    )


def issue(key: str) -> dict[str, Any]:
    for item in load("issues"):
        if item.get("key") == key:
            return item
    raise AssertionError(f"no issue {key} in the fixtures")


def changelog_entry(entry_id: str) -> dict[str, Any]:
    for item in load("changelog_entries"):
        if item.get("id") == entry_id:
            return item
    raise AssertionError(f"no changelog entry {entry_id} in the fixtures")


def comment(comment_id: str) -> dict[str, Any]:
    for item in load("comments"):
        if item.get("id") == comment_id:
            return item
    raise AssertionError(f"no comment {comment_id} in the fixtures")


def issue_link(link_id: str) -> dict[str, Any]:
    for item in load("issue_links"):
        if item.get("id") == link_id:
            return item
    raise AssertionError(f"no issue link {link_id} in the fixtures")


def webhook(event: str) -> dict[str, Any]:
    for item in load("webhooks"):
        if item.get("webhookEvent") == event:
            return item
    raise AssertionError(f"no {event} webhook in the fixtures")


def search_page(*, first: bool) -> dict[str, Any]:
    pages = load("search_pages")
    for page in pages:
        if bool(page.get("nextPageToken")) == first:
            return page
    raise AssertionError("the search fixtures do not cover both pages")


def contract_sources() -> list:
    """Every fixture file as a contract source, with its provenance.

    The bundle shipped in the package is derived from exactly this, and
    a test asserts the two agree — so a fixture change that is not
    accompanied by a regenerated contract fails the build rather than
    leaving the installed contract describing payloads nobody has.
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


#: The note the shipped bundle carries. Kept here so the generator and
#: the test that checks the generator agree on it by construction.
BUNDLE_NOTE = (
    "The fields the Jira Cloud REST v3 normalizers read, with the types "
    "each one was observed to take. Derived from the payloads under "
    "tests/fixtures/jira, every one of which has provenance "
    "'official_documentation': it follows the shape Atlassian publishes "
    "for the endpoint, with synthetic values, and none of it was recorded "
    "from a live site. That makes this contract evidence that the shape "
    "is the documented one and not evidence that the live API still "
    "matches it, so `lltpm integrations verify` reports it as "
    "unverifiable rather than passing."
)
