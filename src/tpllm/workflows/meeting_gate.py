"""Meeting Gate — the anti-meeting enforcer.

A meeting is only justified when ALL of these are true:
1. A decision owner is identified
2. Options/tradeoffs are pre-written
3. A pre-read document is available
4. A "decide or cancel" deadline is set

Otherwise, propose an async alternative.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MeetingVerdict(str, Enum):
    APPROVED = "APPROVED"
    REJECTED_ASYNC = "REJECTED_ASYNC"


@dataclass
class MeetingProposal:
    """A request to schedule a meeting."""

    topic: str
    reason: str  # why synchronous time is needed
    decision_owner: str = ""
    options_documented: bool = False
    pre_read_available: bool = False
    decide_by_deadline: str = ""
    attendees: list[str] | None = None
    duration_minutes: int = 30


@dataclass
class GateResult:
    verdict: MeetingVerdict
    failures: list[str]
    async_alternative: str


class MeetingGate:
    """Evaluate whether a proposed meeting should happen or go async."""

    GATE_CHECKS = [
        ("decision_owner", "No decision owner identified — who will make the call?"),
        ("options_documented", "Options/tradeoffs are not pre-written — document them first."),
        ("pre_read_available", "No pre-read available — write and share one before booking."),
        ("decide_by_deadline", "No decide-by deadline set — set one to enforce closure."),
    ]

    def evaluate(self, proposal: MeetingProposal) -> GateResult:
        failures: list[str] = []

        for field, message in self.GATE_CHECKS:
            value = getattr(proposal, field)
            if isinstance(value, bool) and not value:
                failures.append(message)
            elif isinstance(value, str) and not value.strip():
                failures.append(message)

        if failures:
            alt = self._suggest_async(proposal, failures)
            return GateResult(
                verdict=MeetingVerdict.REJECTED_ASYNC,
                failures=failures,
                async_alternative=alt,
            )

        return GateResult(
            verdict=MeetingVerdict.APPROVED,
            failures=[],
            async_alternative="",
        )

    def _suggest_async(self, proposal: MeetingProposal, failures: list[str]) -> str:
        steps: list[str] = []

        if any("decision owner" in f.lower() for f in failures):
            steps.append("Identify and assign a decision owner.")

        if any("options" in f.lower() for f in failures):
            steps.append(
                "Write a short doc with options, tradeoffs, and a recommendation. "
                "Share it in the team channel."
            )

        if any("pre-read" in f.lower() for f in failures):
            steps.append(
                "Draft a pre-read document and share it with a 24h review deadline."
            )

        if any("deadline" in f.lower() for f in failures):
            steps.append(
                "Set a decide-by date. If no objections by that date, the recommendation stands."
            )

        steps.append(
            f"Post a structured async decision request in the channel: "
            f'"{proposal.topic} — please review and comment by [deadline]."'
        )
        steps.append(
            "If no resolution after the deadline, escalate to a focused 15-min "
            "decision meeting with only the decision owner and key stakeholders."
        )

        return "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
