"""Core actor contract (Commit 20).

The minimum shape of "who" that both intelligence and coordination need.
Neither domain owns this concept, so it lives in core.

Identity answers WHO. Coordination relationships answer IN WHAT
CAPACITY — approval authority, ownership, expertise, and implementation
are relation types on ``OrganizationalActorRef`` in the coordination
layer, deliberately absent here.
"""

from __future__ import annotations

from pydantic import BaseModel


class ActorReference(BaseModel):
    """A reference to an organizational actor: who they are, their role
    and team, and (once resolved) their canonical identity.

    This carries no capacity or authority information by design.
    """
    actor_id: str
    display_name: str | None = None
    role: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    canonical_entity_id: str | None = None
