"""Document Generation Engine — render TPM artifacts as formatted documents.

Converts internal data models into shareable, professional documents:
- HTML (styled, ready for browser/email)
- Markdown (for Slack, GitHub, wikis)
- Plain text (for terminals and logs)

HTML output is self-contained with inline CSS — no external dependencies.
PDF generation can be done by piping HTML through a browser/wkhtmltopdf.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from lltpm.artifacts.store import ArtifactStore


# ── CSS for HTML documents ────────────────────────────────────

_DOCUMENT_CSS = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 800px; margin: 40px auto; padding: 0 20px; color: #1a1a1a;
         line-height: 1.6; font-size: 14px; }
  h1 { border-bottom: 2px solid #2563eb; padding-bottom: 8px; color: #1e293b; }
  h2 { color: #334155; margin-top: 28px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }
  h3 { color: #475569; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  th { background: #f1f5f9; text-align: left; padding: 8px 12px; border: 1px solid #e2e8f0;
       font-weight: 600; font-size: 13px; }
  td { padding: 8px 12px; border: 1px solid #e2e8f0; font-size: 13px; }
  tr:nth-child(even) { background: #f8fafc; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px;
           font-weight: 600; }
  .green { background: #dcfce7; color: #166534; }
  .yellow { background: #fef9c3; color: #854d0e; }
  .red { background: #fee2e2; color: #991b1b; }
  .critical { background: #fecaca; color: #7f1d1d; }
  .blue { background: #dbeafe; color: #1e40af; }
  .gray { background: #f1f5f9; color: #475569; }
  .meta { color: #64748b; font-size: 12px; }
  .section-note { color: #64748b; font-style: italic; margin-top: -8px; }
  ul { padding-left: 20px; }
  li { margin-bottom: 4px; }
  .footer { margin-top: 40px; padding-top: 12px; border-top: 1px solid #e2e8f0;
            color: #94a3b8; font-size: 11px; }
</style>
"""


class DocumentGenerator:
    """Render TPM artifacts as formatted HTML, Markdown, or plain text."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    # ── Program One-Pager ─────────────────────────────────────

    def render_program_brief(self, program_id: str, fmt: str = "html") -> str:
        """Render a program one-pager."""
        from lltpm.models.program import Program
        program = self._store.load(program_id, "program", Program)
        if not program:
            return self._empty(f"No program found: {program_id}", fmt)

        if fmt == "html":
            return self._html_wrap(f"Program Brief — {program.name}", f"""
<h1>{program.name}</h1>
<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>

<h2>Overview</h2>
<table>
  <tr><th>Program ID</th><td>{program.id}</td></tr>
  <tr><th>Goal</th><td>{program.goal}</td></tr>
  <tr><th>Status</th><td><span class="badge {program.health.value.lower() if hasattr(program, 'health') else 'green'}">{getattr(program, 'health', 'GREEN')}</span></td></tr>
</table>

<h2>Success Metrics</h2>
{self._html_list([f"<strong>{m.name}</strong>: {m.target} (current: {m.current})" for m in getattr(program, 'success_metrics', [])])}

<h2>Stakeholders</h2>
<table>
<tr><th>Name</th><th>Role</th><th>Contact</th></tr>
{"".join(f"<tr><td>{s.name}</td><td>{s.role}</td><td>{s.contact}</td></tr>" for s in getattr(program, 'stakeholders', []))}
</table>
{self._footer()}""")
        else:
            lines = [
                f"# {program.name}",
                f"**ID:** {program.id}",
                f"**Goal:** {program.goal}",
            ]
            for m in getattr(program, 'success_metrics', []):
                lines.append(f"- **{m.name}**: {m.target} (current: {m.current})")
            return "\n".join(lines)

    # ── Status Update ─────────────────────────────────────────

    def render_status_update(self, program_id: str, fmt: str = "html") -> str:
        """Render the latest status update."""
        from lltpm.artifacts.status_update import StatusUpdateGenerator
        gen = StatusUpdateGenerator(self._store)
        update = gen.get_latest(program_id)
        if not update:
            return self._empty("No status updates found", fmt)

        if fmt == "html":
            health_class = update.overall_health.value.lower()
            risks_html = "".join(
                f"<tr><td>{i}</td><td>{r}</td></tr>"
                for i, r in enumerate(update.top_risks, 1)
            )
            return self._html_wrap(f"Status Update — {update.program_name}", f"""
<h1>{update.program_name} — Status {update.report_date.isoformat()}</h1>
<p>Overall: <span class="badge {health_class}">{update.overall_health.value}</span></p>

<h2>Progress</h2>
{self._html_list(update.progress)}

<h2>Top Risks</h2>
<table><tr><th>#</th><th>Risk</th></tr>{risks_html}</table>

<h2>Asks</h2>
{self._html_list(update.asks)}

<h2>Upcoming</h2>
{self._html_list(update.upcoming)}
{self._footer()}""")
        else:
            return update.render()

    # ── RAID Log ──────────────────────────────────────────────

    def render_raid_log(self, program_id: str, fmt: str = "html") -> str:
        """Render the RAID log as a formatted document."""
        from lltpm.models.risk import RAIDLog
        raid = self._store.load(program_id, "raid_log", RAIDLog)
        if not raid:
            return self._empty("No RAID log found", fmt)

        if fmt == "html":
            risk_rows = "".join(
                f"<tr><td>{r.id}</td><td>{r.title}</td>"
                f"<td><span class='badge {r.severity.value.lower()}'>{r.severity.value}</span></td>"
                f"<td>{r.owner}</td><td>{r.mitigation}</td>"
                f"<td>{r.status.value}</td></tr>"
                for r in raid.risks
            )
            issue_rows = "".join(
                f"<tr><td>{i.id}</td><td>{i.title}</td>"
                f"<td><span class='badge {i.severity.value.lower()}'>{i.severity.value}</span></td>"
                f"<td>{i.owner}</td><td>{i.status.value}</td></tr>"
                for i in raid.issues
            )
            dep_rows = "".join(
                f"<tr><td>{d.id}</td><td>{d.title}</td>"
                f"<td>{d.upstream_team}</td><td>{d.downstream_team}</td>"
                f"<td>{d.status.value}</td></tr>"
                for d in raid.dependencies
            )
            return self._html_wrap("RAID Log", f"""
<h1>RAID Log</h1>
<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>

<h2>Risks ({len(raid.risks)})</h2>
<table>
<tr><th>ID</th><th>Title</th><th>Severity</th><th>Owner</th><th>Mitigation</th><th>Status</th></tr>
{risk_rows}
</table>

<h2>Issues ({len(raid.issues)})</h2>
<table>
<tr><th>ID</th><th>Title</th><th>Severity</th><th>Owner</th><th>Status</th></tr>
{issue_rows}
</table>

<h2>Dependencies ({len(raid.dependencies)})</h2>
<table>
<tr><th>ID</th><th>Title</th><th>Upstream</th><th>Downstream</th><th>Status</th></tr>
{dep_rows}
</table>

<h2>Assumptions ({len(raid.assumptions)})</h2>
{self._html_list([f"<strong>{a.id}</strong>: {a.statement} (owner: {a.owner})" for a in raid.assumptions])}
{self._footer()}""")
        else:
            return raid.summary()

    # ── Decision Log ──────────────────────────────────────────

    def render_decision_log(self, program_id: str, fmt: str = "html") -> str:
        """Render the decision log."""
        from lltpm.models.decision import DecisionLog
        log = self._store.load(program_id, "decision_log", DecisionLog)
        if not log:
            return self._empty("No decision log found", fmt)

        if fmt == "html":
            rows = ""
            for d in log.decisions:
                chosen = next((o.label for o in d.options if o.chosen), "—")
                status_class = "green" if d.status.value == "DECIDED" else "yellow"
                rows += (
                    f"<tr><td>{d.id}</td><td>{d.title}</td>"
                    f"<td><span class='badge {status_class}'>{d.status.value}</span></td>"
                    f"<td>{chosen}</td><td>{d.decided_by or '—'}</td>"
                    f"<td>{d.rationale or '—'}</td></tr>"
                )
            return self._html_wrap("Decision Log", f"""
<h1>Decision Log</h1>
<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>

<table>
<tr><th>ID</th><th>Title</th><th>Status</th><th>Chosen</th><th>Decided By</th><th>Rationale</th></tr>
{rows}
</table>
{self._footer()}""")
        else:
            return log.summary()

    # ── Milestone Plan ────────────────────────────────────────

    def render_milestone_plan(self, program_id: str, fmt: str = "html") -> str:
        """Render the milestone plan."""
        from lltpm.models.milestone import MilestonePlan
        plan = self._store.load(program_id, "milestone_plan", MilestonePlan)
        if not plan:
            return self._empty("No milestone plan found", fmt)

        if fmt == "html":
            rows = ""
            for m in plan.milestones:
                status_class = {
                    "COMPLETED": "green", "IN_PROGRESS": "blue",
                    "AT_RISK": "yellow", "MISSED": "red",
                }.get(m.status.value, "gray")
                cp = " (critical path)" if m.on_critical_path else ""
                rows += (
                    f"<tr><td>{m.id}</td><td>{m.name}{cp}</td>"
                    f"<td><span class='badge {status_class}'>{m.status.value}</span></td>"
                    f"<td>{m.target_date.isoformat() if m.target_date else '—'}</td>"
                    f"<td>{m.owner}</td>"
                    f"<td>{', '.join(m.dependencies) if m.dependencies else '—'}</td></tr>"
                )
            return self._html_wrap("Milestone Plan", f"""
<h1>Milestone Plan</h1>
<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
<p>Critical path items: {len(plan.critical_path)} | At risk: {len(plan.at_risk)} | Overdue: {len(plan.overdue)}</p>

<table>
<tr><th>ID</th><th>Milestone</th><th>Status</th><th>Target</th><th>Owner</th><th>Dependencies</th></tr>
{rows}
</table>
{self._footer()}""")
        else:
            return plan.summary()

    # ── Launch Checklist ──────────────────────────────────────

    def render_launch_checklist(self, program_id: str, fmt: str = "html") -> str:
        """Render the launch readiness checklist."""
        from lltpm.artifacts.launch_checklist import LaunchChecklistManager
        mgr = LaunchChecklistManager(self._store)
        checklist = mgr.get_checklist(program_id)
        if not checklist:
            return self._empty("No launch checklist found", fmt)

        if fmt == "html":
            status_map = {
                "PASSED": "green", "FAILED": "red",
                "IN_PROGRESS": "blue", "WAIVED": "yellow",
                "NOT_STARTED": "gray",
            }
            rows = ""
            for item in checklist.items:
                sc = status_map.get(item.status.value, "gray")
                rows += (
                    f"<tr><td>{item.category}</td><td>{item.name}</td>"
                    f"<td><span class='badge {sc}'>{item.status.value}</span></td>"
                    f"<td>{item.owner}</td><td>{item.notes or '—'}</td></tr>"
                )
            ready_badge = "green" if checklist.ready else "red"
            ready_text = "READY" if checklist.ready else "NOT READY"
            return self._html_wrap("Launch Readiness", f"""
<h1>Launch Readiness Checklist</h1>
<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
<p>Status: <span class="badge {ready_badge}">{ready_text}</span>
   — Blockers: {len(checklist.blockers)}</p>

<table>
<tr><th>Category</th><th>Check</th><th>Status</th><th>Owner</th><th>Notes</th></tr>
{rows}
</table>

{"<h2>Rollback Plan</h2><p>" + checklist.rollback_plan + "</p>" if checklist.rollback_plan else ""}
{self._footer()}""")
        else:
            return mgr.get_summary(program_id)

    # ── Action Tracker ────────────────────────────────────────

    def render_action_tracker(self, program_id: str, fmt: str = "html") -> str:
        """Render the action item tracker."""
        from lltpm.artifacts.meeting_notes import MeetingNotesManager
        mgr = MeetingNotesManager(self._store)
        open_items = mgr.get_open_items(program_id)
        overdue = mgr.get_overdue_items(program_id)

        if fmt == "html":
            rows = ""
            for item in open_items:
                overdue_class = "red" if item.is_overdue else ""
                status_class = {"OPEN": "blue", "IN_PROGRESS": "yellow", "BLOCKED": "red"}.get(
                    item.status.value, "gray"
                )
                rows += (
                    f"<tr class='{overdue_class}'><td>{item.id}</td>"
                    f"<td>{item.description}</td>"
                    f"<td>{item.owner}</td>"
                    f"<td>{item.due_date.isoformat() if item.due_date else '—'}</td>"
                    f"<td><span class='badge {status_class}'>{item.status.value}</span></td>"
                    f"<td>{item.source or '—'}</td></tr>"
                )
            return self._html_wrap("Action Tracker", f"""
<h1>Action Tracker</h1>
<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
<p>Open: {len(open_items)} | Overdue: {len(overdue)}</p>

<table>
<tr><th>ID</th><th>Action</th><th>Owner</th><th>Due</th><th>Status</th><th>Source</th></tr>
{rows}
</table>
{self._footer()}""")
        else:
            return mgr.get_action_summary(program_id)

    # ── Delegation Plan ───────────────────────────────────────

    def render_delegation_plan(self, program_id: str, fmt: str = "html") -> str:
        """Render the delegation plan."""
        from lltpm.workflows.delegation_engine import DelegationEngine
        engine = DelegationEngine(self._store)
        summary = engine.get_summary(program_id)

        if fmt == "html":
            return self._html_wrap("Delegation Plan", f"""
<h1>Delegation Plan</h1>
<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
<pre>{summary}</pre>
{self._footer()}""")
        else:
            return summary

    # ── Combined Program Report ───────────────────────────────

    def render_full_report(self, program_id: str, fmt: str = "html") -> str:
        """Render a comprehensive program report combining all artifacts."""
        sections = [
            ("Program Brief", self.render_program_brief(program_id, "markdown")),
            ("Status Update", self.render_status_update(program_id, "markdown")),
            ("Milestone Plan", self.render_milestone_plan(program_id, "markdown")),
            ("RAID Log", self.render_raid_log(program_id, "markdown")),
            ("Decision Log", self.render_decision_log(program_id, "markdown")),
            ("Action Tracker", self.render_action_tracker(program_id, "markdown")),
            ("Delegation Plan", self.render_delegation_plan(program_id, "markdown")),
        ]

        if fmt == "html":
            body = ""
            for title, content in sections:
                if "not found" not in content.lower() and "no " not in content[:10].lower():
                    body += f"<h2>{title}</h2>\n<pre>{content}</pre>\n<hr>\n"
            return self._html_wrap(f"Full Program Report — {program_id}", f"""
<h1>Program Report: {program_id}</h1>
<p class="meta">Comprehensive report generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
{body}
{self._footer()}""")
        else:
            parts = []
            for title, content in sections:
                if "not found" not in content.lower() and "no " not in content[:10].lower():
                    parts.append(f"---\n# {title}\n\n{content}")
            return "\n\n".join(parts) if parts else "No artifacts found for this program."

    # ── List available documents ──────────────────────────────

    def list_renderable(self, program_id: str) -> list[dict[str, str]]:
        """List all document types available for rendering."""
        artifacts = self._store.list_artifacts(program_id)
        available = []
        doc_map = {
            "program": ("Program One-Pager", "render_program_brief"),
            "raid_log": ("RAID Log", "render_raid_log"),
            "decision_log": ("Decision Log", "render_decision_log"),
            "milestone_plan": ("Milestone Plan", "render_milestone_plan"),
            "launch_checklist": ("Launch Checklist", "render_launch_checklist"),
            "action_tracker": ("Action Tracker", "render_action_tracker"),
        }
        for artifact_name, (doc_name, method) in doc_map.items():
            if artifact_name in artifacts:
                available.append({
                    "name": doc_name,
                    "artifact": artifact_name,
                    "method": method,
                    "formats": ["html", "markdown"],
                })
        # Status updates use date-based naming
        status = [a for a in artifacts if a.startswith("status_")]
        if status:
            available.append({
                "name": "Status Update (latest)",
                "artifact": status[-1],
                "method": "render_status_update",
                "formats": ["html", "markdown"],
            })
        # Full report is always available
        available.append({
            "name": "Full Program Report",
            "artifact": "all",
            "method": "render_full_report",
            "formats": ["html", "markdown"],
        })
        return available

    # ── HTML helpers ──────────────────────────────────────────

    def _html_wrap(self, title: str, body: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — LLTPM</title>
  {_DOCUMENT_CSS}
</head>
<body>
{body}
</body>
</html>"""

    def _html_list(self, items: list[str]) -> str:
        if not items:
            return "<p class='section-note'>None</p>"
        return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"

    def _footer(self) -> str:
        return f'<div class="footer">Generated by LLTPM — AI Technical Program Manager | {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}</div>'

    def _empty(self, message: str, fmt: str) -> str:
        if fmt == "html":
            return self._html_wrap("Not Found", f"<p>{message}</p>")
        return message
