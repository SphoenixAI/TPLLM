"""Outreach routing — learn how each person prefers to be reached.

A logistics concern: which channel reaches someone, when they are
reachable, whether one-click options work for them, and how long to
allow for a reply.

An earlier release removed the responsiveness grade this module used to assign
and the escalation it drove ("loop in their lead for important
requests"). What is left routes outreach and cannot rank anybody: there
is no rate, no ignored count and no grade to sort on.
"""

from __future__ import annotations

import json
from datetime import datetime

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.engagement import (
    EngagementProfile,
    PreferredChannel,
    ResponseEvent,
)

ENGAGEMENT_ARTIFACT = "engagement_profiles"


class EngagementTracker:
    """Track and analyze engagement patterns per person."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def record_response(
        self,
        program_id: str,
        person: str,
        request_type: str = "",
        response_hours: float | None = None,
        channel: str = "SLACK",
        had_quick_options: bool = False,
        used_quick_option: bool = False,
    ) -> EngagementProfile:
        """Record an observed reply and update the routing profile.

        A request that was never answered is not recorded as anything.
        The reason is unknowable from here, and counting it is how a
        latency estimate becomes a reliability score.
        """
        profile = self._get_or_create(program_id, person)

        try:
            ch = PreferredChannel(channel)
        except ValueError:
            ch = PreferredChannel.SLACK

        event = ResponseEvent(
            request_type=request_type,
            response_hours=response_hours,
            channel=ch,
            had_quick_options=had_quick_options,
            used_quick_option=used_quick_option,
        )
        if response_hours is not None:
            event.responded_at = datetime.utcnow()

        profile.record_response(event)
        self._save_profiles(program_id, self._load_profiles(program_id) | {person: profile})
        return profile

    def get_profile(self, program_id: str, person: str) -> EngagementProfile | None:
        profiles = self._load_profiles(program_id)
        return profiles.get(person)

    def get_all_profiles(self, program_id: str) -> list[EngagementProfile]:
        return list(self._load_profiles(program_id).values())

    def get_summary(self, program_id: str) -> str:
        profiles = self._load_profiles(program_id)
        if not profiles:
            return "No outreach observations yet."
        lines = ["# Outreach routing"]
        for profile in sorted(profiles.values(), key=lambda p: p.avg_response_hours):
            lines.append(profile.summary())
        return "\n".join(lines)

    def get_best_channel(self, program_id: str, person: str) -> str:
        """Get the best channel to reach someone based on their history."""
        profile = self._get_or_create(program_id, person)
        return profile.preferred_channel.value

    def get_best_time(self, program_id: str, person: str) -> str:
        """Get the best time window to reach someone."""
        profile = self._get_or_create(program_id, person)
        return profile.best_response_window

    def get_outreach_recommendation(self, program_id: str, person: str) -> dict:
        """How to reach someone, and how long to allow for a reply."""
        profile = self._get_or_create(program_id, person)
        return {
            "person": person,
            "best_channel": profile.preferred_channel.value,
            "best_time": profile.best_response_window,
            "use_quick_options": profile.prefers_quick_options,
            "batch_requests": profile.prefers_batched,
            "typical_response_hours": (
                profile.median_response_hours if profile.has_observations
                else None
            ),
            "observations": profile.observed_responses,
            "recommendation": self._build_recommendation(profile),
        }

    def _build_recommendation(self, profile: EngagementProfile) -> str:
        """Routing advice, stated as logistics.

        Deliberately the same shape whatever the latency is. The old
        version branched on a grade and became progressively more
        punitive as the number got worse, ending at "loop in their
        lead" — which is what made it an evaluation rather than a
        routing hint.
        """
        channel = profile.preferred_channel.value
        if not profile.has_observations:
            return (
                f"No replies observed from {profile.person} yet. Try "
                f"{channel} during {profile.best_response_window} and "
                f"include quick options."
            )

        allowance = profile.median_response_hours
        plan = (
            f"Reach {profile.person} on {channel} during "
            f"{profile.best_response_window}. Allow ~{allowance:.0f}h for a "
            f"reply ({profile.observed_responses} observation(s))."
        )
        if profile.prefers_quick_options:
            plan += " Quick options have worked."
        if profile.prefers_batched:
            plan += " Batch requests rather than sending them singly."
        if allowance > 48:
            # A long latency is a scheduling fact, so it changes the
            # plan — not a judgement, so it does not change the person.
            plan += (
                " At this latency, ask earlier than the deadline rather "
                "than asking more often."
            )
        return plan

    def _get_or_create(self, program_id: str, person: str) -> EngagementProfile:
        profiles = self._load_profiles(program_id)
        if person not in profiles:
            profiles[person] = EngagementProfile(person=person, program_id=program_id)
        return profiles[person]

    def _load_profiles(self, program_id: str) -> dict[str, EngagementProfile]:
        raw = self._store.load_raw(program_id, ENGAGEMENT_ARTIFACT)
        if raw is None:
            return {}
        try:
            data = json.loads(raw)
            return {k: EngagementProfile.model_validate(v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _save_profiles(self, program_id: str, profiles: dict[str, EngagementProfile]) -> None:
        data = {k: v.model_dump() for k, v in profiles.items()}
        self._store.save_raw(program_id, ENGAGEMENT_ARTIFACT, json.dumps(data, indent=2, default=str))
