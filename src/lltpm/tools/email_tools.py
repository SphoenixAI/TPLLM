"""Tool definitions for email integration."""

from __future__ import annotations

import json
from typing import Any

from lltpm.artifacts.store import ArtifactStore
from lltpm.config import LLTPMConfig


def get_email_tools(config: LLTPMConfig) -> list[dict[str, Any]]:
    """Return tool definitions for email integration."""
    from lltpm.integrations.email_integration import EmailIntegration

    email = EmailIntegration(config.email)

    def send_email(to: str, subject: str, body: str, html: bool = True) -> str:
        if html:
            result = email.send_email(to, subject, body)
        else:
            result = email.send_email(to, subject, f"<pre>{body}</pre>", text_body=body)
        return json.dumps(result)

    def send_status_digest(
        to: str,
        program_id: str,
        subject: str = "",
    ) -> str:
        """Generate and send a status digest for a program."""
        from lltpm.workflows.document_generator import DocumentGenerator
        store = ArtifactStore(config.ensure_data_dir())
        gen = DocumentGenerator(store)

        sections = []
        status_html = gen.render_status_update(program_id, "html")
        if "not found" not in status_html.lower():
            sections.append({"title": "Status Update", "content": status_html})

        risks_html = gen.render_raid_log(program_id, "markdown")
        if "not found" not in risks_html.lower():
            sections.append({"title": "RAID Summary", "content": risks_html})

        milestones_html = gen.render_milestone_plan(program_id, "markdown")
        if "not found" not in milestones_html.lower():
            sections.append({"title": "Milestones", "content": milestones_html})

        if not sections:
            return json.dumps({"error": "No artifacts found for digest"})

        subj = subject or f"LLTPM Status Digest — {program_id}"
        result = email.send_digest(to, subj, sections)
        return json.dumps(result)

    def send_escalation_email(
        to: str,
        item_title: str,
        context: str,
        urgency: str = "high",
    ) -> str:
        """Send an escalation email for a blocked or overdue item."""
        html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
            <h1 style="color: #991b1b; border-bottom: 2px solid #ef4444;">
                Escalation: {item_title}
            </h1>
            <p style="background: #fef2f2; padding: 12px; border-radius: 4px;">
                <strong>Urgency:</strong> {urgency.upper()}<br>
                <strong>Context:</strong> {context}
            </p>
            <p style="color: #64748b; font-size: 12px; margin-top: 20px;">
                Sent by LLTPM — AI Technical Program Manager
            </p>
        </div>
        """
        result = email.send_email(to, f"[ESCALATION] {item_title}", html)
        return json.dumps(result)

    return [
        {
            "name": "send_email",
            "description": "Send an email to a stakeholder with custom subject and body",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Email body (HTML or text)"},
                    "html": {"type": "boolean", "description": "Whether body is HTML"},
                },
                "required": ["to", "subject", "body"],
            },
            "executor": send_email,
        },
        {
            "name": "send_status_digest",
            "description": "Generate and send a program status digest email with latest artifacts",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "program_id": {"type": "string"},
                    "subject": {"type": "string"},
                },
                "required": ["to", "program_id"],
            },
            "executor": send_status_digest,
        },
        {
            "name": "send_escalation_email",
            "description": "Send an escalation email for a blocked or overdue item",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "item_title": {"type": "string"},
                    "context": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["high", "critical"]},
                },
                "required": ["to", "item_title", "context"],
            },
            "executor": send_escalation_email,
        },
    ]
