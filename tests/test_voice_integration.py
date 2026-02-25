"""Tests for Voice/TTS Integration."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from lltpm.config import IntegrationConfig
from lltpm.integrations.voice_integration import (
    VoiceIntegration,
    VoiceBackend,
    VoiceStyle,
    VoiceConfig,
    SynthesisResult,
)


def _make_config(backend="system", **extra):
    defaults = {
        "backend": backend,
        "voice_id": "",
        "speed": "1.0",
        "pitch": "1.0",
    }
    defaults.update(extra)
    return IntegrationConfig(
        enabled=True,
        api_key="test-key",
        extra=defaults,
    )


def test_voice_integration_name():
    voice = VoiceIntegration(_make_config())
    assert voice.name == "voice"


def test_voice_backend_selection():
    voice = VoiceIntegration(_make_config("piper"))
    assert voice.backend == VoiceBackend.PIPER


def test_synthesis_result_ok():
    result = SynthesisResult(text="Hello", audio_path="/tmp/test.wav", backend="system")
    assert result.ok is True


def test_synthesis_result_error():
    result = SynthesisResult(text="Hello", audio_path="", backend="system", error="Failed")
    assert result.ok is False


def test_synthesis_result_to_dict():
    result = SynthesisResult(text="Hello world", audio_path="/tmp/test.wav", backend="system",
                             duration_seconds=1.5, cached=False)
    d = result.to_dict()
    assert d["text"] == "Hello world"
    assert d["ok"] is True
    assert d["duration_seconds"] == 1.5


def test_synthesis_result_long_text_truncated():
    long_text = "A" * 200
    result = SynthesisResult(text=long_text, audio_path="/tmp/test.wav", backend="system")
    d = result.to_dict()
    assert len(d["text"]) < 200
    assert d["text"].endswith("...")


def test_synthesize_empty_text():
    voice = VoiceIntegration(_make_config())
    result = voice.synthesize("")
    assert not result.ok
    assert "Empty" in result.error


def test_synthesize_empty_whitespace():
    voice = VoiceIntegration(_make_config())
    result = voice.synthesize("   ")
    assert not result.ok


def test_apply_style_briefing():
    voice = VoiceIntegration(_make_config())
    processed = voice._apply_style("Status update. All green. ", VoiceStyle.BRIEFING)
    assert "... " in processed


def test_apply_style_urgent():
    voice = VoiceIntegration(_make_config())
    processed = voice._apply_style("Item is blocked and overdue", VoiceStyle.URGENT)
    assert "BLOCKED" in processed
    assert "OVERDUE" in processed


def test_apply_style_alert():
    voice = VoiceIntegration(_make_config())
    processed = voice._apply_style("Server is down", VoiceStyle.ALERT)
    assert processed.startswith("Attention")


def test_estimate_duration():
    duration = VoiceIntegration._estimate_duration("one two three four five six seven eight nine ten")
    assert duration > 0
    assert duration < 30  # 10 words should be very short


def test_cache_key_deterministic():
    voice = VoiceIntegration(_make_config())
    key1 = voice._cache_key("Hello world", VoiceStyle.NEUTRAL)
    key2 = voice._cache_key("Hello world", VoiceStyle.NEUTRAL)
    assert key1 == key2


def test_cache_key_different_for_different_text():
    voice = VoiceIntegration(_make_config())
    key1 = voice._cache_key("Hello", VoiceStyle.NEUTRAL)
    key2 = voice._cache_key("Goodbye", VoiceStyle.NEUTRAL)
    assert key1 != key2


def test_cache_key_different_for_different_style():
    voice = VoiceIntegration(_make_config())
    key1 = voice._cache_key("Hello", VoiceStyle.NEUTRAL)
    key2 = voice._cache_key("Hello", VoiceStyle.URGENT)
    assert key1 != key2


def test_list_available_backends():
    voice = VoiceIntegration(_make_config())
    backends = voice.list_available_backends()
    assert len(backends) == 4
    names = [b["backend"] for b in backends]
    assert "system" in names
    assert "piper" in names
    assert "kokoro" in names
    assert "elevenlabs" in names


def test_elevenlabs_available_with_key():
    voice = VoiceIntegration(_make_config())
    backends = voice.list_available_backends()
    el = next(b for b in backends if b["backend"] == "elevenlabs")
    assert el["available"] is True  # has api_key


def test_elevenlabs_unavailable_without_key():
    cfg = IntegrationConfig(enabled=True, api_key="", extra={"backend": "system"})
    voice = VoiceIntegration(cfg)
    backends = voice.list_available_backends()
    el = next(b for b in backends if b["backend"] == "elevenlabs")
    assert el["available"] is False


def test_get_summary():
    voice = VoiceIntegration(_make_config())
    summary = voice.get_summary()
    assert "Voice Integration" in summary
    assert "system" in summary


def test_synthesize_system_not_found():
    """System TTS returns error when command not found."""
    voice = VoiceIntegration(_make_config())
    with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
        result = voice._synthesize_system("Hello world", "")
        assert not result.ok
        assert "not found" in result.error


def test_synthesize_piper_not_installed():
    """Piper returns error when not installed."""
    voice = VoiceIntegration(_make_config("piper"))
    with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
        result = voice._synthesize_piper("Hello world", "")
        assert not result.ok
        assert "not installed" in result.error.lower() or "not found" in result.error.lower()


def test_synthesize_elevenlabs_no_key():
    """ElevenLabs returns error without API key."""
    cfg = IntegrationConfig(enabled=True, api_key="", extra={"backend": "elevenlabs"})
    voice = VoiceIntegration(cfg)
    result = voice._synthesize_elevenlabs("Hello", "")
    assert not result.ok
    assert "not configured" in result.error


def test_synthesize_briefing():
    """Briefing synthesis processes sections."""
    voice = VoiceIntegration(_make_config())
    sections = [
        {"title": "Status", "content": "All green"},
        {"title": "Risks", "content": "One item flagged"},
    ]

    with patch.object(voice, "synthesize") as mock_synth:
        mock_synth.return_value = SynthesisResult(
            text="test", audio_path="/tmp/test.wav", backend="system",
        )
        result = voice.synthesize_briefing(sections)
        assert result.ok
        call_text = mock_synth.call_args[0][0]
        assert "Status" in call_text
        assert "Risks" in call_text


def test_synthesize_alert():
    """Alert synthesis includes urgency."""
    voice = VoiceIntegration(_make_config())

    with patch.object(voice, "synthesize") as mock_synth:
        mock_synth.return_value = SynthesisResult(
            text="test", audio_path="/tmp/test.wav", backend="system",
        )
        voice.synthesize_alert("Server Down", "Production is offline", "critical")
        call_args = mock_synth.call_args
        assert call_args[1]["style"] == VoiceStyle.ALERT


def test_synthesis_count_increments():
    """Synthesis count tracks calls."""
    voice = VoiceIntegration(_make_config())
    assert voice._synthesis_count == 0

    with patch.object(voice, "_synthesize_system") as mock:
        mock.return_value = SynthesisResult(
            text="test", audio_path="/tmp/test.wav", backend="system",
        )
        voice.synthesize("Hello")
        assert voice._synthesis_count == 1
        voice.synthesize("World")
        assert voice._synthesis_count == 2


def test_no_config_defaults():
    """VoiceIntegration works with no config (defaults)."""
    voice = VoiceIntegration()
    assert voice.backend == VoiceBackend.SYSTEM
    assert voice.name == "voice"
