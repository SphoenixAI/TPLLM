"""Smart Nudge Engine — graduated follow-ups that respect engagement budgets.

Three-tier escalation:
  Tier 0 (OBSERVE):       Track silently. No outreach.
  Tier 1 (GENTLE_REMIND): Friendly nudge with context + quick response options.
  Tier 2 (FIRM_REMIND):   Direct reminder with impact statement + deadline.
  Tier 3 (ESCALATE):      Loop in manager/lead with full context chain.

Every nudge:
  - Checks engagement budget before sending
  - Includes WHY it matters (what it blocks)
  - Offers quick-response options (one-click answers)
  - Never sends during quiet hours or weekends
"""

from __future__ import annotations

import json
from datetime import datetime, date
from uuid import uuid4

from lltpm.artifacts.store import ArtifactStore
from lltpm.models.nudge import (
    EngagementBudget,
    NudgeChannel,
    NudgeLog,
    NudgePolicy,
    NudgeRecord,
    NudgeStatus,
    NudgeTier,
)

NUDGE_LOG_ARTIFACT = "nudge_log"


class NudgeEngine:
    """Smart follow-up engine with graduated escalation."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Create nudge requests ─────────────────────────────────

    def create_nudge(
        self,
        program_id: str,
        target_person: str,
        subject: str,
        why_it_matters: str = "",
        blocks: list[str] | None = None,
        source_type: str = "",
        source_id: str = "",
        quick_options: list[str] | None = None,
        escalate_to: str = "",
        channel: str = "IN_TOOL",
    ) -> NudgeRecord:
        """Create a new nudge tracking record. Starts at OBSERVE tier."""
        log = self._load_log(program_id)

        try:
            ch = NudgeChannel(channel)
        except ValueError:
            ch = NudgeChannel.IN_TOOL

        record = NudgeRecord(
            id=f"NDG-{uuid4().hex[:6]}",
            target_person=target_person,
            subject=subject,
            source_type=source_type,
            source_id=source_id,
            program_id=program_id,
            tier=NudgeTier.OBSERVE,
            channel=ch,
            status=NudgeStatus.PENDING,
            why_it_matters=why_it_matters,
            blocks=blocks or [],
            quick_response_options=quick_options or [],
            escalate_to=escalate_to,
        )

        log.records.append(record)
        self._save_log(program_id, log)
        return record

    def record_response(
        self,
        program_id: str,
        nudge_id: str,
        response: str,
    ) -> bool:
        """Record that someone responded to a nudge."""
        log = self._load_log(program_id)
        for record in log.records:
            if record.id == nudge_id:
                record.status = NudgeStatus.RESPONDED
                record.responded_at = datetime.utcnow()
                record.response = response
                self._save_log(program_id, log)
                return True
        return False

    # ── Evaluate what needs nudging ───────────────────────────

    def evaluate_pending(self, program_id: str) -> list[dict]:
        """Evaluate all pending nudges and return actions to take.

        Returns a list of dicts describing what the agent should do:
        - Send a message (tier 1/2)
        - Escalate (tier 3)
        - Do nothing (within observe window or budget exhausted)
        """
        log = self._load_log(program_id)
        actions = []

        for record in log.records:
            if record.status not in (NudgeStatus.PENDING, NudgeStatus.SENT):
                continue

            policy = log.policies.get(record.target_person, log.default_policy)
            hours = record.hours_since_request

            # Determine what tier we should be at
            target_tier = NudgeTier.OBSERVE
            if hours >= policy.tier3_after_hours:
                target_tier = NudgeTier.ESCALATE
            elif hours >= policy.tier2_after_hours:
                target_tier = NudgeTier.FIRM_REMIND
            elif hours >= policy.tier1_after_hours:
                target_tier = NudgeTier.GENTLE_REMIND

            # Skip if we're already at or past this tier
            tier_order = [NudgeTier.OBSERVE, NudgeTier.GENTLE_REMIND,
                         NudgeTier.FIRM_REMIND, NudgeTier.ESCALATE]
            if tier_order.index(target_tier) <= tier_order.index(record.tier):
                continue

            # Check max nudges
            if record.nudge_count >= policy.max_nudges_per_item:
                continue

            # Check engagement budget
            budget = self._get_budget(log, record.target_person)
            if not budget.can_nudge:
                actions.append({
                    "nudge_id": record.id,
                    "action": "BUDGET_EXHAUSTED",
                    "person": record.target_person,
                    "subject": record.subject,
                    "detail": f"Engagement budget exhausted for {record.target_person} "
                              f"(daily: {budget.daily_remaining}, weekly: {budget.weekly_remaining})",
                })
                continue

            # Check quiet hours
            now = datetime.utcnow()
            if policy.quiet_hours_start <= now.hour or now.hour < policy.quiet_hours_end:
                continue

            # Generate the nudge action
            message = self._compose_message(record, target_tier)

            actions.append({
                "nudge_id": record.id,
                "action": "SEND_NUDGE",
                "person": record.target_person,
                "tier": target_tier.value,
                "channel": record.channel.value,
                "subject": record.subject,
                "message": message,
                "quick_options": record.quick_response_options,
                "escalate_to": record.escalate_to if target_tier == NudgeTier.ESCALATE else "",
                "why_it_matters": record.why_it_matters,
                "blocks": record.blocks,
            })

            # Update the record
            record.tier = target_tier
            record.nudge_count += 1
            record.message = message
            record.status = NudgeStatus.SENT
            record.sent_at = datetime.utcnow()
            budget.spend(1)

            if target_tier == NudgeTier.ESCALATE:
                record.status = NudgeStatus.ESCALATED

        self._save_log(program_id, log)
        return actions

    # ── Budget management ─────────────────────────────────────

    def set_budget(
        self,
        program_id: str,
        person: str,
        daily: int = 3,
        weekly: int = 10,
    ) -> EngagementBudget:
        """Set engagement budget for a person."""
        log = self._load_log(program_id)
        budget = EngagementBudget(
            person=person, daily_budget=daily, weekly_budget=weekly,
        )
        log.budgets[person] = budget
        self._save_log(program_id, log)
        return budget

    def set_policy(
        self,
        program_id: str,
        person: str,
        tier1_hours: float = 24,
        tier2_hours: float = 48,
        tier3_hours: float = 72,
    ) -> NudgePolicy:
        """Set nudge policy for a person."""
        log = self._load_log(program_id)
        policy = NudgePolicy(
            tier1_after_hours=tier1_hours,
            tier2_after_hours=tier2_hours,
            tier3_after_hours=tier3_hours,
        )
        log.policies[person] = policy
        self._save_log(program_id, log)
        return policy

    # ── Queries ───────────────────────────────────────────────

    def get_pending(self, program_id: str) -> list[NudgeRecord]:
        return self._load_log(program_id).pending

    def get_summary(self, program_id: str) -> str:
        log = self._load_log(program_id)
        pending = log.pending
        awaiting = log.awaiting_response

        lines = [
            "# Nudge Summary",
            f"Total: {len(log.records)} | Pending: {len(pending)} | "
            f"Awaiting response: {len(awaiting)} | "
            f"Response rate: {log.response_rate:.0%}",
        ]

        if awaiting:
            lines.append("\n## Awaiting Response")
            for r in awaiting:
                hours = r.hours_since_request
                lines.append(
                    f"- [{r.tier.value}] **{r.target_person}**: {r.subject} "
                    f"— {hours:.0f}h since request"
                )

        return "\n".join(lines)

    # ── Internal helpers ──────────────────────────────────────

    def _compose_message(self, record: NudgeRecord, tier: NudgeTier) -> str:
        """Compose the nudge message based on tier."""
        if tier == NudgeTier.GENTLE_REMIND:
            msg = (
                f"Hi {record.target_person} — friendly reminder about: "
                f"*{record.subject}*"
            )
            if record.why_it_matters:
                msg += f"\n\nWhy this matters: {record.why_it_matters}"
            if record.quick_response_options:
                msg += "\n\nQuick response:"
                for i, opt in enumerate(record.quick_response_options, 1):
                    msg += f"\n  {i}. {opt}"
            return msg

        elif tier == NudgeTier.FIRM_REMIND:
            msg = (
                f"{record.target_person} — I need your input on: "
                f"*{record.subject}*"
            )
            if record.blocks:
                msg += f"\n\nThis is blocking: {', '.join(record.blocks)}"
            if record.why_it_matters:
                msg += f"\nImpact: {record.why_it_matters}"
            msg += f"\n\nThis has been open for {record.hours_since_request:.0f} hours."
            if record.quick_response_options:
                msg += "\n\nPlease select one:"
                for i, opt in enumerate(record.quick_response_options, 1):
                    msg += f"\n  {i}. {opt}"
            return msg

        elif tier == NudgeTier.ESCALATE:
            escalation_target = record.escalate_to or "team lead"
            msg = (
                f"Escalation to {escalation_target}:\n\n"
                f"I've been unable to get a response from {record.target_person} "
                f"regarding: *{record.subject}*\n\n"
                f"Open for: {record.hours_since_request:.0f} hours | "
                f"Nudges sent: {record.nudge_count}"
            )
            if record.blocks:
                msg += f"\nBlocking: {', '.join(record.blocks)}"
            if record.why_it_matters:
                msg += f"\nImpact: {record.why_it_matters}"
            return msg

        return ""

    def _get_budget(self, log: NudgeLog, person: str) -> EngagementBudget:
        if person not in log.budgets:
            log.budgets[person] = EngagementBudget(person=person)
        return log.budgets[person]

    def _load_log(self, program_id: str) -> NudgeLog:
        log = self._store.load(program_id, NUDGE_LOG_ARTIFACT, NudgeLog)
        if log is None:
            log = NudgeLog(program_id=program_id)
        return log

    def _save_log(self, program_id: str, log: NudgeLog) -> None:
        self._store.save(program_id, NUDGE_LOG_ARTIFACT, log)
