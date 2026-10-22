"""Attention routing and runtime surfaces.

The last question in the pipeline is the one that decides whether LLTPM
is useful or exhausting: does anyone need to hear about this?

The default answer is no. ``SILENT`` and ``UPDATE_INTERNAL_STATE`` are
the expected outcomes of most passes — the system absorbed something and
carried on. Attention is spent only where judgment, authority, or a
genuinely unknown risk is involved.

When something is surfaced, it is surfaced as one explanation of one
root cause, not as a queue of alerts the reader has to reassemble. The
renderers here put the cause first, then the portfolio effect, then the
per-project detail, and they always name the projects that need nothing
— knowing where you are *not* needed is part of the value.
"""

from __future__ import annotations

from lltpm.runtime.models import (
    CausalUpdateBundle,
    IntelligenceArtifactType,
    NoActionReason,
    PortfolioIntelligenceUpdate,
    ProjectIntelligenceUpdate,
    RuntimeAttentionAction,
    ScenarioMaterialityPolicy,
)


def determine_attention_action(
    *,
    bundle: CausalUpdateBundle,
    materiality_policy: ScenarioMaterialityPolicy | None = None,
) -> RuntimeAttentionAction:
    """Choose the cheapest surface that still does the job.

    Severity outranks a decision request: if something is going badly
    wrong, that is the headline, and the decision stays visible inside
    the bundle rather than becoming a separate alert.
    """
    if not bundle.material_changes:
        return RuntimeAttentionAction.SILENT
    if not bundle.human_attention_required:
        return RuntimeAttentionAction.UPDATE_INTERNAL_STATE
    if bundle.highest_severity in ("severe", "critical"):
        return RuntimeAttentionAction.HIGH_PRIORITY_ALERT
    if bundle.decision_required:
        return RuntimeAttentionAction.DECISION_REQUEST
    return RuntimeAttentionAction.ASYNC_INFORMATION


def meeting_is_warranted(
    *,
    bundle: CausalUpdateBundle,
    unresolved_disagreement: bool = False,
    authority_ambiguous: bool = False,
) -> tuple[bool, str]:
    """A meeting is the most expensive intervention available.

    It is warranted when people genuinely disagree or nobody knows who
    decides. A change that async context fully explains is not a reason
    to put six calendars in a room.
    """
    if unresolved_disagreement:
        return True, (
            "Positions conflict and the disagreement is not resolvable from "
            "evidence alone."
        )
    if authority_ambiguous:
        return True, (
            "It is unclear who holds the authority to decide; that cannot be "
            "settled asynchronously."
        )
    if bundle.decision_required:
        return False, (
            "A decision is required, but the context, options, and trade-offs "
            "are all stated. An async decision request is sufficient."
        )
    return False, (
        "Nothing here needs synchronous discussion; the update carries its "
        "own context."
    )


def render_root_cause_surface(
    *,
    bundle: CausalUpdateBundle,
    portfolio_update: PortfolioIntelligenceUpdate | None = None,
    project_updates: list[ProjectIntelligenceUpdate] | None = None,
    no_action_project_ids: list[str] | None = None,
) -> str:
    """One compressed explanation: cause, effect, recommendation, ask.

    Deliberately ordered so a reader learns *why* several things moved
    together before learning that they moved.
    """
    project_updates = project_updates or []
    lines: list[str] = ["ROOT CHANGE", bundle.root_summary]

    if portfolio_update and portfolio_update.affected_project_ids:
        lines.append("PORTFOLIO EFFECT")
        lines.append(
            f"{', '.join(portfolio_update.affected_project_ids)} are affected "
            f"by the same cause."
        )
        for change in portfolio_update.priority_changes:
            lines.append(change)
        for bottleneck in portfolio_update.new_bottlenecks:
            lines.append(f"New bottleneck: {bottleneck}")
        for bottleneck in portfolio_update.resolved_bottlenecks:
            lines.append(f"Bottleneck resolved: {bottleneck}")

    # Several projects moved for the same reason; say it once.
    scenario_lines = _unique(
        line for update in project_updates for line in update.scenario_changes
    )
    if scenario_lines:
        lines.append("SCENARIO EFFECT")
        lines.extend(scenario_lines)

    recommendation_lines = _unique(
        line
        for update in project_updates
        for line in update.recommendation_changes
    )
    if recommendation_lines:
        lines.append("RECOMMENDATION")
        lines.extend(recommendation_lines)

    decisions = sorted({
        decision
        for update in project_updates
        for decision in update.decisions_required
    })
    if portfolio_update:
        decisions = sorted(
            set(decisions) | set(portfolio_update.decisions_required)
        )
    if decisions:
        lines.append("DECISION REQUIRED")
        lines.extend(decisions)

    if bundle.low_confidence_qualifier:
        lines.append(
            "CONFIDENCE: at least one estimate above moved materially but "
            "rests on sparse evidence; treat it as a signal to investigate."
        )

    quiet = sorted(set(no_action_project_ids or []))
    if quiet:
        lines.append("NO ACTION REQUIRED")
        lines.append(", ".join(quiet))

    return "\n".join(lines)


def _unique(lines) -> list[str]:
    seen: list[str] = []
    for line in lines:
        if line not in seen:
            seen.append(line)
    return seen


def render_project_surface(update: ProjectIntelligenceUpdate) -> str:
    """The single-project view: what changed, what it changes, what to do."""
    lines: list[str] = []
    if update.what_changed:
        lines.append("WHAT CHANGED")
        lines.extend(update.what_changed)
    if update.why_it_changed:
        lines.append("WHAT THAT CHANGES")
        lines.extend(update.why_it_changed)
    if update.scenario_changes:
        lines.append("SCENARIO SHIFT")
        lines.extend(update.scenario_changes)
    if update.kpi_changes:
        lines.append("KPI")
        lines.extend(update.kpi_changes)
    if update.readiness_changes:
        lines.append("READINESS")
        lines.extend(update.readiness_changes)
    if update.recommendation_changes:
        lines.append("AGENT RECOMMENDATION")
        lines.extend(update.recommendation_changes)
    if update.new_risks:
        lines.append("NEW RISKS")
        lines.extend(update.new_risks)
    if update.resolved_risks:
        lines.append("RESOLVED RISKS")
        lines.extend(update.resolved_risks)
    if update.research_priorities:
        lines.append("BEING RESEARCHED")
        lines.extend(update.research_priorities)
    if update.decisions_required:
        lines.append("ACTION")
        lines.extend(update.decisions_required)
        lines.append("NO MEETING REQUIRED.")
    if update.no_action_required:
        lines.append("NO ACTION REQUIRED")
        lines.extend(update.no_action_required)
    if not lines:
        lines = [
            "NO ACTION REQUIRED",
            f"{update.project_id}: state updated, nothing needs attention.",
        ]
    return "\n".join(lines)


def describe_no_action(reasons: list[NoActionReason]) -> str:
    """Say why the system stayed quiet.

    Silence without a reason is indistinguishable from a system that
    crashed.
    """
    if not reasons:
        return (
            "Organizational state was updated. No human action is required."
        )
    phrasing = {
        NoActionReason.DUPLICATE: "the event had already been processed",
        NoActionReason.NON_MATERIAL: (
            "the change was too small to act on"
        ),
        NoActionReason.ALREADY_RESOLVED: "the situation is already resolved",
        NoActionReason.ALREADY_ANSWERED: (
            "this question has already been answered"
        ),
        NoActionReason.NO_HUMAN_JUDGMENT_REQUIRED: (
            "no human judgment is required"
        ),
        NoActionReason.EXISTING_PLAN_SUFFICIENT: (
            "the existing plan already covers it"
        ),
    }
    described = [phrasing.get(r, r.value) for r in reasons]
    return (
        "Organizational state was updated. No human action is required "
        f"because {'; '.join(described)}."
    )


_ARTIFACT_SURFACE_LABEL = {
    IntelligenceArtifactType.SCENARIO_LATTICE: "scenario",
    IntelligenceArtifactType.LAUNCH_READINESS: "readiness",
    IntelligenceArtifactType.KPI_STATE: "KPI",
    IntelligenceArtifactType.DECISION_RECOMMENDATION: "recommendation",
    IntelligenceArtifactType.SCENARIO_DECISION_RECOMMENDATION: (
        "scenario-aware recommendation"
    ),
    IntelligenceArtifactType.PORTFOLIO_BOTTLENECKS: "portfolio bottleneck",
    IntelligenceArtifactType.PORTFOLIO_RECOMMENDATION: (
        "portfolio allocation"
    ),
}


def artifact_surface_label(artifact_type: IntelligenceArtifactType) -> str:
    return _ARTIFACT_SURFACE_LABEL.get(
        artifact_type, artifact_type.value.replace("_", " "),
    )
