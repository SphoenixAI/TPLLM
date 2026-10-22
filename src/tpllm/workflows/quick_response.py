"""Quick Response Protocol — one-click response options for every request.

Instead of open-ended questions that require context-switching,
the agent sends structured options that engineers can respond to
with a single click. Like vibe coding but for project management.

Examples:
  "What's the status of auth migration?"
  →  [On track for Mar 1] [Slipping to Mar 8] [Blocked - need help] [Need to discuss]

  "Can you review the API spec by Thursday?"
  →  [Yes, will do] [Can do Friday instead] [Need someone else] [Already reviewed]
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class QuickResponseOption:
    """A single clickable response option."""

    label: str              # Short display text
    value: str              # Machine-readable value
    emoji: str = ""         # Optional emoji prefix
    follow_up: str = ""     # What the agent does when this is selected


@dataclass
class QuickResponseSet:
    """A complete set of response options for a request."""

    request_type: str       # What kind of request this is
    context: str = ""       # Brief context for the responder
    options: list[QuickResponseOption] = field(default_factory=list)
    allow_custom: bool = True  # Allow free-text response too

    def render_inline(self) -> str:
        """Render as inline options (for Slack/GChat)."""
        parts = [f"[{opt.label}]" for opt in self.options]
        if self.allow_custom:
            parts.append("[Other...]")
        return " ".join(parts)

    def render_numbered(self) -> str:
        """Render as numbered list."""
        lines = []
        for i, opt in enumerate(self.options, 1):
            prefix = f"{opt.emoji} " if opt.emoji else ""
            lines.append(f"  {i}. {prefix}{opt.label}")
        if self.allow_custom:
            lines.append(f"  {len(self.options) + 1}. Other (type your response)")
        return "\n".join(lines)


class QuickResponseProtocol:
    """Generates contextual quick-response options for different request types.

    The protocol knows what reasonable responses look like for each
    type of TPM request and generates appropriate one-click options.
    """

    # ── Status check responses ────────────────────────────────

    def status_check(self, item_name: str, target_date: str = "") -> QuickResponseSet:
        """Generate options for a status check request."""
        date_ref = f" for {target_date}" if target_date else ""
        return QuickResponseSet(
            request_type="status_check",
            context=f"Status of: {item_name}{date_ref}",
            options=[
                QuickResponseOption("On track", "ON_TRACK", follow_up="Great, logged."),
                QuickResponseOption(f"Slipping", "SLIPPING", follow_up="How many days? Who needs to know?"),
                QuickResponseOption("Blocked", "BLOCKED", follow_up="What's the blocker? I'll help unblock."),
                QuickResponseOption("Need to discuss", "DISCUSS", follow_up="I'll find 15 min on the calendar."),
            ],
        )

    def review_request(self, artifact: str, deadline: str = "") -> QuickResponseSet:
        """Generate options for a review/approval request."""
        by = f" by {deadline}" if deadline else ""
        return QuickResponseSet(
            request_type="review_request",
            context=f"Review: {artifact}{by}",
            options=[
                QuickResponseOption("Will review", "ACCEPTED", follow_up="Tracking — will follow up if not done by deadline."),
                QuickResponseOption(f"Can't{by}, suggest later", "RESCHEDULE", follow_up="When works for you?"),
                QuickResponseOption("Not the right reviewer", "REDIRECT", follow_up="Who should review this?"),
                QuickResponseOption("Already reviewed", "DONE", follow_up="Thanks! Closing this out."),
            ],
        )

    def decision_needed(self, decision: str, options: list[str] | None = None) -> QuickResponseSet:
        """Generate options for a pending decision."""
        qr_options = []
        for opt in (options or []):
            qr_options.append(QuickResponseOption(opt, opt.upper().replace(" ", "_")))
        qr_options.append(
            QuickResponseOption("Need more info", "NEED_INFO", follow_up="What info do you need?")
        )
        qr_options.append(
            QuickResponseOption("Not my call", "REDIRECT", follow_up="Who should decide?")
        )
        return QuickResponseSet(
            request_type="decision_needed",
            context=f"Decision: {decision}",
            options=qr_options,
        )

    def timeline_confirmation(self, milestone: str, proposed_date: str) -> QuickResponseSet:
        """Generate options for timeline confirmation."""
        return QuickResponseSet(
            request_type="timeline_confirmation",
            context=f"Confirm: {milestone} by {proposed_date}",
            options=[
                QuickResponseOption(f"Confirmed {proposed_date}", "CONFIRMED", follow_up="Locked in."),
                QuickResponseOption("Need more time", "EXTEND", follow_up="What date works? What changed?"),
                QuickResponseOption("Depends on blocker", "CONDITIONAL", follow_up="What's the dependency?"),
                QuickResponseOption("Can deliver sooner", "ACCELERATE", follow_up="Nice! When?"),
            ],
        )

    def scope_change_approval(self, change_title: str, impact: str = "") -> QuickResponseSet:
        """Generate options for scope change approval."""
        impact_ref = f" (Impact: {impact})" if impact else ""
        return QuickResponseSet(
            request_type="scope_change_approval",
            context=f"Scope change: {change_title}{impact_ref}",
            options=[
                QuickResponseOption("Approve", "APPROVED", follow_up="Approved — updating baseline."),
                QuickResponseOption("Reject", "REJECTED", follow_up="Noted — what's the reason?"),
                QuickResponseOption("Need impact analysis", "NEED_ANALYSIS", follow_up="I'll prepare one."),
                QuickResponseOption("Defer to next sprint", "DEFERRED", follow_up="Moved to backlog."),
            ],
        )

    def blocker_triage(self, blocker: str) -> QuickResponseSet:
        """Generate options for blocker resolution."""
        return QuickResponseSet(
            request_type="blocker_triage",
            context=f"Blocker: {blocker}",
            options=[
                QuickResponseOption("I can unblock this", "WILL_FIX", follow_up="ETA?"),
                QuickResponseOption("Need help from another team", "NEED_HELP", follow_up="Which team?"),
                QuickResponseOption("Working around it", "WORKAROUND", follow_up="What's the workaround?"),
                QuickResponseOption("This is a non-issue", "FALSE_ALARM", follow_up="Got it — closing out."),
            ],
        )

    def commitment_check(self, commitment: str, owner: str) -> QuickResponseSet:
        """Generate options for checking on a meeting commitment."""
        return QuickResponseSet(
            request_type="commitment_check",
            context=f"{owner}'s commitment: {commitment}",
            options=[
                QuickResponseOption("Done", "COMPLETED", follow_up="Marking complete."),
                QuickResponseOption("In progress", "IN_PROGRESS", follow_up="When will it be done?"),
                QuickResponseOption("Haven't started", "NOT_STARTED", follow_up="What's blocking you?"),
                QuickResponseOption("No longer needed", "CANCELLED", follow_up="Why? Updating records."),
            ],
        )

    def generate_for_request(self, request_type: str, **kwargs) -> QuickResponseSet:
        """Auto-generate quick responses based on request type."""
        generators = {
            "status_check": self.status_check,
            "review_request": self.review_request,
            "decision_needed": self.decision_needed,
            "timeline_confirmation": self.timeline_confirmation,
            "scope_change_approval": self.scope_change_approval,
            "blocker_triage": self.blocker_triage,
            "commitment_check": self.commitment_check,
        }
        gen = generators.get(request_type)
        if gen is None:
            return QuickResponseSet(
                request_type=request_type,
                options=[
                    QuickResponseOption("Acknowledged", "ACK"),
                    QuickResponseOption("Working on it", "IN_PROGRESS"),
                    QuickResponseOption("Need more context", "NEED_CONTEXT"),
                    QuickResponseOption("Not my area", "REDIRECT"),
                ],
            )
        return gen(**kwargs)
