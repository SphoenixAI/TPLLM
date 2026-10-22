"""What the Jira normalizers actually read.

The consumer-driven half of the contract, in the same notation
``contracts.py`` defines: ``fields.status.name`` for nesting,
``items[].field`` for a field on each element of a list. The machinery —
``extract_contract``, ``check_contract``, ``build_contract_bundle`` —
lives there and is not duplicated; this module is only the declaration
of what Jira must keep providing.

Declared rather than inferred, for the reason the GitHub list gives: a
reviewer can check this against the normalizer, and a future change has
to update it deliberately. Atlassian adding a field must not fail the
build; Atlassian renaming ``fields.updated`` must.

Two things on this list are worth saying out loud.

``fields.worklog`` is read, and is read in order to refuse it. The
normalizer reports a worklog as declined rather than ignoring it, so the
path belongs in the contract: if Atlassian renamed it, the refusal would
silently stop happening and nobody would learn that the field had
started arriving under a new name.

``changelog.histories``, ``fields.comment.comments`` and
``fields.issuelinks`` are container paths. The normalizer walks them to
find the entries it then maps under their own payload kinds, so what the
issue contract pins is that the containers are still lists in the places
they were. The entries' own fields are pinned by the ``changelog``,
``comment`` and ``issue_link`` contracts.
"""

from __future__ import annotations

#: One issue, as returned by the issue endpoint or inside a search page.
#:
#: ``id`` before ``key`` deliberately: the numeric id is the identity
#: this integration keys on, because an issue key changes when a project
#: is renamed or an issue is moved between projects.
JIRA_ISSUE_FIELDS: tuple[str, ...] = (
    "id",
    "key",
    "self",
    "fields.summary",
    "fields.description",
    "fields.created",
    "fields.updated",
    "fields.duedate",
    "fields.project.key",
    "fields.status.id",
    "fields.status.name",
    "fields.status.statusCategory.key",
    "fields.priority.name",
    "fields.resolution.name",
    "fields.issuetype.name",
    "fields.assignee.accountId",
    "fields.assignee.displayName",
    "fields.reporter.accountId",
    "fields.creator.accountId",
    "fields.issuelinks",
    "fields.comment.comments",
    "fields.worklog",
    "changelog.histories",
)

#: One changelog history entry: the only honest source of a transition.
#:
#: ``created`` is the single most important path in this module. It is
#: the transition's reality time, and a renamed or retyped ``created``
#: would move weeks of history onto the day we happened to poll.
JIRA_CHANGELOG_FIELDS: tuple[str, ...] = (
    "id",
    "created",
    "author.accountId",
    "author.displayName",
    "items",
    "items[].field",
    "items[].fieldtype",
    "items[].from",
    "items[].fromString",
    "items[].to",
    "items[].toString",
)

#: One comment. ``body`` is an Atlassian Document Format object in v3
#: and the contract records it as such, so a reversion to a plain string
#: would show up as a type change rather than as prose quietly turning
#: into a JSON dump inside evidence.
JIRA_COMMENT_FIELDS: tuple[str, ...] = (
    "id",
    "created",
    "updated",
    "body",
    "author.accountId",
    "author.displayName",
    "updateAuthor.accountId",
    "visibility",
)

#: One ``fields.issuelinks`` entry.
#:
#: ``type.name`` is what decides whether a link is a dependency at all,
#: and ``inwardIssue.fields.status.statusCategory.key`` is what decides
#: whether that dependency still blocks. Both are in the contract
#: because the mapping refuses to guess when either is missing, and a
#: rename would turn every blocking link into an "unknown status" report.
JIRA_ISSUE_LINK_FIELDS: tuple[str, ...] = (
    "id",
    "type.name",
    "type.inward",
    "type.outward",
    "inwardIssue.id",
    "inwardIssue.key",
    "inwardIssue.fields.status.name",
    "inwardIssue.fields.status.statusCategory.key",
    "outwardIssue.id",
    "outwardIssue.key",
)

#: One webhook delivery body. ``timestamp`` is deliberately absent: the
#: normalizer does not read it, because a delivery timestamp is never an
#: idempotency key and never a reality time.
JIRA_WEBHOOK_FIELDS: tuple[str, ...] = (
    "webhookEvent",
    "issue.id",
    "issue.key",
    "issue.self",
    "issue.fields.updated",
    "issue.fields.project.key",
    "issue.fields.status.statusCategory.key",
    "changelog.id",
    "changelog.created",
    "changelog.items[].field",
    "comment.id",
    "comment.updated",
)

#: One page of a JQL search. Short, and all three paths matter: the
#: reader's pagination loop ends on ``nextPageToken`` and ``isLast``, so
#: a change to either would make it read one page and call that the
#: whole backlog.
JIRA_SEARCH_FIELDS: tuple[str, ...] = (
    "issues",
    "nextPageToken",
    "isLast",
)

JIRA_CONTRACT_PATHS: dict[str, tuple[str, ...]] = {
    "changelog": JIRA_CHANGELOG_FIELDS,
    "comment": JIRA_COMMENT_FIELDS,
    "issue": JIRA_ISSUE_FIELDS,
    "issue_link": JIRA_ISSUE_LINK_FIELDS,
    "search": JIRA_SEARCH_FIELDS,
    "webhook": JIRA_WEBHOOK_FIELDS,
}
