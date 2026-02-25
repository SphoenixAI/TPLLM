"""Engagement Tracker — learn how each person prefers to be reached.

Not punitive. Used to optimize *when*, *how*, and *what* to send to
each person so the agent's outreach is maximally effective and
minimally disruptive.
"""

from __future__ import annotations

import json
from datetime import datetime

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.engagement import (
    EngagementProfile,
    PreferredChannel,
    ResponseEvent,
    ResponsivenessGrade,
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
        ignored: bool = False,
    ) -> EngagementProfile:
        """Record a response event and update the person's profile."""
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
            ignored=ignored,
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
            return "No engagement data yet."
        lines = ["# Engagement Profiles"]
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
        """Get a full recommendation for how to reach someone."""
        profile = self._get_or_create(program_id, person)
        return {
            "person": person,
            "grade": profile.grade.value,
            "best_channel": profile.preferred_channel.value,
            "best_time": profile.best_response_window,
            "use_quick_options": profile.prefers_quick_options,
            "batch_requests": profile.prefers_batched,
            "avg_response_hours": profile.avg_response_hours,
            "response_rate": profile.response_rate,
            "recommendation": self._build_recommendation(profile),
        }

    def _build_recommendation(self, profile: EngagementProfile) -> str:
        if profile.grade == ResponsivenessGrade.HIGHLY_RESPONSIVE:
            return (
                f"Reach {profile.person} on {profile.preferred_channel.value}. "
                f"They typically respond within {profile.avg_response_hours:.0f}h. "
                f"Quick options {'work well' if profile.prefers_quick_options else 'not preferred'}."
            )
        elif profile.grade == ResponsivenessGrade.RESPONSIVE:
            return (
                f"Reach {profile.person} on {profile.preferred_channel.value} "
                f"during {profile.best_response_window}. Allow ~{profile.avg_response_hours:.0f}h for response. "
                f"{'Use quick options.' if profile.prefers_quick_options else 'They prefer free-text.'}"
            )
        elif profile.grade == ResponsivenessGrade.SLOW:
            return (
                f"{profile.person} is typically slow to respond ({profile.avg_response_hours:.0f}h avg). "
                f"Use {profile.preferred_channel.value} with quick options. "
                f"Consider batching requests. Set explicit deadlines."
            )
        else:
            return (
                f"{profile.person} has a {profile.response_rate:.0%} response rate. "
                f"Recommend: quick options only, explicit deadlines, "
                f"and loop in their lead for important requests."
            )

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
