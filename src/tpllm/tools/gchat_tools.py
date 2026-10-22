"""Google Chat tools — messaging via Google Chat (requires Google Chat integration)."""

from __future__ import annotations

import json
from typing import Any

from tpllm.config import TPLLMConfig
from tpllm.integrations.gchat_integration import GoogleChatIntegration


def get_gchat_tools(config: TPLLMConfig) -> list[dict[str, Any]]:
    gchat = GoogleChatIntegration(config.gchat)

    def send_gchat_message(space: str, text: str, thread_key: str = "") -> str:
        if gchat._is_webhook_mode:
            result = gchat.send_webhook_message(text)
        else:
            result = gchat.send_message(space, text, thread_key)
        return json.dumps(result)

    def send_gchat_update(space: str, title: str, sections: list[dict[str, str]], thread_key: str = "") -> str:
        result = gchat.send_structured_update(space, title, sections, thread_key)
        return json.dumps(result)

    def request_gchat_update(space: str, owner: str, topic: str, deadline: str, thread_key: str = "") -> str:
        result = gchat.request_update(space, owner, topic, deadline, thread_key)
        return json.dumps(result)

    def list_gchat_spaces() -> str:
        spaces = gchat.list_spaces()
        return json.dumps(spaces)

    return [
        {
            "name": "send_gchat_message",
            "description": "Send a message to a Google Chat space. In webhook mode, only text is used.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "space": {
                        "type": "string",
                        "description": "Space resource name (e.g. 'spaces/AAAA...') or ignored in webhook mode",
                    },
                    "text": {"type": "string", "description": "Message text"},
                    "thread_key": {
                        "type": "string",
                        "description": "Optional thread key for threading replies",
                    },
                },
                "required": ["space", "text"],
            },
            "executor": send_gchat_message,
        },
        {
            "name": "send_gchat_update",
            "description": (
                "Send a structured TPM status update to Google Chat as a rich card. "
                "Includes title and multiple sections."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "space": {"type": "string", "description": "Space resource name or webhook URL"},
                    "title": {"type": "string", "description": "Card title"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "header": {"type": "string"},
                                "text": {"type": "string"},
                            },
                            "required": ["header", "text"],
                        },
                        "description": "Sections of the update card",
                    },
                    "thread_key": {"type": "string"},
                },
                "required": ["space", "title", "sections"],
            },
            "executor": send_gchat_update,
        },
        {
            "name": "request_gchat_update",
            "description": (
                "Send a structured async update request to a team member via Google Chat. "
                "Uses the standard format: Yesterday / Today / Blockers / ETA confidence."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "space": {"type": "string"},
                    "owner": {"type": "string", "description": "Person to request update from"},
                    "topic": {"type": "string", "description": "What to update on"},
                    "deadline": {"type": "string", "description": "When the update is needed by"},
                    "thread_key": {"type": "string"},
                },
                "required": ["space", "owner", "topic", "deadline"],
            },
            "executor": request_gchat_update,
        },
        {
            "name": "list_gchat_spaces",
            "description": "List available Google Chat spaces the bot has access to.",
            "input_schema": {"type": "object", "properties": {}},
            "executor": list_gchat_spaces,
        },
    ]
