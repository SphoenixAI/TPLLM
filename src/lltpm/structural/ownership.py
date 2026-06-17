"""Ownership context — the false-positive suppressor for scope-drift.

Naive scope-drift detection flags every cross-domain change as risk. That
produces alert fatigue and punishes healthy collaboration. The fix is
*context*: most "drift" is actually planned bridge work, prior familiarity,
or an explicit temporary authorization.

``OwnershipContext`` captures multi-level ownership for a person:
    - ``baseline_owned_node_ids``     — what they normally own
    - ``temporary_authorized_node_ids`` — explicit, time-boxed grants
    - ``historical_familiarity_node_ids`` — areas they've worked before

When the metric engine computes scope variance, any actively-modified node
that falls under temporary authorization, historical familiarity, or an
active :class:`~lltpm.ontology.entities.AuthorizedBridge` contributes zero
distance — so a migration lead touching Payments is not flagged as drift.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from lltpm.ontology.core import SourceRef
from lltpm.ontology.entities import AuthorizedBridge


class OwnershipLevel(str, Enum):
    """Granularity at which ownership / authorization can be expressed."""
    PERSON = "person"
    TEAM = "team"
    REPOSITORY = "repository"
    SERVICE = "service"
    COMPONENT = "component"
    DOMAIN = "domain"


class OwnershipContext(BaseModel):
    """Multi-level ownership picture for one person.

    Used to decide whether modifying a given node is expected (baseline,
    authorized, or familiar) or genuine scope drift.
    """
    person_id: str = ""
    team_id: str = ""
    baseline_owned_node_ids: list[str] = Field(default_factory=list)
    temporary_authorized_node_ids: list[str] = Field(default_factory=list)
    historical_familiarity_node_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    def is_expected(
        self,
        node_id: str,
        bridges: list[AuthorizedBridge] | None = None,
        at: datetime | None = None,
    ) -> bool:
        """True if modifying ``node_id`` is expected for this person.

        Expected when the node is baseline-owned, temporarily authorized,
        historically familiar, or covered by an active AuthorizedBridge.
        """
        if (
            node_id in self.baseline_owned_node_ids
            or node_id in self.temporary_authorized_node_ids
            or node_id in self.historical_familiarity_node_ids
        ):
            return True
        return self.is_authorized_by_bridge(node_id, bridges, at)

    def is_authorized_by_bridge(
        self,
        node_id: str,
        bridges: list[AuthorizedBridge] | None = None,
        at: datetime | None = None,
    ) -> bool:
        """True if an active AuthorizedBridge grants this person the node."""
        if not bridges:
            return False
        now = at or datetime.utcnow()
        for bridge in bridges:
            if bridge.person_id and bridge.person_id != self.person_id:
                continue
            if node_id not in bridge.authorized_node_ids:
                continue
            if bridge.valid_from and now < bridge.valid_from:
                continue
            if bridge.valid_until and now > bridge.valid_until:
                continue
            return True
        return False
