"""Slack Bot — bidirectional, interactive Slack bot for LLTPM.

Provides:
- Event-driven message handling (Socket Mode or HTTP events)
- Slash command processing (/lltpm status, /lltpm risk, etc.)
- Interactive components (buttons, modals, select menus)
- Threaded conversation context tracking
- Rich Block Kit message formatting
- Approval workflow integration (approve/reject buttons)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from lltpm.config import IntegrationConfig


class SlackCommandType(str, Enum):
    """Recognized slash command sub-commands."""
    STATUS = "status"
    RISK = "risk"
    APPROVE = "approve"
    NUDGE = "nudge"
    DIGEST = "digest"
    HELP = "help"
    DELEGATE = "delegate"
    ESCALATE = "escalate"
    MEETING = "meeting"
    SCOPE = "scope"


@dataclass
class SlackConversation:
    """Tracks a threaded conversation with context."""
    channel_id: str
    thread_ts: str
    user_id: str
    program_id: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def add_message(self, role: str, text: str) -> None:
        self.messages.append({"role": role, "text": text})
        self.last_activity = time.time()

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.last_activity) > 3600  # 1 hour


@dataclass
class SlackAction:
    """An interactive action from a Slack component."""
    action_id: str
    action_type: str  # button_click, menu_select, modal_submit
    user_id: str
    channel_id: str
    value: str = ""
    thread_ts: str = ""
    trigger_id: str = ""  # for opening modals
    metadata: dict[str, Any] = field(default_factory=dict)


class SlackBot:
    """Bidirectional Slack bot for LLTPM agent interaction.

    Handles incoming events and commands, routes them through the TPM agent,
    and sends formatted responses back to Slack.
    """

    def __init__(self, config: IntegrationConfig) -> None:
        self._config = config
        self._bot_token = config.api_key
        self._app_token = config.extra.get("app_token", "")
        self._signing_secret = config.extra.get("signing_secret", "")
        self._bot_user_id: str = ""

        # Conversation tracking
        self._conversations: dict[str, SlackConversation] = {}

        # Command handlers
        self._command_handlers: dict[str, Callable[..., dict[str, Any]]] = {}

        # Action handlers (for interactive components)
        self._action_handlers: dict[str, Callable[..., dict[str, Any]]] = {}

        # Register built-in command handlers
        self._register_defaults()

    @property
    def name(self) -> str:
        return "slack_bot"

    def _register_defaults(self) -> None:
        """Register default command and action handlers."""
        self._command_handlers["help"] = self._handle_help
        self._command_handlers["status"] = self._handle_status_command
        self._command_handlers["risk"] = self._handle_risk_command
        self._command_handlers["approve"] = self._handle_approve_command
        self._command_handlers["digest"] = self._handle_digest_command

        self._action_handlers["approve_request"] = self._handle_approve_action
        self._action_handlers["reject_request"] = self._handle_reject_action
        self._action_handlers["snooze_nudge"] = self._handle_snooze_action
        self._action_handlers["delegate_task"] = self._handle_delegate_action

    # ── Event Processing ─────────────────────────────────────

    def handle_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Process an incoming Slack event.

        Handles:
        - message events (DM and channel mentions)
        - app_mention events
        - slash commands
        - interactive component callbacks
        """
        event_type = event.get("type", "")

        if event_type == "url_verification":
            return {"challenge": event.get("challenge", "")}

        inner_event = event.get("event", event)
        inner_type = inner_event.get("type", "")

        if inner_type == "app_mention":
            return self._handle_mention(inner_event)
        elif inner_type == "message":
            # Only respond to DMs or threads we're in
            channel_type = inner_event.get("channel_type", "")
            if channel_type == "im":
                return self._handle_dm(inner_event)
            elif inner_event.get("thread_ts") in self._conversations:
                return self._handle_thread_reply(inner_event)
            return {"ok": True, "action": "ignored"}

        return {"ok": True, "action": "unhandled", "type": inner_type}

    def handle_slash_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process a /lltpm slash command.

        Payload fields: command, text, user_id, channel_id, trigger_id
        """
        text = payload.get("text", "").strip()
        parts = text.split(None, 1)
        subcommand = parts[0].lower() if parts else "help"
        args = parts[1] if len(parts) > 1 else ""

        handler = self._command_handlers.get(subcommand)
        if handler:
            return handler(
                user_id=payload.get("user_id", ""),
                channel_id=payload.get("channel_id", ""),
                args=args,
                trigger_id=payload.get("trigger_id", ""),
            )

        return {
            "response_type": "ephemeral",
            "text": f"Unknown command: `{subcommand}`. Try `/lltpm help`.",
        }

    def handle_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process an interactive component callback (buttons, menus, modals)."""
        interaction_type = payload.get("type", "")

        if interaction_type == "block_actions":
            return self._handle_block_actions(payload)
        elif interaction_type == "view_submission":
            return self._handle_modal_submit(payload)

        return {"ok": True}

    # ── Message Handlers ─────────────────────────────────────

    def _handle_mention(self, event: dict[str, Any]) -> dict[str, Any]:
        """Handle @lltpm mentions in channels."""
        text = event.get("text", "")
        user_id = event.get("user", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", event.get("ts", ""))

        # Strip the bot mention
        clean_text = re.sub(r"<@\w+>", "", text).strip()

        # Track conversation
        conv = self._get_or_create_conversation(channel, thread_ts, user_id)
        conv.add_message("user", clean_text)

        return {
            "ok": True,
            "action": "mention",
            "text": clean_text,
            "user_id": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "conversation_context": [m for m in conv.messages[-10:]],
        }

    def _handle_dm(self, event: dict[str, Any]) -> dict[str, Any]:
        """Handle direct messages to the bot."""
        text = event.get("text", "")
        user_id = event.get("user", "")
        channel = event.get("channel", "")

        # Ignore bot's own messages
        if event.get("bot_id") or user_id == self._bot_user_id:
            return {"ok": True, "action": "ignored"}

        thread_ts = event.get("thread_ts", event.get("ts", ""))
        conv = self._get_or_create_conversation(channel, thread_ts, user_id)
        conv.add_message("user", text)

        return {
            "ok": True,
            "action": "dm",
            "text": text,
            "user_id": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "conversation_context": [m for m in conv.messages[-10:]],
        }

    def _handle_thread_reply(self, event: dict[str, Any]) -> dict[str, Any]:
        """Handle replies in threads the bot is participating in."""
        thread_ts = event.get("thread_ts", "")
        text = event.get("text", "")
        user_id = event.get("user", "")
        channel = event.get("channel", "")

        conv = self._conversations.get(thread_ts)
        if conv:
            conv.add_message("user", text)

        return {
            "ok": True,
            "action": "thread_reply",
            "text": text,
            "user_id": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "conversation_context": [m for m in conv.messages[-10:]] if conv else [],
        }

    # ── Command Handlers ─────────────────────────────────────

    def _handle_help(self, **kwargs) -> dict[str, Any]:
        """Return help text with available commands."""
        return {
            "response_type": "ephemeral",
            "blocks": self.build_help_blocks(),
        }

    def _handle_status_command(
        self, user_id: str = "", channel_id: str = "", args: str = "", **kwargs,
    ) -> dict[str, Any]:
        """Handle /lltpm status [program_id]."""
        program_id = args.strip() if args else ""
        return {
            "response_type": "in_channel",
            "action": "status_request",
            "program_id": program_id,
            "user_id": user_id,
            "channel_id": channel_id,
        }

    def _handle_risk_command(
        self, user_id: str = "", channel_id: str = "", args: str = "", **kwargs,
    ) -> dict[str, Any]:
        """Handle /lltpm risk [program_id]."""
        program_id = args.strip() if args else ""
        return {
            "response_type": "in_channel",
            "action": "risk_request",
            "program_id": program_id,
            "user_id": user_id,
            "channel_id": channel_id,
        }

    def _handle_approve_command(
        self, user_id: str = "", channel_id: str = "", args: str = "", **kwargs,
    ) -> dict[str, Any]:
        """Handle /lltpm approve — show pending approvals."""
        return {
            "response_type": "ephemeral",
            "action": "pending_approvals",
            "user_id": user_id,
            "channel_id": channel_id,
        }

    def _handle_digest_command(
        self, user_id: str = "", channel_id: str = "", args: str = "", **kwargs,
    ) -> dict[str, Any]:
        """Handle /lltpm digest — generate and post a digest."""
        return {
            "response_type": "in_channel",
            "action": "digest_request",
            "user_id": user_id,
            "channel_id": channel_id,
            "program_id": args.strip() if args else "",
        }

    # ── Interactive Action Handlers ──────────────────────────

    def _handle_block_actions(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Route block action callbacks to registered handlers."""
        actions = payload.get("actions", [])
        results = []

        for action in actions:
            action_id = action.get("action_id", "")
            handler = self._action_handlers.get(action_id)

            slack_action = SlackAction(
                action_id=action_id,
                action_type="button_click",
                user_id=payload.get("user", {}).get("id", ""),
                channel_id=payload.get("channel", {}).get("id", ""),
                value=action.get("value", ""),
                trigger_id=payload.get("trigger_id", ""),
                metadata=action.get("block_id", ""),
            )

            if handler:
                results.append(handler(slack_action))
            else:
                results.append({"ok": True, "action": "unhandled", "action_id": action_id})

        return {"ok": True, "results": results}

    def _handle_modal_submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle modal form submissions."""
        view = payload.get("view", {})
        callback_id = view.get("callback_id", "")
        values = view.get("state", {}).get("values", {})

        return {
            "ok": True,
            "action": "modal_submit",
            "callback_id": callback_id,
            "values": values,
            "user_id": payload.get("user", {}).get("id", ""),
        }

    def _handle_approve_action(self, action: SlackAction) -> dict[str, Any]:
        """Handle approval button click."""
        return {
            "ok": True,
            "action": "approve",
            "request_id": action.value,
            "user_id": action.user_id,
            "channel_id": action.channel_id,
        }

    def _handle_reject_action(self, action: SlackAction) -> dict[str, Any]:
        """Handle rejection button click."""
        return {
            "ok": True,
            "action": "reject",
            "request_id": action.value,
            "user_id": action.user_id,
            "channel_id": action.channel_id,
        }

    def _handle_snooze_action(self, action: SlackAction) -> dict[str, Any]:
        """Handle nudge snooze button."""
        return {
            "ok": True,
            "action": "snooze",
            "nudge_id": action.value,
            "user_id": action.user_id,
        }

    def _handle_delegate_action(self, action: SlackAction) -> dict[str, Any]:
        """Handle delegate button click."""
        return {
            "ok": True,
            "action": "delegate",
            "task_id": action.value,
            "user_id": action.user_id,
            "trigger_id": action.trigger_id,
        }

    # ── Conversation Management ──────────────────────────────

    def _get_or_create_conversation(
        self, channel: str, thread_ts: str, user_id: str,
    ) -> SlackConversation:
        """Get existing conversation or create a new one."""
        if thread_ts in self._conversations:
            conv = self._conversations[thread_ts]
            if not conv.is_stale:
                return conv

        conv = SlackConversation(
            channel_id=channel,
            thread_ts=thread_ts,
            user_id=user_id,
        )
        self._conversations[thread_ts] = conv
        return conv

    def get_conversation(self, thread_ts: str) -> SlackConversation | None:
        """Retrieve a conversation by thread timestamp."""
        return self._conversations.get(thread_ts)

    def set_conversation_program(self, thread_ts: str, program_id: str) -> None:
        """Associate a conversation with a program."""
        conv = self._conversations.get(thread_ts)
        if conv:
            conv.program_id = program_id

    def add_bot_response(self, thread_ts: str, text: str) -> None:
        """Record the bot's response in conversation history."""
        conv = self._conversations.get(thread_ts)
        if conv:
            conv.add_message("assistant", text)

    def cleanup_stale_conversations(self) -> int:
        """Remove stale conversations. Returns number removed."""
        stale = [ts for ts, c in self._conversations.items() if c.is_stale]
        for ts in stale:
            del self._conversations[ts]
        return len(stale)

    # ── Message Sending ──────────────────────────────────────

    def send_message(
        self,
        channel: str,
        text: str,
        thread_ts: str = "",
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a message to Slack. Returns API response dict."""
        try:
            client = self._get_client()
            kwargs: dict[str, Any] = {"channel": channel, "text": text}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            if blocks:
                kwargs["blocks"] = blocks
            resp = client.chat_postMessage(**kwargs)
            return {"ok": True, "ts": resp["ts"], "channel": channel}
        except ImportError:
            return {"ok": False, "error": "slack_sdk not installed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send_ephemeral(
        self, channel: str, user: str, text: str, blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send an ephemeral message visible only to one user."""
        try:
            client = self._get_client()
            kwargs: dict[str, Any] = {"channel": channel, "user": user, "text": text}
            if blocks:
                kwargs["blocks"] = blocks
            resp = client.chat_postEphemeral(**kwargs)
            return {"ok": True, "message_ts": resp.get("message_ts", "")}
        except ImportError:
            return {"ok": False, "error": "slack_sdk not installed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def update_message(
        self, channel: str, ts: str, text: str, blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update an existing message."""
        try:
            client = self._get_client()
            kwargs: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
            if blocks:
                kwargs["blocks"] = blocks
            resp = client.chat_update(**kwargs)
            return {"ok": True, "ts": resp["ts"]}
        except ImportError:
            return {"ok": False, "error": "slack_sdk not installed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_modal(self, trigger_id: str, view: dict[str, Any]) -> dict[str, Any]:
        """Open a modal dialog."""
        try:
            client = self._get_client()
            resp = client.views_open(trigger_id=trigger_id, view=view)
            return {"ok": True, "view_id": resp["view"]["id"]}
        except ImportError:
            return {"ok": False, "error": "slack_sdk not installed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Block Kit Builders ───────────────────────────────────

    @staticmethod
    def build_help_blocks() -> list[dict[str, Any]]:
        """Build Block Kit blocks for help message."""
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "LLTPM — AI Technical Program Manager"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Available Commands:*\n"
                        "• `/lltpm status [program]` — Get program status\n"
                        "• `/lltpm risk [program]` — View risk summary\n"
                        "• `/lltpm approve` — See pending approvals\n"
                        "• `/lltpm digest [program]` — Generate status digest\n"
                        "• `/lltpm nudge @user` — Send a follow-up nudge\n"
                        "• `/lltpm delegate @user task` — Delegate a task\n"
                        "• `/lltpm escalate issue` — Escalate a blocker\n"
                        "• `/lltpm meeting [topic]` — Meeting gate check\n"
                        "• `/lltpm scope [program]` — View scope status\n"
                        "• `/lltpm help` — Show this help"
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "You can also DM me or @mention me in any channel for conversational interaction.",
                    }
                ],
            },
        ]

    @staticmethod
    def build_status_blocks(
        program_id: str,
        health: str,
        milestones: str,
        risks: str,
        blockers: str,
    ) -> list[dict[str, Any]]:
        """Build Block Kit blocks for program status display."""
        health_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(
            health.lower(), "⚪"
        )

        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Program Status: {program_id}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Health:* {health_emoji} {health}"},
                    {"type": "mrkdwn", "text": f"*Milestones:* {milestones}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Risks:*\n{risks}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Blockers:*\n{blockers}"},
            },
        ]

    @staticmethod
    def build_approval_blocks(
        request_id: str,
        title: str,
        requester: str,
        description: str,
        step_info: str,
    ) -> list[dict[str, Any]]:
        """Build Block Kit blocks for an approval request with action buttons."""
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Approval Required: {title}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Requested by:* {requester}\n"
                        f"*Step:* {step_info}\n\n"
                        f"{description}"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": f"approval_{request_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "approve_request",
                        "value": request_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": "reject_request",
                        "value": request_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Delegate"},
                        "action_id": "delegate_task",
                        "value": request_id,
                    },
                ],
            },
        ]

    @staticmethod
    def build_nudge_blocks(
        nudge_id: str,
        owner: str,
        topic: str,
        urgency: str,
        days_pending: int,
    ) -> list[dict[str, Any]]:
        """Build Block Kit blocks for a follow-up nudge."""
        urgency_indicator = {"low": "📋", "medium": "⚠️", "high": "🔥"}.get(
            urgency.lower(), "📋"
        )

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{urgency_indicator} *Follow-up needed:* {topic}\n"
                        f"*Owner:* <@{owner}> • *Pending:* {days_pending} days"
                    ),
                },
            },
            {
                "type": "actions",
                "block_id": f"nudge_{nudge_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Done"},
                        "style": "primary",
                        "action_id": "approve_request",
                        "value": nudge_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Snooze 24h"},
                        "action_id": "snooze_nudge",
                        "value": nudge_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Delegate"},
                        "action_id": "delegate_task",
                        "value": nudge_id,
                    },
                ],
            },
        ]

    @staticmethod
    def build_risk_blocks(
        program_id: str,
        risks: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Build Block Kit blocks for risk display."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Risk Summary: {program_id}"},
            },
        ]

        for risk in risks[:10]:  # Slack limit
            severity = risk.get("severity", "medium")
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
                severity.lower(), "⚪"
            )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{icon} *{risk.get('title', 'Untitled')}*\n"
                        f"_{risk.get('description', '')}_\n"
                        f"Owner: {risk.get('owner', 'Unassigned')} • "
                        f"Mitigation: {risk.get('mitigation', 'None')}"
                    ),
                },
            })

        if not risks:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_No active risks._"},
            })

        return blocks

    @staticmethod
    def build_delegation_modal(
        task_id: str,
        task_title: str,
        team_members: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Build a modal for task delegation."""
        options = [
            {
                "text": {"type": "plain_text", "text": f"{m['name']} ({m.get('role', '')})"},
                "value": m.get("id", m["name"]),
            }
            for m in team_members[:100]  # Slack limit
        ]

        return {
            "type": "modal",
            "callback_id": "delegate_modal",
            "title": {"type": "plain_text", "text": "Delegate Task"},
            "submit": {"type": "plain_text", "text": "Delegate"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": json.dumps({"task_id": task_id}),
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Task:* {task_title}",
                    },
                },
                {
                    "type": "input",
                    "block_id": "delegate_to",
                    "element": {
                        "type": "static_select",
                        "action_id": "selected_member",
                        "placeholder": {"type": "plain_text", "text": "Choose a team member"},
                        "options": options,
                    },
                    "label": {"type": "plain_text", "text": "Delegate to"},
                },
                {
                    "type": "input",
                    "block_id": "delegate_notes",
                    "optional": True,
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "notes",
                        "multiline": True,
                        "placeholder": {"type": "plain_text", "text": "Additional context..."},
                    },
                    "label": {"type": "plain_text", "text": "Notes"},
                },
            ],
        }

    # ── Registration ─────────────────────────────────────────

    def register_command(self, command: str, handler: Callable[..., dict[str, Any]]) -> None:
        """Register a custom slash command handler."""
        self._command_handlers[command.lower()] = handler

    def register_action(self, action_id: str, handler: Callable[..., dict[str, Any]]) -> None:
        """Register a custom interactive action handler."""
        self._action_handlers[action_id] = handler

    # ── Summary ──────────────────────────────────────────────

    def get_summary(self) -> str:
        """Get a summary of bot state."""
        active = sum(1 for c in self._conversations.values() if not c.is_stale)
        stale = sum(1 for c in self._conversations.values() if c.is_stale)

        lines = [
            "## Slack Bot Status",
            f"- Active conversations: {active}",
            f"- Stale conversations: {stale}",
            f"- Registered commands: {len(self._command_handlers)}",
            f"- Registered actions: {len(self._action_handlers)}",
        ]
        return "\n".join(lines)

    # ── Internal ─────────────────────────────────────────────

    def _get_client(self):
        from slack_sdk import WebClient
        return WebClient(token=self._bot_token)
