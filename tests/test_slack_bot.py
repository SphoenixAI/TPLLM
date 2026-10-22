"""Tests for Slack Bot interface."""

from __future__ import annotations

import time

from tpllm.config import IntegrationConfig
from tpllm.integrations.slack_bot import SlackBot, SlackAction, SlackConversation


def _make_config():
    return IntegrationConfig(
        enabled=True,
        api_key="xoxb-test-token",
        extra={
            "app_token": "xapp-test-token",
            "signing_secret": "test-secret",
        },
    )


def _make_bot():
    return SlackBot(_make_config())


# ── Event Processing ──────────────────────────────────────


def test_url_verification():
    bot = _make_bot()
    result = bot.handle_event({
        "type": "url_verification",
        "challenge": "abc123",
    })
    assert result["challenge"] == "abc123"


def test_handle_app_mention():
    bot = _make_bot()
    result = bot.handle_event({
        "event": {
            "type": "app_mention",
            "text": "<@U123> what's the status of project-alpha?",
            "user": "U456",
            "channel": "C789",
            "ts": "1234567890.123456",
        },
    })

    assert result["action"] == "mention"
    assert "status of project-alpha" in result["text"]
    assert result["user_id"] == "U456"
    assert result["channel"] == "C789"


def test_handle_dm():
    bot = _make_bot()
    result = bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "im",
            "text": "Show me risks for beta-launch",
            "user": "U456",
            "channel": "D789",
            "ts": "1234567890.123456",
        },
    })

    assert result["action"] == "dm"
    assert "risks for beta-launch" in result["text"]


def test_ignore_bot_message():
    bot = _make_bot()
    result = bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "im",
            "text": "Bot response",
            "user": "U456",
            "channel": "D789",
            "bot_id": "B123",
            "ts": "1234567890.123456",
        },
    })

    assert result["action"] == "ignored"


def test_handle_thread_reply():
    bot = _make_bot()
    # First, create a conversation via a DM
    bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "im",
            "text": "Initial message",
            "user": "U456",
            "channel": "D789",
            "ts": "1234567890.123456",
        },
    })

    # Now reply in that thread
    result = bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "channel",
            "text": "Follow-up",
            "user": "U456",
            "channel": "D789",
            "thread_ts": "1234567890.123456",
            "ts": "1234567891.000000",
        },
    })

    assert result["action"] == "thread_reply"
    assert len(result["conversation_context"]) == 2


def test_unhandled_event():
    bot = _make_bot()
    result = bot.handle_event({
        "event": {
            "type": "channel_created",
        },
    })
    assert result["action"] == "unhandled"


# ── Slash Commands ────────────────────────────────────────


def test_slash_help():
    bot = _make_bot()
    result = bot.handle_slash_command({
        "text": "help",
        "user_id": "U456",
        "channel_id": "C789",
    })

    assert result["response_type"] == "ephemeral"
    assert "blocks" in result


def test_slash_status():
    bot = _make_bot()
    result = bot.handle_slash_command({
        "text": "status project-alpha",
        "user_id": "U456",
        "channel_id": "C789",
    })

    assert result["action"] == "status_request"
    assert result["program_id"] == "project-alpha"


def test_slash_risk():
    bot = _make_bot()
    result = bot.handle_slash_command({
        "text": "risk beta-launch",
        "user_id": "U456",
        "channel_id": "C789",
    })

    assert result["action"] == "risk_request"
    assert result["program_id"] == "beta-launch"


def test_slash_approve():
    bot = _make_bot()
    result = bot.handle_slash_command({
        "text": "approve",
        "user_id": "U456",
        "channel_id": "C789",
    })

    assert result["action"] == "pending_approvals"


def test_slash_digest():
    bot = _make_bot()
    result = bot.handle_slash_command({
        "text": "digest my-program",
        "user_id": "U456",
        "channel_id": "C789",
    })

    assert result["action"] == "digest_request"
    assert result["program_id"] == "my-program"


def test_slash_unknown():
    bot = _make_bot()
    result = bot.handle_slash_command({
        "text": "foobar",
        "user_id": "U456",
        "channel_id": "C789",
    })

    assert "Unknown command" in result["text"]


def test_slash_empty():
    bot = _make_bot()
    result = bot.handle_slash_command({
        "text": "",
        "user_id": "U456",
        "channel_id": "C789",
    })

    # Empty defaults to help
    assert result["response_type"] == "ephemeral"


# ── Interactive Components ────────────────────────────────


def test_handle_approve_action():
    bot = _make_bot()
    result = bot.handle_interaction({
        "type": "block_actions",
        "user": {"id": "U456"},
        "channel": {"id": "C789"},
        "trigger_id": "T123",
        "actions": [
            {
                "action_id": "approve_request",
                "value": "req-001",
                "block_id": "approval_req-001",
            }
        ],
    })

    assert result["ok"] is True
    assert result["results"][0]["action"] == "approve"
    assert result["results"][0]["request_id"] == "req-001"


def test_handle_reject_action():
    bot = _make_bot()
    result = bot.handle_interaction({
        "type": "block_actions",
        "user": {"id": "U456"},
        "channel": {"id": "C789"},
        "trigger_id": "T123",
        "actions": [
            {
                "action_id": "reject_request",
                "value": "req-001",
            }
        ],
    })

    assert result["results"][0]["action"] == "reject"


def test_handle_snooze_action():
    bot = _make_bot()
    result = bot.handle_interaction({
        "type": "block_actions",
        "user": {"id": "U456"},
        "channel": {"id": "C789"},
        "actions": [
            {
                "action_id": "snooze_nudge",
                "value": "nudge-001",
            }
        ],
    })

    assert result["results"][0]["action"] == "snooze"
    assert result["results"][0]["nudge_id"] == "nudge-001"


def test_handle_delegate_action():
    bot = _make_bot()
    result = bot.handle_interaction({
        "type": "block_actions",
        "user": {"id": "U456"},
        "channel": {"id": "C789"},
        "trigger_id": "T999",
        "actions": [
            {
                "action_id": "delegate_task",
                "value": "task-001",
            }
        ],
    })

    assert result["results"][0]["action"] == "delegate"
    assert result["results"][0]["trigger_id"] == "T999"


def test_handle_modal_submit():
    bot = _make_bot()
    result = bot.handle_interaction({
        "type": "view_submission",
        "user": {"id": "U456"},
        "view": {
            "callback_id": "delegate_modal",
            "state": {
                "values": {
                    "delegate_to": {
                        "selected_member": {"selected_option": {"value": "bob"}}
                    }
                }
            },
        },
    })

    assert result["action"] == "modal_submit"
    assert result["callback_id"] == "delegate_modal"


def test_unhandled_action():
    bot = _make_bot()
    result = bot.handle_interaction({
        "type": "block_actions",
        "user": {"id": "U456"},
        "channel": {"id": "C789"},
        "actions": [
            {"action_id": "unknown_action", "value": "x"},
        ],
    })

    assert result["results"][0]["action"] == "unhandled"


# ── Conversation Tracking ─────────────────────────────────


def test_conversation_tracking():
    bot = _make_bot()

    # First message creates conversation
    bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "im",
            "text": "Hello",
            "user": "U456",
            "channel": "D789",
            "ts": "1000.000",
        },
    })

    conv = bot.get_conversation("1000.000")
    assert conv is not None
    assert conv.user_id == "U456"
    assert len(conv.messages) == 1


def test_conversation_program_association():
    bot = _make_bot()

    bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "im",
            "text": "Tell me about project-alpha",
            "user": "U456",
            "channel": "D789",
            "ts": "1000.000",
        },
    })

    bot.set_conversation_program("1000.000", "project-alpha")
    conv = bot.get_conversation("1000.000")
    assert conv.program_id == "project-alpha"


def test_bot_response_tracking():
    bot = _make_bot()

    bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "im",
            "text": "What's the risk?",
            "user": "U456",
            "channel": "D789",
            "ts": "1000.000",
        },
    })

    bot.add_bot_response("1000.000", "There are 3 active risks.")
    conv = bot.get_conversation("1000.000")
    assert len(conv.messages) == 2
    assert conv.messages[1]["role"] == "assistant"


def test_stale_conversation_cleanup():
    bot = _make_bot()

    bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "im",
            "text": "Old message",
            "user": "U456",
            "channel": "D789",
            "ts": "old.000",
        },
    })

    # Make conversation stale by backdating
    conv = bot.get_conversation("old.000")
    conv.last_activity = time.time() - 7200  # 2 hours ago

    removed = bot.cleanup_stale_conversations()
    assert removed == 1
    assert bot.get_conversation("old.000") is None


# ── Block Kit Builders ────────────────────────────────────


def test_build_help_blocks():
    blocks = SlackBot.build_help_blocks()
    assert len(blocks) >= 3
    assert blocks[0]["type"] == "header"
    assert "TPLLM" in blocks[0]["text"]["text"]


def test_build_status_blocks():
    blocks = SlackBot.build_status_blocks(
        "project-alpha", "green", "3/5 done", "1 critical", "None",
    )
    assert blocks[0]["type"] == "header"
    assert "project-alpha" in blocks[0]["text"]["text"]


def test_build_approval_blocks():
    blocks = SlackBot.build_approval_blocks(
        "req-001", "Scope Change", "alice", "Adding OAuth2", "Step 1/2",
    )

    # Find the actions block
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1
    assert len(actions[0]["elements"]) == 3  # approve, reject, delegate


def test_build_nudge_blocks():
    blocks = SlackBot.build_nudge_blocks(
        "nudge-001", "U456", "Design review", "high", 3,
    )

    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1


def test_build_risk_blocks():
    risks = [
        {"title": "API Latency", "severity": "critical", "owner": "Bob", "description": "P99 too high", "mitigation": "Caching"},
        {"title": "Staffing", "severity": "medium", "owner": "Alice", "description": "1 headcount short", "mitigation": "Backfill"},
    ]
    blocks = SlackBot.build_risk_blocks("project-alpha", risks)
    assert blocks[0]["type"] == "header"
    assert len(blocks) == 3  # header + 2 risk sections


def test_build_risk_blocks_empty():
    blocks = SlackBot.build_risk_blocks("project-alpha", [])
    assert "No active risks" in blocks[1]["text"]["text"]


def test_build_delegation_modal():
    modal = SlackBot.build_delegation_modal(
        "task-001",
        "Design API specs",
        [
            {"name": "Alice", "role": "Engineer", "id": "U1"},
            {"name": "Bob", "role": "Designer", "id": "U2"},
        ],
    )

    assert modal["type"] == "modal"
    assert modal["callback_id"] == "delegate_modal"
    assert len(modal["blocks"]) == 3  # task info, select, notes


# ── Registration ──────────────────────────────────────────


def test_custom_command_handler():
    bot = _make_bot()

    def custom_handler(**kwargs):
        return {"action": "custom", "args": kwargs.get("args", "")}

    bot.register_command("deploy", custom_handler)

    result = bot.handle_slash_command({
        "text": "deploy staging",
        "user_id": "U456",
        "channel_id": "C789",
    })

    assert result["action"] == "custom"
    assert result["args"] == "staging"


def test_custom_action_handler():
    bot = _make_bot()

    def custom_action(action):
        return {"action": "custom_button", "value": action.value}

    bot.register_action("custom_button", custom_action)

    result = bot.handle_interaction({
        "type": "block_actions",
        "user": {"id": "U456"},
        "channel": {"id": "C789"},
        "actions": [{"action_id": "custom_button", "value": "test-val"}],
    })

    assert result["results"][0]["action"] == "custom_button"


# ── Summary ───────────────────────────────────────────────


def test_bot_summary():
    bot = _make_bot()

    # Create a conversation
    bot.handle_event({
        "event": {
            "type": "message",
            "channel_type": "im",
            "text": "Hello",
            "user": "U456",
            "channel": "D789",
            "ts": "1000.000",
        },
    })

    summary = bot.get_summary()
    assert "Slack Bot Status" in summary
    assert "Active conversations: 1" in summary


# ── SlackConversation Model ──────────────────────────────


def test_conversation_is_stale():
    conv = SlackConversation(
        channel_id="C123",
        thread_ts="1000.000",
        user_id="U456",
    )
    assert conv.is_stale is False

    conv.last_activity = time.time() - 7200
    assert conv.is_stale is True
