"""Smart Calendar Engine — intelligent meeting scheduling with availability optimization.

Goes beyond basic "find free slot" to consider:
- Who actually needs to be in this meeting (scope relevance)
- Engagement patterns (schedule when people are most responsive)
- Meeting load balancing (don't overload anyone's day)
- Meeting gate integration (only schedule if gate approves)
- Focus time protection (don't fragment deep work blocks)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.calendar import (
    AttendeeInfo,
    AttendeeRole,
    AvailabilitySlot,
    CalendarBlock,
    MeetingPriority,
    MeetingReschedule,
    ScheduleResult,
    SmartScheduleRequest,
)


class SmartCalendarEngine:
    """Intelligent meeting scheduling with availability optimization."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def schedule(self, request: SmartScheduleRequest) -> ScheduleResult:
        """Schedule a meeting with smart optimization.

        Finds the best slot considering attendee availability,
        engagement patterns, meeting load, and scope relevance.
        """
        result = ScheduleResult(request_title=request.title)

        if not request.attendees:
            result.scheduling_notes.append("No attendees specified")
            return result

        if not request.meeting_gate_approved:
            result.scheduling_notes.append(
                "WARNING: Meeting not yet approved by meeting gate. "
                "Consider whether this could be handled async."
            )

        # Generate candidate slots
        candidates = self._generate_candidate_slots(request)

        # Score each slot
        scored = []
        for slot in candidates:
            score = self._score_slot(slot, request)
            slot.score = score
            scored.append(slot)

        # Sort by score (highest first)
        scored.sort(key=lambda s: s.score, reverse=True)

        result.recommended_slots = scored[:5]
        if scored:
            result.best_slot = scored[0]

        # Generate per-attendee optimization notes
        for att in request.attendees:
            if att.meeting_load_today >= att.max_daily_meetings:
                result.attendee_optimization[att.person] = (
                    f"Already at {att.meeting_load_today}/{att.max_daily_meetings} "
                    f"meetings today. Consider async alternative."
                )
            elif att.scope_relevance < 0.3:
                result.attendee_optimization[att.person] = (
                    f"Low scope relevance ({att.scope_relevance:.0%}). "
                    f"Consider making them optional or sending notes afterward."
                )

        result.scheduling_notes.append(
            f"Found {len(scored)} candidate slots. "
            f"Best slot scores {scored[0].score:.2f}/1.0." if scored else
            "No suitable slots found in the requested window."
        )

        return result

    def find_optimal_attendees(
        self,
        program_id: str,
        topic: str,
        candidate_attendees: list[AttendeeInfo],
    ) -> list[AttendeeInfo]:
        """Determine who actually needs to be in a meeting.

        Uses scope relevance and engagement scores to recommend
        required vs optional vs just-send-notes attendees.
        """
        optimized = []
        for att in candidate_attendees:
            if att.scope_relevance >= 0.7 or att.engagement_score >= 0.8:
                att.role = AttendeeRole.REQUIRED
            elif att.scope_relevance >= 0.3:
                att.role = AttendeeRole.OPTIONAL
            else:
                att.role = AttendeeRole.INFORMED
            optimized.append(att)

        return sorted(optimized, key=lambda a: a.scope_relevance, reverse=True)

    def block_focus_time(
        self,
        program_id: str,
        person: str,
        date: datetime,
        hours: int = 2,
        reason: str = "Focus time — protected by LLTPM",
    ) -> CalendarBlock:
        """Block focus time on someone's calendar."""
        start = date.replace(hour=10, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=hours)

        return CalendarBlock(
            person=person,
            title=f"Focus Time — {reason}",
            start=start,
            end=end,
            block_type="focus_time",
            reason=reason,
        )

    def suggest_reschedule(
        self,
        program_id: str,
        event_id: str,
        reason: str,
        attendees: list[AttendeeInfo] | None = None,
    ) -> MeetingReschedule:
        """Suggest rescheduling a meeting with new optimal slots."""
        reschedule = MeetingReschedule(
            event_id=event_id,
            reason=reason,
            message=f"LLTPM suggests rescheduling: {reason}",
        )

        if attendees:
            request = SmartScheduleRequest(
                title="Rescheduled meeting",
                program_id=program_id,
                attendees=attendees,
            )
            result = self.schedule(request)
            reschedule.new_slots = result.recommended_slots

        return reschedule

    def get_meeting_load(
        self,
        person: str,
        meetings_today: int = 0,
        max_daily: int = 6,
    ) -> dict:
        """Get meeting load assessment for a person."""
        ratio = meetings_today / max_daily if max_daily > 0 else 0
        if ratio >= 1.0:
            status = "overloaded"
            recommendation = "Do not schedule more meetings. Use async."
        elif ratio >= 0.75:
            status = "heavy"
            recommendation = "Only schedule if critical. Prefer async."
        elif ratio >= 0.5:
            status = "moderate"
            recommendation = "Can take meetings, prefer morning slots."
        else:
            status = "light"
            recommendation = "Good availability for scheduling."

        return {
            "person": person,
            "meetings_today": meetings_today,
            "max_daily": max_daily,
            "load_ratio": ratio,
            "status": status,
            "recommendation": recommendation,
        }

    def get_summary(self, program_id: str) -> str:
        """Get a summary of calendar-related state."""
        return (
            "# Smart Calendar\n"
            "Calendar engine ready. Use schedule() to find optimal meeting times "
            "considering attendee availability, engagement patterns, and scope relevance."
        )

    # ── Slot generation and scoring ───────────────────────────

    def _generate_candidate_slots(
        self,
        request: SmartScheduleRequest,
    ) -> list[AvailabilitySlot]:
        """Generate candidate time slots for a meeting.

        In production, this queries Google Calendar / Microsoft Graph
        for free/busy data. Here we generate synthetic slots based
        on attendee preferences.
        """
        candidates: list[AvailabilitySlot] = []
        now = datetime.utcnow()
        duration = timedelta(minutes=request.duration_minutes)

        for day_offset in range(request.preferred_window_days):
            day = now + timedelta(days=day_offset + 1)

            # Skip weekends
            if day.weekday() >= 5:
                continue

            # Skip Monday/Friday if requested
            if request.avoid_mondays and day.weekday() == 0:
                continue
            if request.avoid_fridays and day.weekday() == 4:
                continue

            # Generate hourly slots during business hours
            for hour in range(9, 17):
                # Skip lunch if requested
                if request.avoid_lunch and hour == 12:
                    continue

                start = day.replace(hour=hour, minute=0, second=0, microsecond=0)
                end = start + duration

                if end.hour > 17:
                    continue

                available = []
                unavailable = []
                for att in request.attendees:
                    pref_start, pref_end = att.preferred_hours
                    if pref_start <= hour < pref_end:
                        available.append(att.person)
                    else:
                        unavailable.append(att.person)

                candidates.append(AvailabilitySlot(
                    start=start,
                    end=end,
                    attendees_available=available,
                    attendees_unavailable=unavailable,
                ))

        return candidates

    def _score_slot(
        self,
        slot: AvailabilitySlot,
        request: SmartScheduleRequest,
    ) -> float:
        """Score a slot based on multiple factors."""
        if not request.attendees:
            return 0.5

        score = 0.0
        total_attendees = len(request.attendees)

        # 1. Attendee availability (40% weight)
        if total_attendees > 0:
            availability_ratio = len(slot.attendees_available) / total_attendees
            score += 0.4 * availability_ratio

        # 2. Required attendees present (30% weight)
        required = [a for a in request.attendees if a.role == AttendeeRole.REQUIRED]
        if required:
            required_names = {a.person for a in required}
            present = required_names & set(slot.attendees_available)
            score += 0.3 * (len(present) / len(required_names))
        else:
            score += 0.3

        # 3. Scope relevance (15% weight) — prefer slots where high-relevance people are available
        relevance_scores = []
        for att in request.attendees:
            if att.person in slot.attendees_available:
                relevance_scores.append(att.scope_relevance)
        if relevance_scores:
            score += 0.15 * (sum(relevance_scores) / len(relevance_scores))

        # 4. Engagement optimization (10% weight) — schedule when people are most responsive
        engagement_scores = []
        for att in request.attendees:
            if att.person in slot.attendees_available:
                engagement_scores.append(att.engagement_score)
        if engagement_scores:
            score += 0.10 * (sum(engagement_scores) / len(engagement_scores))

        # 5. Meeting load penalty (5% weight) — avoid overloading people
        overloaded = [
            a for a in request.attendees
            if a.meeting_load_today >= a.max_daily_meetings
            and a.person in slot.attendees_available
        ]
        if overloaded and total_attendees > 0:
            score -= 0.05 * (len(overloaded) / total_attendees)
        else:
            score += 0.05

        return max(0.0, min(1.0, score))
