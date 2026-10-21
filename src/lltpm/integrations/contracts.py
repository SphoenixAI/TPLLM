"""Recorded provider contracts (Commit 37).

Testing an integration against a live third-party API gives you a suite
that needs credentials, needs network, and fails for reasons that have
nothing to do with your code. Testing it against a hand-written mock
gives you a suite that passes forever while the real API moves
underneath it. Fowler's self-initializing fake is the way out: record
the real responses once, replay them offline, and run a separate
occasional suite that checks the recordings still match reality.

This module is the two halves of that.

**Recording.** ``scrub_github_payload`` pseudonymizes a real response so
it can be committed: handles become stable aliases, the repository
becomes a declared alias, avatars and emails are dropped, and opaque
node ids are rewritten deterministically. The *shape* is preserved
exactly, which is the part a contract test is about. Committing real
contributors' handles into a repository that is being open-sourced would
be a gratuitous disclosure, and the project's own stance is that it
models organizational mechanics rather than individuals.

**Verifying.** A contract here is deliberately not "the whole payload
looks like this". It is the set of fields the normalizers actually read,
with the types they are allowed to take. That is the consumer-driven
contract: GitHub adding a field must not fail the build, and GitHub
renaming ``merged_at`` must. Recording the whole payload shape and
diffing it would invert both of those.

Field paths use a small notation: ``user.login`` for nesting, and
``labels[].name`` for a field on each element of a list.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

#: Placeholder for anything dropped during scrubbing. Visible on
#: purpose: a reader of a fixture should see that a value was removed
#: rather than wonder whether the API returns an empty string.
SCRUBBED = "scrubbed"


# ── Field paths ──────────────────────────────────────────────────────


def resolve_path(payload: Any, path: str) -> list[Any]:
    """Every value at ``path``. Empty when the path is absent.

    A list segment fans out, so ``labels[].name`` over three labels
    returns three values — and an empty list returns none, which is
    why absence and "present but empty" are distinguished by the
    caller rather than here.
    """
    nodes: list[Any] = [payload]
    for segment in path.split("."):
        explode = segment.endswith("[]")
        key = segment[:-2] if explode else segment
        collected: list[Any] = []
        for node in nodes:
            if not isinstance(node, dict) or key not in node:
                continue
            value = node[key]
            if explode:
                if isinstance(value, list):
                    collected.extend(value)
            else:
                collected.append(value)
        nodes = collected
        if not nodes:
            return []
    return nodes


def type_name(value: Any) -> str:
    """A stable type label. ``null`` is its own type, because a field
    that is sometimes null is a different contract from one that is
    always a string."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


# ── The contract ─────────────────────────────────────────────────────


class FieldContract(BaseModel):
    """One field a normalizer reads, and what it may contain."""
    path: str
    types: list[str] = Field(default_factory=list)
    always_present: bool = True

    model_config = {"frozen": True}


class PayloadContract(BaseModel):
    """The fields one payload kind must keep providing."""
    source_system: str
    payload_kind: str
    fields: list[FieldContract] = Field(default_factory=list)
    recorded_from_samples: int = 0

    def field_for(self, path: str) -> FieldContract | None:
        for field in self.fields:
            if field.path == path:
                return field
        return None


class ContractBreach(BaseModel):
    """One way a live payload stopped honouring what we depend on."""
    path: str
    kind: str  # "missing" | "type_changed"
    expected: list[str] = Field(default_factory=list)
    actual: str = ""

    @property
    def explanation(self) -> str:
        if self.kind == "missing":
            return (
                f"'{self.path}' is no longer present; a normalizer reads it"
            )
        return (
            f"'{self.path}' is now {self.actual}, not one of "
            f"{sorted(self.expected)}"
        )


def extract_contract(
    *,
    source_system: str,
    payload_kind: str,
    payloads: list[dict[str, Any]],
    paths: tuple[str, ...],
) -> PayloadContract:
    """Derive the contract from recorded samples.

    A field is ``always_present`` only if every sample had it, so one
    pull request without ``merged_at`` is enough to record that the
    field is optional — which it genuinely is. Deriving this rather than
    declaring it keeps the contract honest about what was actually
    observed.
    """
    fields: list[FieldContract] = []
    for path in paths:
        observed: set[str] = set()
        present_in = 0
        for payload in payloads:
            values = resolve_path(payload, path)
            if values:
                present_in += 1
                observed.update(type_name(value) for value in values)
        fields.append(FieldContract(
            path=path,
            types=sorted(observed),
            always_present=bool(payloads) and present_in == len(payloads),
        ))
    return PayloadContract(
        source_system=source_system,
        payload_kind=payload_kind,
        fields=fields,
        recorded_from_samples=len(payloads),
    )


def check_contract(
    *, payload: dict[str, Any], contract: PayloadContract,
) -> list[ContractBreach]:
    """What a live payload no longer provides.

    Only two things count as a breach: a field we read has gone, or it
    now carries a type we do not handle. A new field is not a breach —
    that is the whole point of recording what we consume rather than
    what the provider sends.
    """
    breaches: list[ContractBreach] = []
    for field in contract.fields:
        values = resolve_path(payload, field.path)
        if not values:
            if field.always_present:
                breaches.append(ContractBreach(
                    path=field.path, kind="missing",
                    expected=list(field.types),
                ))
            continue
        for value in values:
            actual = type_name(value)
            if field.types and actual not in field.types:
                breaches.append(ContractBreach(
                    path=field.path, kind="type_changed",
                    expected=list(field.types), actual=actual,
                ))
                break
    return breaches


# ── What the GitHub normalizers actually read ────────────────────────
#
# Kept here rather than inferred, so the dependency is a declaration a
# reviewer can check against the normalizer and a future change has to
# update deliberately.

GITHUB_PULL_REQUEST_FIELDS: tuple[str, ...] = (
    "node_id",
    "number",
    "state",
    "draft",
    "title",
    "body",
    "created_at",
    "updated_at",
    "closed_at",
    "merged_at",
    "html_url",
    "user.login",
    "base.ref",
    "labels[].name",
)

GITHUB_ISSUE_FIELDS: tuple[str, ...] = (
    "node_id",
    "number",
    "state",
    "title",
    "body",
    "created_at",
    "updated_at",
    "closed_at",
    "html_url",
    "user.login",
    "labels[].name",
)

GITHUB_EVENT_FIELDS: tuple[str, ...] = (
    "id",
    "type",
    "created_at",
    "actor.login",
    "repo.name",
    "payload",
)

GITHUB_REVIEW_FIELDS: tuple[str, ...] = (
    "node_id",
    "state",
    "body",
    "submitted_at",
    "html_url",
    "user.login",
)

GITHUB_CONTRACT_PATHS: dict[str, tuple[str, ...]] = {
    "pull_request": GITHUB_PULL_REQUEST_FIELDS,
    "issue": GITHUB_ISSUE_FIELDS,
    "event": GITHUB_EVENT_FIELDS,
    "review": GITHUB_REVIEW_FIELDS,
}


# ── Scrubbing ────────────────────────────────────────────────────────

#: Keys dropped outright. None of them is read by a normalizer, and all
#: of them identify a person or let someone enumerate one.
_DROPPED_KEYS = frozenset({
    "avatar_url",
    "email",
    "gravatar_id",
    "followers_url",
    "following_url",
    "gists_url",
    "organizations_url",
    "received_events_url",
    "starred_url",
    "subscriptions_url",
})

#: Keys whose value is an opaque provider id that identifies a real
#: object. Rewritten deterministically rather than dropped, because the
#: normalizers key on ``node_id`` and a fixture without one would
#: exercise the rejection path instead of the real one.
_PSEUDONYMIZED_ID_KEYS = frozenset({"node_id"})

#: A dict carrying this key is a user-like object, so its numeric id is
#: a real account id and is rewritten too.
_USER_MARKER_KEY = "login"


def _alias_for(value: str, *, prefix: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"{prefix}-{digest[:12]}"


def _numeric_alias(value: int) -> int:
    digest = hashlib.sha256(str(value).encode()).hexdigest()
    return int(digest[:8], 16)


def collect_github_logins(payload: Any) -> set[str]:
    """Every handle a payload names, anywhere in it.

    Collected in a first pass because handles do not only appear under
    ``login``: GitHub embeds them in half a dozen URL fields on the same
    object, and a scrubber that only rewrote the field it recognized
    would leave the name in ``html_url`` — which is how a pseudonymized
    fixture quietly stops being pseudonymous.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            login = node.get(_USER_MARKER_KEY)
            if isinstance(login, str) and login.strip():
                found.add(login.strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


def scrub_github_payload(
    payload: Any,
    *,
    repository_alias: str,
    real_repository: str = "",
) -> Any:
    """A committable copy of a real response.

    Shape-faithful and value-pseudonymous: every key a normalizer reads
    is still there with the same type, while handles, account ids,
    avatars and the repository name no longer name anyone. Aliases are
    derived by hash, so re-recording the same response produces the same
    fixture and a diff shows a real API change rather than churn.

    Two passes, because one is not enough: the first collects every
    handle the payload mentions, the second rewrites them everywhere
    they appear — including inside the URL fields that carry a handle in
    their path.
    """
    owner_alias = repository_alias.split("/")[0]
    real_owner = real_repository.split("/")[0] if real_repository else ""
    logins = collect_github_logins(payload)
    replacements = {
        login: (
            owner_alias if login == real_owner
            else _alias_for(login, prefix="contributor")
        )
        for login in logins
    }
    if real_repository:
        replacements[real_repository] = repository_alias

    def rewrite(text: str) -> str:
        # Longest first, so "owner/repo" is replaced before "owner".
        for needle in sorted(replacements, key=len, reverse=True):
            if needle in text:
                text = text.replace(needle, replacements[needle])
        return text

    def walk(node: Any, *, parent_key: str = "", in_user: bool = False) -> Any:
        if isinstance(node, dict):
            user_like = _USER_MARKER_KEY in node
            scrubbed: dict[str, Any] = {}
            for key, value in node.items():
                if key in _DROPPED_KEYS:
                    continue
                if key in _PSEUDONYMIZED_ID_KEYS and isinstance(value, str):
                    scrubbed[key] = _alias_for(value, prefix="NODE")
                    continue
                if key == "id" and user_like and isinstance(value, int):
                    scrubbed[key] = _numeric_alias(value)
                    continue
                if key == "name" and parent_key == "repo" and isinstance(
                    value, str
                ):
                    # The Events API puts "owner/name" in repo.name while
                    # the REST resource puts the bare name there. Keeping
                    # the distinction matters: a fixture whose shape
                    # differs from the live response is not a recording
                    # of it.
                    scrubbed[key] = (
                        repository_alias if "/" in value
                        else repository_alias.split("/")[-1]
                    )
                    continue
                if key == "full_name" and isinstance(value, str):
                    scrubbed[key] = repository_alias
                    continue
                scrubbed[key] = walk(
                    value, parent_key=key, in_user=user_like,
                )
            return scrubbed
        if isinstance(node, list):
            return [
                walk(item, parent_key=parent_key, in_user=in_user)
                for item in node
            ]
        if isinstance(node, str):
            return rewrite(node)
        return node

    return walk(payload)


def unscrubbed_paths(payload: Any, *, forbidden: set[str]) -> list[str]:
    """Paths in ``payload`` that still disclose something.

    Checked against the real values the recorder set out to remove,
    rather than against a guess about what identifying data looks like.
    A recorder that cannot name what it is removing cannot verify that
    it removed it.
    """
    offenders: list[str] = []
    needles = {value for value in forbidden if value}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else key
                if key in _DROPPED_KEYS:
                    offenders.append(here)
                walk(value, here)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            for needle in needles:
                if needle in node:
                    offenders.append(f"{path} (contains '{needle}')")
                    break

    walk(payload, "")
    return offenders
