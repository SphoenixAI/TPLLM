"""The provider registry — what each source system can actually do.

One table, read by `lltpm integrations sources`, so the answer to "is
Slack supported?" is never a bare `True`. Support is not one fact. A
provider can be normalized from a recorded payload, verified against a
live API, have its webhook signature checked, or none of those, and the
differences matter to whoever is deciding whether to trust what comes
out.

So capabilities are enumerated and each provider declares the ones it
has. An integration implemented against the provider's published
examples but never run against the live API says exactly that, rather
than appearing beside one that has been.

Deliberately declarative, and deliberately the only shared file the
provider modules need. Each provider lives in its own module and exposes
one uniform entry point; this table names it and nothing more, so adding
a provider is an entry here rather than an edit to everything.
"""

from __future__ import annotations

import importlib
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field


class ProviderCapability(str, Enum):
    """What a provider integration can do, stated one claim at a time."""

    #: Payloads of this provider's shapes become EvidenceEnvelopes.
    NORMALIZATION = "normalization"
    #: A read-only client exists and can fetch from the live API.
    LIVE_READ = "live_read"
    #: Webhook deliveries can be normalized, keyed on the provider's own
    #: delivery identity.
    WEBHOOK_NORMALIZATION = "webhook_normalization"
    #: Webhook authenticity can be checked before anything is parsed.
    WEBHOOK_SIGNATURE_VERIFICATION = "webhook_signature_verification"
    #: At least one contract was recorded from the live API, so
    #: `lltpm integrations verify` can re-check it.
    RECORDED_CONTRACT = "recorded_contract"
    #: At least one contract comes from the provider's own published
    #: example. Honest, and weaker than a recording.
    OFFICIAL_CONTRACT = "official_contract"
    #: `verify` can run against this provider at all.
    CONTRACT_VERIFICATION = "contract_verification"
    #: Provider-side pagination and rate limiting are respected.
    RATE_LIMIT_AWARE = "rate_limit_aware"


class ProviderDescriptor(BaseModel):
    """One provider's declared surface.

    ``normalize_entry_point`` is a dotted path rather than a callable so
    that reading this table does not import every provider module —
    which keeps `lltpm integrations sources` fast and keeps a broken
    provider from breaking the listing that would have told you.
    """

    source_system: str
    payload_kinds: tuple[str, ...]
    capabilities: frozenset[ProviderCapability]
    normalize_entry_point: str
    scope_label: str = Field(
        description=(
            "What a caller passes as `scope`, for CLI help. Not uniform "
            "across providers on purpose: GitHub needs the whole "
            "`owner/name` because a payload may not carry it, while Jira "
            "takes only the site and composes the project key from the "
            "payload — which is the better design, since the project key "
            "is the provider's own fact rather than the caller's claim."
        ),
    )
    notes: str = ""

    model_config = {"frozen": True}

    #: Whether the normalizer takes the whole binding scope or only its
    #: first segment. Jira binds on `site/PROJECT` but composes the
    #: project key from the payload, so it must be handed the site
    #: alone — the project key is the provider's own fact rather than
    #: the caller's claim, and letting the caller assert it would be a
    #: way to file evidence under the wrong project.
    normalizer_takes_scope_prefix: bool = False

    def has(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    def normalizer_scope(self, binding_scope: str) -> str:
        """What to pass the normalizer, given the scope a caller bound.

        One declarative difference in the registry, rather than a
        conditional per provider at every call site. A caller always
        passes the scope that `lltpm integrations sources` shows; this
        is the adapter between that and what each normalizer wants.
        """
        if not self.normalizer_takes_scope_prefix:
            return binding_scope
        return binding_scope.split("/", 1)[0]

    def normalizer(self) -> Callable[..., Any]:
        """Import and return the provider's uniform entry point.

        Every provider exposes the same signature::

            normalize_payload(
                *, payload_kind, payload, bindings, received_at,
                scope="", delivery_id=None,
            ) -> NormalizationOutcome

        A uniform seam is what lets the CLI and the cross-provider tests
        treat providers interchangeably without a conditional per
        provider.
        """
        module_path, _, name = self.normalize_entry_point.rpartition(".")
        return getattr(importlib.import_module(module_path), name)


#: Every provider, by source system. The single place a new integration
#: is registered.
PROVIDERS: dict[str, ProviderDescriptor] = {
    "github": ProviderDescriptor(
        source_system="github",
        payload_kinds=("pull_request", "issue", "event", "review"),
        capabilities=frozenset({
            ProviderCapability.NORMALIZATION,
            ProviderCapability.LIVE_READ,
            ProviderCapability.WEBHOOK_NORMALIZATION,
            ProviderCapability.RECORDED_CONTRACT,
            ProviderCapability.CONTRACT_VERIFICATION,
        }),
        normalize_entry_point=(
            "lltpm.integrations.normalizers.normalize_payload"
        ),
        scope_label="repository (owner/name)",
        notes=(
            "Pull requests and events are recorded from the live API; "
            "issues are derived from those recordings and reviews are "
            "hand-written, because the recorded repository has neither. "
            "The reader does not yet honour Retry-After, which is why it "
            "does not claim RATE_LIMIT_AWARE."
        ),
    ),
    "slack": ProviderDescriptor(
        source_system="slack",
        payload_kinds=(
            "message", "message_changed", "message_deleted", "event",
            "conversations_history", "channel_scope",
        ),
        capabilities=frozenset({
            ProviderCapability.NORMALIZATION,
            ProviderCapability.LIVE_READ,
            ProviderCapability.WEBHOOK_NORMALIZATION,
            ProviderCapability.WEBHOOK_SIGNATURE_VERIFICATION,
            ProviderCapability.OFFICIAL_CONTRACT,
            ProviderCapability.CONTRACT_VERIFICATION,
            ProviderCapability.RATE_LIMIT_AWARE,
        }),
        normalize_entry_point=(
            "lltpm.integrations.slack_normalizer.normalize_payload"
        ),
        scope_label="workspace/channel (T…/C…)",
        notes=(
            "No contract is recorded: slack.com is unreachable from the "
            "environment this was built in, so all six contracts come "
            "from Slack's published examples. Reactions, DMs and bot "
            "messages are declined rather than ingested, each with a "
            "stated reason."
        ),
    ),
    "jira": ProviderDescriptor(
        source_system="jira",
        payload_kinds=(
            "issue", "changelog", "comment", "issue_link", "search",
            "webhook",
        ),
        capabilities=frozenset({
            ProviderCapability.NORMALIZATION,
            ProviderCapability.LIVE_READ,
            ProviderCapability.WEBHOOK_NORMALIZATION,
            ProviderCapability.WEBHOOK_SIGNATURE_VERIFICATION,
            ProviderCapability.OFFICIAL_CONTRACT,
            ProviderCapability.CONTRACT_VERIFICATION,
            ProviderCapability.RATE_LIMIT_AWARE,
        }),
        normalize_entry_point=(
            "lltpm.integrations.jira_normalizer.normalize_payload"
        ),
        scope_label="site/project key (acme.atlassian.net/ABC)",
        normalizer_takes_scope_prefix=True,
        notes=(
            "REST v3, token pagination. No contract is recorded: "
            "api.atlassian.com is unreachable from the environment this "
            "was built in, so all six contracts come from Atlassian's "
            "published examples. Transitions come from the changelog, "
            "never inferred from a snapshot; worklogs are refused. A "
            "`changelog` or `comment` payload carries no issue id of its "
            "own, so normalizing one standalone is reported as "
            "unidentifiable rather than guessed — feed it with its issue "
            "through the ingest path instead."
        ),
    ),
}


def describe_providers() -> list[ProviderDescriptor]:
    return [PROVIDERS[name] for name in sorted(PROVIDERS)]


def provider(source_system: str) -> ProviderDescriptor | None:
    return PROVIDERS.get(source_system)


def provider_names() -> list[str]:
    return sorted(PROVIDERS)
