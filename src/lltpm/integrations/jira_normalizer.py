"""Jira Cloud REST v3 → evidence: the ticket-side anti-corruption layer.

The sibling of ``integrations.normalizers`` for Jira. The five rules
stated in that module's docstring hold here unchanged and are not
restated; this docstring records only what is specific to Jira, because
Jira's model differs from GitHub's in four ways that each invite a
specific false statement.

**Ticket state is evidence, not organizational truth.** This is the
one that matters most. An issue reaching ``Done`` means a ticket was
moved to a column. It does not mean the feature works, that the launch
is ready, or that anything was validated — those are claims the
intelligence layer makes from many sources, and a project manager who
has watched a board knows exactly how often "Done" and "done" diverge.
So every completion maps to ``ticket_completed``, whose refresh scope
is deliberately ``EVIDENCE_ONLY | WORKSTREAM``: it can move what the
workstream looks like and it can never, on its own, move a launch
readiness assessment. Nothing in this module may emit a class whose
scope reaches ``LAUNCH_READINESS``, and a test asserts it against the
real scope policy rather than against a list of strings.

**The changelog is the history; the snapshot is only the present.** A
polled issue carries its *current* status, priority and due date and no
record of how it got there. Inferring "it must have moved to In
Progress at some point" from a snapshot would invent a transition with
a fabricated time. So transitions come from changelog entries only, and
a transition's reality time is the entry's own ``created`` — a status
change recorded six weeks ago occurred six weeks ago, whatever today's
poll says.

**An issue key is a label, not an identity.** ``ABC-42`` becomes
``XYZ-91`` when a project is renamed or an issue is moved between
projects, while the numeric ``id`` never changes. Keying identity or
idempotency on the key would make one issue look like two the day
someone reorganizes the board, so the immutable ``id`` is the identity
and the key is carried as metadata.

The tenant half of that identity is the site host. Jira's opaque
``cloudId`` is the more durable tenant identifier, but it appears in no
issue payload — it comes from a separate ``/_edge/tenant_info`` read —
so it is not invented here. The site host is what a payload actually
states, through the ``self`` URL, and a caller that knows the cloud id
can pass it as the site.

**Rich text is a document, not a string.** v3 returns descriptions and
comment bodies in Atlassian Document Format — nested JSON, not text.
``adf_to_text`` flattens it defensively, and a field that arrives as a
plain string (the v2 shape, or a v3 field that was never ADF) is
handled rather than crashed on.

What this module declines to interpret, and why, because declining
visibly is the behaviour and not a gap:

- **Worklogs and time tracking.** ``timespent``, ``worklogTimeSpent``
  and a worklog entry record how many hours a named person booked
  against a ticket. That is individual activity measurement, which this
  system does not do at any layer; see the no-worker-scoring invariant.
  Refusing the field here is cheaper than removing it downstream.
- **Sprint membership.** Jira Software keeps it in a custom field whose
  id differs per site and whose value is a mix of ids and rendered
  strings. Reading it would be a guess about a configuration.
- **Custom fields in general**, for the same reason. A custom field's
  meaning lives in a site's configuration, not in its payload.
- **``Link`` changelog items.** Jira records a link change as prose —
  ``"This issue blocks ABC-43"``. Parsing English to recover a
  dependency is exactly the kind of guess this layer exists to refuse;
  dependency structure is read from ``fields.issuelinks`` instead,
  where the relationship is typed data.
- **``Rank`` changes and attachments.** Neither asserts an
  organizational fact.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from lltpm.coordination.models import OrganizationalActorRef
from lltpm.integrations.envelopes import make_envelope
from lltpm.integrations.normalizers import (
    CONTENT_EXCERPT_LIMIT,
    NormalizationOutcome,
    ProjectBindings,
    SkippedPayload,
    SourceBinding,
    TimestampMissingError,
    to_naive_utc,
)

logger = logging.getLogger(__name__)

JIRA = "jira"

#: Jira's own status *categories*, which are a fixed three-value
#: vocabulary across every site: ``new``, ``indeterminate``, ``done``.
#: Status *names* are per-workflow and unbounded, so the category is the
#: authority whenever it is present in the payload.
DONE_STATUS_CATEGORY_KEY = "done"

#: Status names read as completion when no category is available — a
#: changelog item carries the status name and id but not its category.
#: A declared default rather than an inference: the same reasoning as
#: ``DEFAULT_BLOCKING_LABELS`` on the GitHub side. A deployment whose
#: workflow ends in "Shipped" passes its own set, and a status outside
#: the set is ordinary movement rather than a guessed completion.
DEFAULT_DONE_STATUS_NAMES = frozenset({
    "done", "closed", "resolved", "complete", "completed",
})

#: The link types whose semantics genuinely are a dependency. Jira ships
#: four link types by default — Blocks, Duplicate, Relates, Cloners —
#: and only one of them says anything about whether work can proceed.
BLOCKING_LINK_TYPE_NAMES = frozenset({"blocks"})

#: Why each of the other default link types is not a dependency. Kept as
#: data so the refusal is reviewable beside the mapping it refuses.
_NON_DEPENDENCY_LINK_REASONS: dict[str, str] = {
    "relates": (
        "'relates to' states that two tickets are worth reading together. "
        "It says nothing about whether either can proceed, and treating "
        "it as a dependency would manufacture congestion that does not "
        "exist"
    ),
    "duplicate": (
        "a duplicate is one piece of work recorded twice, not one piece "
        "of work waiting on another"
    ),
    "cloners": (
        "a clone is a copy made for convenience; the copy does not wait "
        "on the original"
    ),
}

#: Changelog fields that are ordinary ticket movement, with the reason
#: each one is movement and not something stronger.
_MOVEMENT_FIELDS: dict[str, str] = {
    "assignee": (
        "ownership of the ticket moved. Who holds a piece of work is "
        "organizational mechanics; no count, rate or backlog size is "
        "derived for the person named"
    ),
    "duedate": (
        "a due date moved. A date on a ticket is a plan someone recorded, "
        "not a commitment the organization made, so it is ticket movement "
        "rather than a decision"
    ),
    "priority": (
        "a priority change re-ranks the work. It is not a blocker — "
        "nothing is prevented from proceeding — and not a decision"
    ),
    "summary": "the ticket's title was edited",
    "description": "the ticket's description was edited",
    "labels": (
        "labels moved. Jira labels are free text per site, so no blocker "
        "vocabulary is inferred from them here"
    ),
    "issuetype": "the ticket was reclassified",
    "components": "the ticket moved between components",
    "fixVersions": (
        "the fix version moved. A version is a release container; whether "
        "a release is ready is a judgement the readiness engines make "
        "from evidence, never a field on a ticket"
    ),
    "reporter": "the ticket's reporter was corrected",
    "project": (
        "the ticket was moved between projects. The issue id is unchanged, "
        "which is why identity keys on it rather than on the key"
    ),
}

#: Changelog fields deliberately left uninterpreted, each with its
#: reason. Reported, never dropped: "we declined to read this" and "there
#: was nothing to read" must stay distinguishable.
_IGNORED_FIELDS: dict[str, str] = {
    "link": (
        "Jira records a link change as prose ('This issue blocks ABC-43'). "
        "Parsing English to recover a dependency would be a guess; "
        "dependency structure is read from fields.issuelinks, where the "
        "relationship is typed"
    ),
    "timespent": (
        "time spent records how many hours a named person booked against "
        "a ticket. This system measures dependencies, queues and capacity "
        "and never an individual's hours"
    ),
    "timeestimate": (
        "a remaining-estimate change is time-tracking bookkeeping, and "
        "the estimate it adjusts is not an organizational fact"
    ),
    "timeoriginalestimate": (
        "an original-estimate change is time-tracking bookkeeping"
    ),
    "worklogid": (
        "a worklog records an individual's logged hours; see timespent"
    ),
    "worklogtimespent": (
        "a worklog records an individual's logged hours; see timespent"
    ),
    "rank": (
        "a backlog rank change is a view ordering, not a fact about the "
        "work"
    ),
    "attachment": "attaching a file asserts nothing organizational",
    "sprint": (
        "sprint membership lives in a Jira Software custom field whose id "
        "differs per site and whose value mixes ids with rendered names; "
        "reading it would be a guess about a configuration"
    ),
    "epic link": (
        "the epic link is a custom field whose id differs per site; the "
        "parent relationship is read from the issue's own fields instead"
    ),
    "status category": (
        "the category follows the status, so the status transition is the "
        "fact and this would duplicate it"
    ),
}


# ── Bindings ─────────────────────────────────────────────────────────


def jira_scope(*, site: str, project_key: str) -> str:
    """The binding scope for one Jira project: ``site/PROJECTKEY``.

    The site is part of the scope because a project key is only unique
    within a site: two Atlassian tenants both having a ``PLATFORM``
    project is ordinary, and binding on the bare key would file one
    tenant's evidence under the other's project. ``ProjectBindings``
    then works unchanged, matching exactly and case-insensitively.
    """
    return f"{(site or '').strip().lower()}/{(project_key or '').strip()}"


def jira_project_bindings(
    *, mapping: dict[tuple[str, str], list[str]],
) -> ProjectBindings:
    """Typed bindings from ``(site, project key) -> project ids``.

    A typed constructor rather than the string-keyed
    ``ProjectBindings.from_mapping``, because the scope string has
    internal structure and a caller that assembled it by hand could get
    the separator wrong and silently bind nothing. Nothing fuzzy is
    accepted: there is no matching on a project's display name, since
    "Platform" and "Platform (legacy)" are not the same project and no
    normalizer can know which one was meant.
    """
    return ProjectBindings(bindings=[
        SourceBinding(
            source_system=JIRA,
            external_scope=jira_scope(site=site, project_key=key),
            project_ids=list(projects),
        )
        for (site, key), projects in sorted(mapping.items())
    ])


def site_from_self_url(url: Any) -> str:
    """The Jira site host out of a ``self`` URL.

    Every v3 resource carries one, and it is the only place a payload
    states which tenant it came from — there is no site or cloud id
    field on an issue. A caller that knows the site passes it
    explicitly; this is the fallback, and it reads the host rather than
    guessing from a project key.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    host = urlsplit(text).netloc
    return host.lower()


# ── Atlassian Document Format ────────────────────────────────────────


def adf_to_text(value: Any) -> str:
    """Plain text out of an ADF document, defensively.

    v3 returns ``description`` and comment bodies as a nested document
    rather than a string, so ``str(value)`` would put a JSON dump into
    evidence content. The walk collects text nodes in document order and
    separates block nodes with a blank line.

    Three deliberate behaviours:

    - a plain string passes through, so a v2-shaped payload or a field
      that was never ADF does not crash the normalizer;
    - an unknown node type is descended into rather than refused — ADF
      grows new node types and losing a paragraph because it sat inside
      one would be worse than the alternative;
    - a mention node contributes nothing. Its ``attrs`` carry an
      account id and a rendered display name, and a display name is not
      identity here.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()

    _BLOCK_TYPES = {
        "paragraph", "heading", "blockquote", "codeBlock", "panel",
        "listItem", "tableRow", "rule", "mediaSingle",
    }
    blocks: list[str] = []

    def walk(node: Any, parts: list[str]) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, parts)
            return
        if not isinstance(node, dict):
            return
        node_type = str(node.get("type") or "")
        if node_type == "text":
            text = node.get("text")
            if isinstance(text, str):
                parts.append(text)
            return
        if node_type == "hardBreak":
            parts.append("\n")
            return
        if node_type in ("mention", "emoji", "inlineCard", "status"):
            # Rendered chrome, not prose a person wrote.
            return
        if node_type in _BLOCK_TYPES:
            own: list[str] = []
            walk(node.get("content"), own)
            rendered = "".join(own).strip()
            if rendered:
                blocks.append(rendered)
            return
        walk(node.get("content"), parts)

    loose: list[str] = []
    walk(value.get("content") if isinstance(value, dict) else value, loose)
    trailing = "".join(loose).strip()
    if trailing:
        blocks.append(trailing)
    return "\n\n".join(blocks).strip()


def _excerpt(*parts: Any) -> tuple[str | None, bool]:
    """Joined prose as an excerpt. Returns ``(text, truncated)``.

    Each part is flattened through ``adf_to_text`` first, so a caller
    never has to know whether a field arrived as a document or a string.
    """
    rendered = [adf_to_text(part) for part in parts]
    text = "\n\n".join(part for part in rendered if part)
    if not text:
        return None, False
    if len(text) <= CONTENT_EXCERPT_LIMIT:
        return text, False
    return text[:CONTENT_EXCERPT_LIMIT], True


# ── Identity ─────────────────────────────────────────────────────────


def _actor_refs(*users: Any) -> list[OrganizationalActorRef]:
    """Actor references for the Jira users a payload names.

    The ``accountId`` is the reference, because it is the only stable
    identifier Atlassian exposes — ``displayName`` is user-editable,
    repeats across people, and is explicitly not identity.
    ``canonical_entity_id`` stays unset: deciding that an ``accountId``
    and a Slack member id are one person is the identity layer's call,
    under a policy that refuses a mere name resemblance.
    """
    refs: list[OrganizationalActorRef] = []
    seen: set[str] = set()
    for user in users:
        if not isinstance(user, dict):
            continue
        account_id = str(user.get("accountId") or "").strip()
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        refs.append(OrganizationalActorRef(
            actor_id=account_id,
            display_name=str(user.get("displayName") or account_id),
        ))
    return refs


def _actor_ref_for_account(account_id: Any) -> list[OrganizationalActorRef]:
    """An actor reference built from a bare ``accountId``.

    A changelog assignee item carries the account id in ``to`` and the
    display name in ``toString``. Only the first is identity, so the
    second is deliberately not used here even though it would read
    better in a report.
    """
    text = str(account_id or "").strip()
    if not text:
        return []
    return [OrganizationalActorRef(actor_id=text, display_name=text)]


class _IssueContext:
    """The identity and scope of one issue, resolved once.

    Built before any mapping runs, because every Jira payload kind needs
    the same five facts and three of them can fail: an issue with no id,
    an issue whose project key is absent, and a project nothing is bound
    to are three different outcomes with three different reports.
    """

    __slots__ = (
        "issue_id", "issue_key", "project_key", "site", "version",
        "status_name", "status_id", "status_category", "self_url",
    )

    def __init__(self, issue: dict[str, Any], *, site: str = ""):
        fields = issue.get("fields") if isinstance(issue, dict) else None
        fields = fields if isinstance(fields, dict) else {}
        project = fields.get("project")
        project = project if isinstance(project, dict) else {}
        status = fields.get("status")
        status = status if isinstance(status, dict) else {}
        category = status.get("statusCategory")
        category = category if isinstance(category, dict) else {}

        self.self_url = str(issue.get("self") or "")
        self.issue_id = str(issue.get("id") or "").strip()
        self.issue_key = str(issue.get("key") or "").strip()
        self.project_key = str(project.get("key") or "").strip()
        if not self.project_key and self.issue_key and "-" in self.issue_key:
            # The key's prefix *is* the project key in Jira, and a
            # search response can be asked for a narrow field set that
            # omits the project object. This is a decomposition of a
            # provider-supplied identifier, not a guess about which
            # project someone meant.
            self.project_key = self.issue_key.rsplit("-", 1)[0]
        self.site = (site or site_from_self_url(self.self_url)).strip().lower()
        self.version = str(fields.get("updated") or "").strip()
        self.status_name = str(status.get("name") or "").strip()
        self.status_id = str(status.get("id") or "").strip()
        self.status_category = str(category.get("key") or "").strip().lower()

    @property
    def scope(self) -> str:
        return jira_scope(site=self.site, project_key=self.project_key)

    @property
    def label(self) -> str:
        return self.issue_key or self.issue_id or "?"

    def metadata(self, **extra: Any) -> dict[str, Any]:
        """The identity every Jira envelope carries.

        Site, issue id, issue key, project key and the ``updated``
        version are all preserved: the id is identity, the key is what a
        person will search for, and the version is what makes a polled
        observation deduplicable.
        """
        base: dict[str, Any] = {
            "jira_site": self.site,
            "jira_issue_id": self.issue_id,
            "jira_issue_key": self.issue_key,
            "jira_project_key": self.project_key,
            "jira_issue_version": self.version,
        }
        base.update({k: v for k, v in extra.items() if v is not None})
        return base

    def is_done_status(
        self, *, status_name: str = "", status_id: str = "",
        done_status_names: frozenset[str] = DEFAULT_DONE_STATUS_NAMES,
    ) -> bool:
        """Whether a status means the ticket left open work.

        Jira's ``statusCategory`` is preferred wherever it is available,
        because it is a fixed three-value vocabulary — ``new``,
        ``indeterminate``, ``done`` — across every site, while a status
        *name* is per-workflow prose that a site can set to anything.

        A changelog item carries the destination status' name and id but
        not its category, so the category can only be used when the
        transition landed on the status the snapshot currently shows. In
        every other case the declared name set decides, and a status
        outside it is ordinary movement rather than a guessed completion.
        """
        if not status_name and not status_id:
            if self.status_category:
                return self.status_category == DONE_STATUS_CATEGORY_KEY
            return self.status_name.strip().lower() in done_status_names
        if status_id and self.status_category and status_id == self.status_id:
            return self.status_category == DONE_STATUS_CATEGORY_KEY
        return status_name.strip().lower() in done_status_names


def _issue_context(issue: Any, *, site: str = "") -> _IssueContext:
    return _IssueContext(
        issue if isinstance(issue, dict) else {}, site=site,
    )


def _resolve(
    context: _IssueContext,
    *,
    bindings: ProjectBindings,
    payload_kind: str,
    payload_id: str,
    outcome: NormalizationOutcome,
) -> list[str] | None:
    """Identity and binding checks, or ``None`` with a report filed.

    One helper for all five payload kinds, because the three failure
    reports have to read the same way whichever kind hit them.
    """
    if not context.issue_id:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind=payload_kind, payload_id=payload_id,
            reason=(
                "an issue needs its immutable numeric id to be identified; "
                "the issue key is not a substitute because a key changes "
                "when a project is renamed or an issue is moved"
            ),
        ))
        return None
    if not context.project_key or not context.site:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind=payload_kind, payload_id=payload_id,
            reason=(
                "a Jira payload needs both a site and a project key to be "
                "placed; a project key alone is only unique within one "
                "Atlassian tenant"
            ),
        ))
        return None

    projects = bindings.projects_for(
        source_system=JIRA, external_scope=context.scope,
    )
    if projects is None:
        outcome.unbound.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind=payload_kind, payload_id=payload_id,
            reason=(
                f"no project is bound to Jira project '{context.scope}'; the "
                f"evidence is real but which LLTPM project it belongs to is "
                f"a declaration, not a guess, and no match is made on the "
                f"project's display name"
            ),
        ))
        return None
    return projects


# ── Issue snapshot ───────────────────────────────────────────────────


def normalize_jira_issue(
    issue: dict[str, Any],
    *,
    bindings: ProjectBindings,
    received_at: datetime,
    site: str = "",
    include_embedded: bool = True,
    done_status_names: frozenset[str] = DEFAULT_DONE_STATUS_NAMES,
) -> NormalizationOutcome:
    """One issue as polled or delivered: its present state.

    Two mappings and no more:

    - a ticket in a ``done`` status category → ``ticket_completed``, at
      ``fields.updated``;
    - anything else → ``ticket_updated``, at ``fields.updated`` falling
      back to ``fields.created``.

    ``ticket_completed`` is as far as a Jira status is allowed to carry.
    A column named Done is evidence that someone moved a card; feature
    completeness, launch readiness and validation are conclusions drawn
    from many sources, and the refresh-scope policy keeps this class
    away from all of them. Mapping Done onto a readiness-bearing class
    would let one drag-and-drop on a board declare a launch ready.

    The snapshot is the present tense only. No transition is inferred
    from it — a ticket now In Progress tells us nothing about when it
    moved, and ``normalize_jira_changelog_entry`` is where history comes
    from.

    ``include_embedded`` walks the changelog, comments and issue links
    carried inside an expanded issue. They are part of the payload, and
    ignoring them would throw away the only history a single ``GET``
    returns.
    """
    outcome = NormalizationOutcome()
    context = _issue_context(issue, site=site)
    payload_id = f"issue/{context.label}"

    projects = _resolve(
        context, bindings=bindings, payload_kind="issue",
        payload_id=payload_id, outcome=outcome,
    )
    if projects is None:
        return outcome

    fields = issue.get("fields")
    fields = fields if isinstance(fields, dict) else {}

    completed = context.is_done_status(done_status_names=done_status_names)
    event_class = "ticket_completed" if completed else "ticket_updated"
    # A ticket whose updated time still equals its created time has not
    # been touched since it was raised. Said in the metadata rather than
    # mapped to a class of its own: there is no "ticket_created" in the
    # recognized vocabulary, and inventing one to capture a distinction
    # the refresh policy does not act on would be vocabulary growth for
    # its own sake.
    created_only = bool(
        fields.get("created")
        and fields.get("created") == fields.get("updated")
    )

    try:
        observed_at = to_naive_utc(
            fields.get("updated") or fields.get("created")
        )
    except TimestampMissingError as error:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="issue", payload_id=payload_id,
            reason=(
                f"{error} A Jira issue always carries fields.updated; a "
                f"payload without one is not stamped with the poll time, "
                f"because that would record today's reading as today's "
                f"change."
            ),
        ))
        return outcome

    content, truncated = _excerpt(
        fields.get("summary"), fields.get("description"),
    )
    assignee = fields.get("assignee")
    priority = fields.get("priority")
    resolution = fields.get("resolution")
    issue_type = fields.get("issuetype")

    outcome.envelopes.append(make_envelope(
        source_system=JIRA,
        source_type="ticket",
        source_id=f"{context.site}:{context.issue_id}",
        event_type=event_class,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=adf_to_text(fields.get("summary")) or None,
        content=content,
        actor_refs=_actor_refs(
            assignee, fields.get("reporter"), fields.get("creator"),
        ),
        source_pointer=_browse_url(context),
        metadata=context.metadata(
            content_truncated=True if truncated else None,
            jira_status=context.status_name or None,
            jira_status_category=context.status_category or None,
            jira_priority=_named(priority),
            jira_resolution=_named(resolution),
            jira_issue_type=_named(issue_type),
            jira_due_date=fields.get("duedate"),
            jira_created_at=fields.get("created"),
            jira_first_observation=True if created_only else None,
            jira_assignee_account_id=(
                str(assignee.get("accountId"))
                if isinstance(assignee, dict) and assignee.get("accountId")
                else None
            ),
            # Said out loud beside the mapping: this is the guardrail the
            # module exists to hold, and a reader of the evidence should
            # see it rather than have to know it.
            interpretation=(
                "a ticket in a done status is evidence that a card moved, "
                "not that the work is validated or the launch is ready"
                if completed else
                "the ticket as raised, not edited since"
                if created_only else
                "the ticket's present state as of this observation"
            ),
        ),
        idempotency_key=jira_issue_key(
            issue_id=context.issue_id, updated=context.version,
            event_class=event_class,
        ),
    ))

    if include_embedded:
        outcome = outcome.merge(_normalize_embedded(
            issue=issue, context=context, bindings=bindings,
            received_at=received_at, done_status_names=done_status_names,
        ))
    return outcome


def _named(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("name"):
        return str(value["name"])
    return None


def _browse_url(context: _IssueContext) -> str | None:
    """A human pointer back to the ticket.

    Built from the site and the key rather than taken from ``self``: the
    API URL is not a page a person can open, and the browse URL is the
    one stable human address Jira has.
    """
    if context.site and context.issue_key:
        return f"https://{context.site}/browse/{context.issue_key}"
    return None


def jira_issue_key(
    *, issue_id: str, updated: str, event_class: str,
    delivery_id: str | None = None,
) -> str:
    """The stable identity of one issue observation.

    A polled issue has no provider event id, so identity is the resource
    plus its version: the immutable numeric ``id`` and ``fields.updated``.
    Re-reading an unchanged issue produces the same key and appends
    nothing; a real edit bumps ``updated`` and is a new fact. Our clock
    appears nowhere, and neither does the issue key — a project rename
    would otherwise turn one ticket's history into two.

    The event class is part of the key because one issue legitimately
    produces movement and later a completion, and those are two
    organizational facts rather than two versions of one.
    """
    if delivery_id:
        return f"jira:delivery:{delivery_id}:{event_class}"
    version = (updated or "").strip() or "unversioned"
    return f"jira:issue:{issue_id}:{version}:{event_class}"


# ── Changelog: where history actually comes from ──────────────────────


def normalize_jira_changelog_entry(
    entry: dict[str, Any],
    *,
    issue: dict[str, Any],
    bindings: ProjectBindings,
    received_at: datetime,
    site: str = "",
    done_status_names: frozenset[str] = DEFAULT_DONE_STATUS_NAMES,
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """One changelog history entry — one real transition, or several.

    This is the important half of the Jira integration. A snapshot says
    a ticket is In Progress; only the changelog says it moved to In
    Progress on the 3rd, from To Do, and that is the fact a temporal
    stack can reason about.

    Reality time is the entry's own ``created``. A transition recorded
    six weeks ago occurred six weeks ago however late we read it, and
    stamping it with the poll time would move six weeks of history into
    today and corrupt every scenario rooted in it.

    One entry can carry several ``items`` — a status change and a
    priority change saved together — so one entry can yield several
    envelopes. Each item maps on its own merits:

    - ``status`` → ``ticket_completed`` when the destination status is a
      completion, otherwise ``ticket_updated``;
    - ``resolution`` set → ``ticket_completed``; cleared → ``ticket_updated``
      (a reopening);
    - the movement fields in :data:`_MOVEMENT_FIELDS` → ``ticket_updated``;
    - everything else → reported, with the reason.
    """
    outcome = NormalizationOutcome()
    context = _issue_context(issue, site=site)
    history_id = str(entry.get("id") or "").strip()
    payload_id = f"changelog/{context.label}/{history_id or '?'}"

    projects = _resolve(
        context, bindings=bindings, payload_kind="changelog",
        payload_id=payload_id, outcome=outcome,
    )
    if projects is None:
        return outcome

    if not history_id:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="changelog", payload_id=payload_id,
            reason=(
                "a changelog entry with no id has no stable identity, so it "
                "cannot be deduplicated and must not be journalled"
            ),
        ))
        return outcome

    try:
        observed_at = to_naive_utc(entry.get("created"))
    except TimestampMissingError as error:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="changelog", payload_id=payload_id,
            reason=(
                f"{error} A changelog entry's created time is the only "
                f"honest reality time for the transition it records; the "
                f"issue's updated time is a later, different moment."
            ),
        ))
        return outcome

    author = entry.get("author")
    items = entry.get("items")
    items = items if isinstance(items, list) else []
    if not items:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="changelog", payload_id=payload_id,
            reason="a changelog entry with no items records no transition",
        ))
        return outcome

    for item in items:
        if not isinstance(item, dict):
            continue
        outcome = outcome.merge(_normalize_changelog_item(
            item,
            context=context, projects=projects, author=author,
            observed_at=observed_at, received_at=received_at,
            history_id=history_id, payload_id=payload_id,
            done_status_names=done_status_names, delivery_id=delivery_id,
        ))
    return outcome


def _normalize_changelog_item(
    item: dict[str, Any],
    *,
    context: _IssueContext,
    projects: list[str],
    author: Any,
    observed_at: datetime,
    received_at: datetime,
    history_id: str,
    payload_id: str,
    done_status_names: frozenset[str],
    delivery_id: str | None,
) -> NormalizationOutcome:
    """One ``items[]`` element: one field that actually changed."""
    outcome = NormalizationOutcome()
    field = str(item.get("field") or "").strip()
    field_key = field.lower()
    field_type = str(item.get("fieldtype") or "").strip().lower()
    from_string = item.get("fromString")
    to_string = item.get("toString")

    if field_key in _IGNORED_FIELDS:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="changelog", payload_id=f"{payload_id}/{field}",
            reason=_IGNORED_FIELDS[field_key],
        ))
        return outcome

    if field_key == "status":
        completed = context.is_done_status(
            status_name=str(to_string or ""),
            status_id=str(item.get("to") or ""),
            done_status_names=done_status_names,
        )
        event_class = "ticket_completed" if completed else "ticket_updated"
        rationale = (
            "the ticket reached a completion status. That is evidence a "
            "card moved — ticket_completed refreshes the workstream and "
            "deliberately cannot reach launch readiness"
            if completed else
            "the ticket moved between workflow statuses"
        )
    elif field_key == "resolution":
        if str(to_string or "").strip() or str(item.get("to") or "").strip():
            event_class = "ticket_completed"
            rationale = (
                "a resolution was set, so the ticket is no longer open "
                "work. Which resolution it received is carried as metadata "
                "and deliberately not read as success: 'Won't Do' closes a "
                "ticket without anything being delivered"
            )
        else:
            event_class = "ticket_updated"
            rationale = (
                "the resolution was cleared, which is a reopening: the "
                "ticket is open work again"
            )
    elif field_key in _MOVEMENT_FIELDS:
        event_class = "ticket_updated"
        rationale = _MOVEMENT_FIELDS[field_key]
    elif field_type == "custom":
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="changelog", payload_id=f"{payload_id}/{field}",
            reason=(
                f"custom field '{field}' has no declared mapping. A custom "
                f"field's meaning lives in a site's configuration and not "
                f"in its payload, so reading it would be a guess"
            ),
        ))
        return outcome
    else:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="changelog", payload_id=f"{payload_id}/{field}",
            reason=(
                f"changelog field '{field}' has no declared mapping; an "
                f"event class is never invented to make a payload fit"
            ),
        ))
        return outcome

    # The assignee item is the one place a Jira payload puts an account
    # id in `to` and a display name in `toString`. Only the first is
    # identity, so the actor reference is built from `to` and the author
    # of the change is carried alongside it.
    actor_refs = _actor_refs(author)
    if field_key == "assignee":
        for ref in _actor_ref_for_account(item.get("to")):
            if ref.actor_id not in {r.actor_id for r in actor_refs}:
                actor_refs.append(ref)

    transition = _transition_text(field, from_string, to_string)
    outcome.envelopes.append(make_envelope(
        source_system=JIRA,
        source_type="ticket_transition",
        source_id=f"{context.site}:{context.issue_id}:change:{history_id}",
        event_type=event_class,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=f"{context.label}: {transition}",
        content=transition,
        actor_refs=actor_refs,
        source_pointer=_browse_url(context),
        metadata=context.metadata(
            jira_changelog_id=history_id,
            jira_changed_field=field,
            jira_changed_field_type=field_type or None,
            jira_from=_short(from_string),
            jira_to=_short(to_string),
            interpretation=rationale,
            # A transition's reality time is its own, which is the
            # property the whole changelog path exists for.
            reality_time_basis="changelog_entry_created",
        ),
        idempotency_key=jira_changelog_key(
            issue_id=context.issue_id, history_id=history_id,
            field=field_key, event_class=event_class,
            delivery_id=delivery_id,
        ),
    ))
    return outcome


def _short(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:200]


def _transition_text(field: str, before: Any, after: Any) -> str:
    """``status: To Do → In Progress``, as prose for a reader.

    The rendered strings are used here and nowhere else: this is the
    human-readable content of the evidence, never its identity. An
    assignee transition renders display names, which is correct for
    prose and would be wrong as an actor reference.
    """
    start = str(before or "").strip() or "(none)"
    end = str(after or "").strip() or "(none)"
    return f"{field}: {start} → {end}"


def jira_changelog_key(
    *, issue_id: str, history_id: str, field: str, event_class: str,
    delivery_id: str | None = None,
) -> str:
    """The stable identity of one transition.

    A changelog entry is immutable once written, so the issue id plus the
    entry's own id is the provider's strongest identity for it — no
    version is needed and no timestamp is involved.

    The field is part of the key because one entry can carry several
    items: a priority change and a due-date change saved together both
    map to ``ticket_updated``, and a key without the field would collapse
    two real changes into one.
    """
    if delivery_id:
        return f"jira:delivery:{delivery_id}:{field}:{event_class}"
    return (
        f"jira:changelog:{issue_id}:{history_id}:{field}:{event_class}"
    )


# ── Comments ─────────────────────────────────────────────────────────


def normalize_jira_comment(
    comment: dict[str, Any],
    *,
    issue: dict[str, Any],
    bindings: ProjectBindings,
    received_at: datetime,
    site: str = "",
    delivery_id: str | None = None,
) -> NormalizationOutcome:
    """One comment: communication evidence, and nothing more.

    A Jira comment maps to ``chat_message``, whose refresh scope is
    evidence-only. That is the point. "Approved, ship it" in a comment
    is a sentence someone typed, not an approval the organization
    recorded; whether it expresses a decision, an approval or a status
    is a judgement the coordination and decision engines make from the
    content, with their own evidence thresholds. A normalizer that read
    it as ``decision_conclusive`` would let a sentence finalize a
    decision, which is precisely the shape of error the interpretation
    guardrails exist to prevent.

    What is preserved is what a downstream reader needs: the author's
    external identity, the issue context, the comment id, and the
    comment's own reality time.
    """
    outcome = NormalizationOutcome()
    context = _issue_context(issue, site=site)
    comment_id = str(comment.get("id") or "").strip()
    payload_id = f"comment/{context.label}/{comment_id or '?'}"

    projects = _resolve(
        context, bindings=bindings, payload_kind="comment",
        payload_id=payload_id, outcome=outcome,
    )
    if projects is None:
        return outcome

    if not comment_id:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="comment", payload_id=payload_id,
            reason=(
                "a comment with no id has no stable identity and cannot be "
                "deduplicated"
            ),
        ))
        return outcome

    created = str(comment.get("created") or "").strip()
    updated = str(comment.get("updated") or "").strip()
    edited = bool(updated and updated != created)
    try:
        # An edited comment is a second fact about the same comment, at
        # the time of the edit. Its key carries the version, so the
        # original and the edit are both kept rather than one silently
        # replacing the other.
        observed_at = to_naive_utc(updated if edited else (created or updated))
    except TimestampMissingError as error:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="comment", payload_id=payload_id,
            reason=str(error),
        ))
        return outcome

    content, truncated = _excerpt(comment.get("body"))
    visibility = comment.get("visibility")
    outcome.envelopes.append(make_envelope(
        source_system=JIRA,
        source_type="comment",
        source_id=f"{context.site}:{context.issue_id}:comment:{comment_id}",
        event_type="chat_message",
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=f"Comment on {context.label}",
        content=content,
        actor_refs=_actor_refs(
            comment.get("author"), comment.get("updateAuthor"),
        ),
        source_pointer=(
            f"{_browse_url(context)}?focusedCommentId={comment_id}"
            if _browse_url(context) else None
        ),
        metadata=context.metadata(
            content_truncated=True if truncated else None,
            jira_comment_id=comment_id,
            jira_comment_version=updated or created or None,
            jira_comment_edited=edited or None,
            jira_comment_restricted=(
                True if isinstance(visibility, dict) and visibility else None
            ),
            interpretation=(
                "communication evidence. What the content expresses — a "
                "decision, an approval, a status — is left to the engines "
                "that weigh evidence, not asserted here"
            ),
        ),
        idempotency_key=jira_comment_key(
            comment_id=comment_id, version=updated or created,
            delivery_id=delivery_id,
        ),
    ))
    return outcome


def jira_comment_key(
    *, comment_id: str, version: str, delivery_id: str | None = None,
) -> str:
    """Comment id plus version.

    The comment id alone is not enough: Jira comments are editable, and
    an edit is a new fact about the same comment rather than a
    redelivery of the old one. ``updated`` is the version Jira itself
    maintains, so re-reading an unedited comment appends nothing while
    an edit arrives as its own observation.
    """
    stamp = (version or "").strip() or "unversioned"
    if delivery_id:
        return f"jira:delivery:{delivery_id}:chat_message"
    return f"jira:comment:{comment_id}:{stamp}:chat_message"


# ── Issue links: dependencies, conservatively ────────────────────────


def normalize_jira_issue_link(
    link: dict[str, Any],
    *,
    issue: dict[str, Any],
    bindings: ProjectBindings,
    received_at: datetime,
    site: str = "",
    done_status_names: frozenset[str] = DEFAULT_DONE_STATUS_NAMES,
) -> NormalizationOutcome:
    """One entry of ``fields.issuelinks``.

    Jira link types are per-site configuration with four shipped by
    default, and only ``Blocks`` says anything about whether work can
    proceed. So:

    - a link whose type is a blocking type, seen from the **blocked**
      side (``inwardIssue`` present, reading "this issue is blocked by
      that one") → ``blocker_opened``, or ``blocker_resolved`` when the
      blocking issue is already in a completion status;
    - the same edge seen from the blocking side (``outwardIssue``) is
      reported rather than emitted, so one dependency does not become
      two facts when both issues are polled;
    - ``relates to``, ``duplicates`` and ``clones`` are reported with
      their reasons. Turning every link into a dependency would invent
      congestion, and congestion is what this stack reports on.

    The provider's relationship type is preserved in metadata either
    way, so a future policy can revisit a link type without re-reading
    Jira.

    A link carries no timestamp of its own. ``fields.updated`` on the
    issue is used as the observation's reality time and the metadata
    says so — it is an upper bound on when the link appeared, not the
    moment it was created, and claiming otherwise would be the kind of
    invented precision this layer refuses. The alternative, stamping it
    ``now``, would be worse.
    """
    outcome = NormalizationOutcome()
    context = _issue_context(issue, site=site)
    link_id = str(link.get("id") or "").strip()
    payload_id = f"issuelink/{context.label}/{link_id or '?'}"

    projects = _resolve(
        context, bindings=bindings, payload_kind="issue_link",
        payload_id=payload_id, outcome=outcome,
    )
    if projects is None:
        return outcome

    if not link_id:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="issue_link", payload_id=payload_id,
            reason="an issue link with no id cannot be deduplicated",
        ))
        return outcome

    link_type = link.get("type")
    link_type = link_type if isinstance(link_type, dict) else {}
    type_name = str(link_type.get("name") or "").strip()
    type_key = type_name.lower()
    inward = link.get("inwardIssue")
    outward = link.get("outwardIssue")

    if type_key not in BLOCKING_LINK_TYPE_NAMES:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="issue_link", payload_id=payload_id,
            reason=_NON_DEPENDENCY_LINK_REASONS.get(type_key, (
                f"link type '{type_name}' is not a declared dependency "
                f"type. Jira link types are per-site configuration, and "
                f"reading an undeclared one as a dependency would invent "
                f"congestion that does not exist"
            )),
        ))
        return outcome

    if not isinstance(inward, dict) or not inward:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="issue_link", payload_id=payload_id,
            reason=(
                f"this is the blocking side of a '{type_name}' link "
                f"({context.label} blocks another issue). The same "
                f"dependency edge is normalized from the blocked issue's "
                f"side, so one edge does not become two organizational "
                f"facts"
            ) if isinstance(outward, dict) and outward else (
                f"a '{type_name}' link with neither an inward nor an "
                f"outward issue names no counterpart"
            ),
        ))
        return outcome

    blocker_fields = inward.get("fields")
    blocker_fields = blocker_fields if isinstance(blocker_fields, dict) else {}
    blocker_status = blocker_fields.get("status")
    blocker_status = (
        blocker_status if isinstance(blocker_status, dict) else {}
    )
    blocker_category = blocker_status.get("statusCategory")
    blocker_category = (
        blocker_category if isinstance(blocker_category, dict) else {}
    )
    category_key = str(blocker_category.get("key") or "").strip().lower()
    status_name = str(blocker_status.get("name") or "").strip()

    if not category_key and not status_name:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="issue_link", payload_id=payload_id,
            reason=(
                f"{context.label} is blocked by "
                f"{inward.get('key') or inward.get('id')}, but the "
                f"blocking issue's status is not in this payload, so "
                f"whether the dependency is still blocking is unknown. "
                f"Reported rather than assumed open: an unresolved "
                f"blocker is a stronger claim than no claim"
            ),
        ))
        return outcome

    resolved = (
        category_key == DONE_STATUS_CATEGORY_KEY if category_key
        else status_name.strip().lower() in done_status_names
    )
    event_class = "blocker_resolved" if resolved else "blocker_opened"

    try:
        fields = issue.get("fields")
        fields = fields if isinstance(fields, dict) else {}
        observed_at = to_naive_utc(
            fields.get("updated") or fields.get("created")
        )
    except TimestampMissingError as error:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="issue_link", payload_id=payload_id,
            reason=(
                f"{error} An issue link carries no timestamp of its own, so "
                f"the issue's updated time is the only honest basis for the "
                f"observation; without it there is nothing to stand on."
            ),
        ))
        return outcome

    blocker_label = str(inward.get("key") or inward.get("id") or "?")
    outcome.envelopes.append(make_envelope(
        source_system=JIRA,
        source_type="ticket_dependency",
        source_id=f"{context.site}:{context.issue_id}:link:{link_id}",
        event_type=event_class,
        observed_at=observed_at,
        received_at=received_at,
        project_ids=projects,
        title=f"{context.label} is blocked by {blocker_label}",
        content=(
            f"{context.label} {link_type.get('inward') or 'is blocked by'} "
            f"{blocker_label}"
        ),
        actor_refs=[],
        source_pointer=_browse_url(context),
        metadata=context.metadata(
            jira_link_id=link_id,
            # The provider's own vocabulary, kept verbatim so a future
            # policy can revisit a link type without re-reading Jira.
            jira_link_type=type_name,
            jira_link_inward_description=link_type.get("inward"),
            jira_link_outward_description=link_type.get("outward"),
            jira_link_direction="inward",
            jira_blocking_issue_key=inward.get("key"),
            jira_blocking_issue_id=inward.get("id"),
            jira_blocking_issue_status=status_name or None,
            jira_blocking_issue_status_category=category_key or None,
            reality_time_basis="issue_updated",
            interpretation=(
                "the blocking issue is in a completion status, so this "
                "dependency no longer blocks"
                if resolved else
                "a declared blocking dependency with the blocker still "
                "open"
            ),
        ),
        idempotency_key=jira_issue_link_key(
            link_id=link_id, event_class=event_class,
        ),
    ))
    return outcome


def jira_issue_link_key(*, link_id: str, event_class: str) -> str:
    """The link's own id plus what it currently asserts.

    Jira assigns a link its own stable id, so re-reading an unchanged
    link appends nothing. The event class is in the key because the
    blocker later resolving is a second, genuine fact about the same
    edge — and because a key without it would make the resolution look
    like a duplicate of the opening.
    """
    return f"jira:issuelink:{link_id}:{event_class}"


# ── Embedded payloads inside an expanded issue ───────────────────────


def _has_worklog_entries(worklog: Any) -> bool:
    """Whether a worklog container actually holds entries.

    The container is present on every expanded issue, so reporting its
    mere presence would file a refusal for every ticket nobody has
    logged time against. The refusal is worth making when there is
    something real being refused.
    """
    if isinstance(worklog, dict):
        return bool(worklog.get("worklogs"))
    return bool(worklog) if isinstance(worklog, list) else False


def _normalize_embedded(
    *,
    issue: dict[str, Any],
    context: _IssueContext,
    bindings: ProjectBindings,
    received_at: datetime,
    done_status_names: frozenset[str],
) -> NormalizationOutcome:
    """Changelog, comments and links carried inside one issue payload.

    ``GET /rest/api/3/issue/{id}?expand=changelog`` returns the history
    under ``changelog.histories``; the changelog endpoint returns the
    same entries under ``values``. Both are read, because which one a
    caller has depends on which endpoint they called and the entries are
    identical.
    """
    outcome = NormalizationOutcome()
    changelog = issue.get("changelog")
    if isinstance(changelog, dict):
        entries = changelog.get("histories")
        if not isinstance(entries, list):
            entries = changelog.get("values")
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                outcome = outcome.merge(normalize_jira_changelog_entry(
                    entry, issue=issue, bindings=bindings,
                    received_at=received_at, site=context.site,
                    done_status_names=done_status_names,
                ))

    fields = issue.get("fields")
    fields = fields if isinstance(fields, dict) else {}

    comment_container = fields.get("comment")
    comments: Any = []
    if isinstance(comment_container, dict):
        comments = comment_container.get("comments")
    elif isinstance(comment_container, list):
        # The documented "Get issue" example shows fields.comment as a
        # bare list while the comment endpoint wraps it in an object.
        # Both are accepted rather than one being assumed.
        comments = comment_container
    for comment in comments if isinstance(comments, list) else []:
        if isinstance(comment, dict):
            outcome = outcome.merge(normalize_jira_comment(
                comment, issue=issue, bindings=bindings,
                received_at=received_at, site=context.site,
            ))

    for link in fields.get("issuelinks") or []:
        if isinstance(link, dict):
            outcome = outcome.merge(normalize_jira_issue_link(
                link, issue=issue, bindings=bindings,
                received_at=received_at, site=context.site,
                done_status_names=done_status_names,
            ))

    if _has_worklog_entries(fields.get("worklog")):
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="worklog", payload_id=f"worklog/{context.label}",
            reason=(
                "a worklog records how many hours a named person booked "
                "against a ticket. This system measures dependency "
                "congestion, queues and capacity, and never an "
                "individual's logged time — so the field is declined here "
                "rather than carried and dropped later"
            ),
        ))
    return outcome


# ── Webhooks ─────────────────────────────────────────────────────────

#: Jira webhook event names this normalizer interprets, and the piece of
#: the payload each one is actually about.
_WEBHOOK_EVENTS: dict[str, str] = {
    "jira:issue_created": "issue",
    "jira:issue_updated": "issue",
    "jira:issue_deleted": "deleted",
    "comment_created": "comment",
    "comment_updated": "comment",
    "comment_deleted": "deleted",
}


def normalize_jira_webhook(
    *,
    event_name: str,
    payload: dict[str, Any],
    bindings: ProjectBindings,
    received_at: datetime,
    delivery_id: str | None = None,
    site: str = "",
    done_status_names: frozenset[str] = DEFAULT_DONE_STATUS_NAMES,
) -> NormalizationOutcome:
    """One Jira webhook delivery.

    ``event_name`` is the body's own ``webhookEvent``. Note what is
    *not* used as the key: the delivery's ``timestamp``. Jira has no
    documented delivery-id header, so when a receiver cannot supply one
    the key falls back to the underlying resource's identity and version
    — the issue id plus ``fields.updated``, the changelog entry's id, the
    comment id plus ``updated``. A timestamp is never the key, whether it
    is our clock or the provider's: the same delivery can legitimately
    arrive carrying a different one, and redelivery is exactly the case
    the key exists for.

    An ``issue_updated`` delivery carries the issue *and* the changelog
    entry that caused it, so both are normalized: the snapshot for the
    present state, the entry for the transition with its own reality
    time.

    A deletion is reported rather than mapped. Jira deleting a ticket is
    not an organizational transition — nothing was completed, resolved
    or decided — and the journal is append-only, so there is nothing to
    unsay.
    """
    outcome = NormalizationOutcome()
    issue = payload.get("issue")
    issue = issue if isinstance(issue, dict) else {}
    context = _issue_context(issue, site=site)
    kind = _WEBHOOK_EVENTS.get(event_name, "")

    if not kind:
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="webhook",
            payload_id=f"{event_name or '?'}/{delivery_id or '?'}",
            reason=(
                f"webhook event '{event_name}' has no declared mapping. "
                f"Jira emits events for sprints, worklogs, versions and "
                f"board configuration; an event class is never invented to "
                f"make one fit"
            ),
        ))
        return outcome

    if kind == "deleted":
        outcome.uninterpreted.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="webhook",
            payload_id=f"{event_name}/{context.label}",
            reason=(
                "a deletion in the source system is not an organizational "
                "transition: nothing was completed, resolved or decided. "
                "The journal is append-only and there is nothing to unsay"
            ),
        ))
        return outcome

    if kind == "comment":
        comment = payload.get("comment")
        if not isinstance(comment, dict) or not comment:
            outcome.rejected.append(SkippedPayload(
                source_system=JIRA, external_scope=context.scope,
                payload_kind="webhook", payload_id=event_name,
                reason=(
                    f"'{event_name}' carried no comment object, so there is "
                    f"nothing to normalize and nothing to guess from"
                ),
            ))
            return outcome
        return normalize_jira_comment(
            comment, issue=issue, bindings=bindings,
            received_at=received_at, site=site, delivery_id=delivery_id,
        )

    if not issue:
        outcome.rejected.append(SkippedPayload(
            source_system=JIRA, external_scope=context.scope,
            payload_kind="webhook", payload_id=event_name,
            reason=f"'{event_name}' carried no issue object",
        ))
        return outcome

    outcome = outcome.merge(normalize_jira_issue(
        issue, bindings=bindings, received_at=received_at, site=site,
        include_embedded=False, done_status_names=done_status_names,
    ))

    changelog = payload.get("changelog")
    if isinstance(changelog, dict) and changelog.get("items"):
        outcome = outcome.merge(normalize_jira_changelog_entry(
            changelog, issue=issue, bindings=bindings,
            received_at=received_at, site=site,
            done_status_names=done_status_names,
        ))
    return outcome


# ── Batches ──────────────────────────────────────────────────────────


def normalize_jira_payloads(
    *,
    issues: list[dict[str, Any]] | None = None,
    comments: list[dict[str, Any]] | None = None,
    bindings: ProjectBindings,
    received_at: datetime,
    site: str = "",
    issue_for_comments: dict[str, Any] | None = None,
    done_status_names: frozenset[str] = DEFAULT_DONE_STATUS_NAMES,
) -> NormalizationOutcome:
    """Normalize a batch and report everything that was skipped."""
    outcome = NormalizationOutcome()
    for issue in issues or []:
        outcome = outcome.merge(normalize_jira_issue(
            issue, bindings=bindings, received_at=received_at, site=site,
            done_status_names=done_status_names,
        ))
    for comment in comments or []:
        outcome = outcome.merge(normalize_jira_comment(
            comment, issue=issue_for_comments or {}, bindings=bindings,
            received_at=received_at, site=site,
        ))

    logger.info(
        "normalized source payloads into evidence",
        extra={
            "source_system": JIRA,
            "external_scope": site,
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
    """Jira's uniform entry point for the provider registry.

    Every provider exposes this signature so the CLI and the
    cross-provider tests can treat providers interchangeably rather than
    carrying a conditional per provider. ``scope`` here is the Jira site
    host — the project key comes from the payload, and the two together
    are the binding scope.

    The ``changelog``, ``comment`` and ``issue_link`` kinds need issue
    context, which Jira's own endpoints return alongside them. A payload
    of one of those kinds is accepted either wrapped —
    ``{"issue": {...}, "changelog": {...}}`` — or with the entry at the
    top level and the issue under ``issue``, because both shapes occur
    depending on which endpoint produced it.

    An unknown kind is reported rather than guessed at, for the same
    reason an unmapped changelog field is: a dispatcher that fell back
    to "probably an issue" would invent a fact.
    """
    site = scope

    if payload_kind == "issue":
        return normalize_jira_issue(
            payload, bindings=bindings, received_at=received_at, site=site,
        )
    if payload_kind == "webhook":
        return normalize_jira_webhook(
            event_name=event_name or str(payload.get("webhookEvent") or ""),
            payload=payload, bindings=bindings, received_at=received_at,
            delivery_id=delivery_id, site=site,
        )
    if payload_kind in ("changelog", "comment", "issue_link"):
        issue = payload.get("issue")
        issue = issue if isinstance(issue, dict) else {}
        entry = payload.get(
            {"changelog": "changelog", "comment": "comment",
             "issue_link": "issue_link"}[payload_kind]
        )
        if not isinstance(entry, dict):
            entry = {k: v for k, v in payload.items() if k != "issue"}
        if payload_kind == "changelog":
            return normalize_jira_changelog_entry(
                entry, issue=issue, bindings=bindings,
                received_at=received_at, site=site, delivery_id=delivery_id,
            )
        if payload_kind == "comment":
            return normalize_jira_comment(
                entry, issue=issue, bindings=bindings,
                received_at=received_at, site=site, delivery_id=delivery_id,
            )
        return normalize_jira_issue_link(
            entry, issue=issue, bindings=bindings, received_at=received_at,
            site=site,
        )

    outcome = NormalizationOutcome()
    outcome.rejected.append(SkippedPayload(
        source_system=JIRA, external_scope=site,
        payload_kind=payload_kind, payload_id="?",
        reason=(
            f"'{payload_kind}' is not a Jira payload kind this normalizer "
            f"handles; a dispatcher that guessed would invent a fact"
        ),
    ))
    return outcome


#: Every event class this module can emit. Asserted in the tests against
#: the runtime's own closed vocabulary, and against the refresh-scope
#: policy — nothing here may reach launch readiness.
JIRA_EMITTED_EVENT_CLASSES = frozenset({
    "blocker_opened",
    "blocker_resolved",
    "chat_message",
    "ticket_completed",
    "ticket_updated",
})
