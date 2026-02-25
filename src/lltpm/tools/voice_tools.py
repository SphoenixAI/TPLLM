"""Tool definitions for voice/TTS integration."""

from __future__ import annotations

import json
from typing import Any

from lltpm.config import LLTPMConfig


def get_voice_tools(config: LLTPMConfig) -> list[dict[str, Any]]:
    """Return tool definitions for voice synthesis."""
    from lltpm.integrations.voice_integration import VoiceIntegration, VoiceStyle

    voice = VoiceIntegration(config.voice if hasattr(config, "voice") else None)

    def synthesize_speech(text: str, style: str = "neutral") -> str:
        vs = VoiceStyle(style) if style in VoiceStyle.__members__.values() else VoiceStyle.NEUTRAL
        result = voice.synthesize(text, style=vs)
        return json.dumps(result.to_dict())

    def synthesize_briefing(sections_json: str) -> str:
        sections = json.loads(sections_json)
        result = voice.synthesize_briefing(sections)
        return json.dumps(result.to_dict())

    def synthesize_alert(title: str, context: str, urgency: str = "high") -> str:
        result = voice.synthesize_alert(title, context, urgency)
        return json.dumps(result.to_dict())

    def list_voice_backends() -> str:
        backends = voice.list_available_backends()
        return json.dumps(backends)

    def get_voice_summary() -> str:
        return voice.get_summary()

    return [
        {
            "name": "synthesize_speech",
            "description": "Convert text to speech audio using the configured TTS backend",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to synthesize"},
                    "style": {
                        "type": "string",
                        "enum": ["neutral", "urgent", "calm", "briefing", "alert"],
                        "description": "Voice delivery style",
                    },
                },
                "required": ["text"],
            },
            "executor": synthesize_speech,
        },
        {
            "name": "synthesize_briefing",
            "description": "Synthesize a multi-section briefing as speech (daily standup, status update)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "sections_json": {
                        "type": "string",
                        "description": 'JSON array of {"title": "...", "content": "..."} sections',
                    },
                },
                "required": ["sections_json"],
            },
            "executor": synthesize_briefing,
        },
        {
            "name": "synthesize_alert",
            "description": "Synthesize an urgent alert or escalation as speech",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "context": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["high", "critical"]},
                },
                "required": ["title", "context"],
            },
            "executor": synthesize_alert,
        },
        {
            "name": "list_voice_backends",
            "description": "List available TTS backends on this system",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": list_voice_backends,
        },
        {
            "name": "get_voice_summary",
            "description": "Get voice integration status and configuration",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
            "executor": get_voice_summary,
        },
    ]
