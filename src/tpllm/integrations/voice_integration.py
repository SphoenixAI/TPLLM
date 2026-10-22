"""Voice / TTS integration — text-to-speech synthesis for TPLLM.

Supports multiple backends:
- Chatterbox (MIT, Resemble AI — beats ElevenLabs in blind tests, emotion control)
- Piper (local, fast, offline-capable, 900+ voices)
- Kokoro (local, 82M params, Apache 2.0, 8 languages)
- ElevenLabs (cloud API, production-grade voice cloning)
- System TTS (macOS say, espeak on Linux — zero-dependency fallback)

The voice engine converts text into audio suitable for:
- Meeting briefing narration
- Status update voice summaries
- Escalation alerts with voice urgency
- Accessibility audio output
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from tpllm.config import IntegrationConfig


class VoiceBackend(str, Enum):
    CHATTERBOX = "chatterbox"
    PIPER = "piper"
    KOKORO = "kokoro"
    ELEVENLABS = "elevenlabs"
    SYSTEM = "system"


class VoiceStyle(str, Enum):
    """Voice delivery styles for different contexts."""
    NEUTRAL = "neutral"
    URGENT = "urgent"
    CALM = "calm"
    BRIEFING = "briefing"
    ALERT = "alert"


@dataclass
class VoiceConfig:
    """Configuration for voice synthesis."""
    backend: VoiceBackend = VoiceBackend.SYSTEM
    voice_id: str = ""
    speed: float = 1.0
    pitch: float = 1.0
    output_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "tpllm-voice")
    cache_enabled: bool = True
    max_cache_mb: int = 100


@dataclass
class SynthesisResult:
    """Result of a text-to-speech synthesis."""
    text: str
    audio_path: str
    backend: str
    duration_seconds: float = 0.0
    cached: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.audio_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text[:100] + "..." if len(self.text) > 100 else self.text,
            "audio_path": self.audio_path,
            "backend": self.backend,
            "duration_seconds": self.duration_seconds,
            "cached": self.cached,
            "ok": self.ok,
            "error": self.error,
        }


class VoiceIntegration:
    """Text-to-speech synthesis engine with multi-backend support."""

    def __init__(self, config: IntegrationConfig | None = None) -> None:
        self._config = config or IntegrationConfig()
        extra = self._config.extra if self._config else {}

        self._voice_config = VoiceConfig(
            backend=VoiceBackend(extra.get("backend", "system")),
            voice_id=extra.get("voice_id", ""),
            speed=float(extra.get("speed", 1.0)),
            pitch=float(extra.get("pitch", 1.0)),
        )

        if self._config and self._config.api_key:
            self._api_key = self._config.api_key
        else:
            self._api_key = ""

        self._voice_config.output_dir.mkdir(parents=True, exist_ok=True)
        self._synthesis_count = 0

    @property
    def name(self) -> str:
        return "voice"

    @property
    def backend(self) -> VoiceBackend:
        return self._voice_config.backend

    def synthesize(
        self,
        text: str,
        style: VoiceStyle = VoiceStyle.NEUTRAL,
        output_path: str = "",
    ) -> SynthesisResult:
        """Synthesize text to speech.

        Args:
            text: The text to synthesize.
            style: Voice delivery style.
            output_path: Optional specific output path. Auto-generated if empty.

        Returns:
            SynthesisResult with audio file path and metadata.
        """
        if not text.strip():
            return SynthesisResult(text=text, audio_path="", backend=self._voice_config.backend.value,
                                   error="Empty text")

        # Check cache
        if self._voice_config.cache_enabled and not output_path:
            cached = self._check_cache(text, style)
            if cached:
                return cached

        # Apply style preprocessing
        processed_text = self._apply_style(text, style)

        # Route to backend
        if self._voice_config.backend == VoiceBackend.CHATTERBOX:
            result = self._synthesize_chatterbox(processed_text, output_path)
        elif self._voice_config.backend == VoiceBackend.PIPER:
            result = self._synthesize_piper(processed_text, output_path)
        elif self._voice_config.backend == VoiceBackend.KOKORO:
            result = self._synthesize_kokoro(processed_text, output_path)
        elif self._voice_config.backend == VoiceBackend.ELEVENLABS:
            result = self._synthesize_elevenlabs(processed_text, output_path)
        else:
            result = self._synthesize_system(processed_text, output_path)

        self._synthesis_count += 1
        return result

    def synthesize_briefing(self, sections: list[dict[str, str]]) -> SynthesisResult:
        """Synthesize a multi-section briefing (e.g., daily standup).

        Sections format: [{"title": "...", "content": "..."}, ...]
        """
        parts = []
        for section in sections:
            title = section.get("title", "")
            content = section.get("content", "")
            if title:
                parts.append(f"{title}.")
            if content:
                parts.append(content)
            parts.append("")  # pause between sections

        full_text = " ".join(parts)
        return self.synthesize(full_text, style=VoiceStyle.BRIEFING)

    def synthesize_alert(self, title: str, context: str, urgency: str = "high") -> SynthesisResult:
        """Synthesize an alert/escalation with appropriate urgency."""
        style = VoiceStyle.ALERT if urgency == "critical" else VoiceStyle.URGENT
        text = f"Alert: {title}. {context}"
        return self.synthesize(text, style=style)

    def list_available_backends(self) -> list[dict[str, Any]]:
        """Check which TTS backends are available on this system."""
        backends = []

        # Chatterbox (recommended — MIT, best quality in blind tests)
        chatterbox_available = self._check_chatterbox()
        backends.append({
            "backend": "chatterbox",
            "available": chatterbox_available,
            "description": "MIT licensed, Resemble AI — beats ElevenLabs in blind tests, emotion control",
            "recommended": True,
        })

        # System TTS
        system_available = self._check_system_tts()
        backends.append({
            "backend": "system",
            "available": system_available,
            "description": "OS-native TTS (macOS say, Linux espeak)",
        })

        # Piper
        piper_available = self._check_piper()
        backends.append({
            "backend": "piper",
            "available": piper_available,
            "description": "Fast local neural TTS, 900+ voices, 47 languages",
        })

        # Kokoro
        kokoro_available = self._check_kokoro()
        backends.append({
            "backend": "kokoro",
            "available": kokoro_available,
            "description": "Lightweight neural TTS (82M params), Apache 2.0, 8 languages",
        })

        # ElevenLabs
        elevenlabs_available = bool(self._api_key)
        backends.append({
            "backend": "elevenlabs",
            "available": elevenlabs_available,
            "description": "Cloud API with voice cloning (~$206/M chars)",
            "requires_api_key": True,
        })

        return backends

    def get_summary(self) -> str:
        """Get voice integration status summary."""
        backends = self.list_available_backends()
        available = [b["backend"] for b in backends if b["available"]]

        lines = [
            "## Voice Integration",
            f"- Active backend: {self._voice_config.backend.value}",
            f"- Available backends: {', '.join(available) or 'none'}",
            f"- Synthesis count: {self._synthesis_count}",
            f"- Cache: {'enabled' if self._voice_config.cache_enabled else 'disabled'}",
            f"- Speed: {self._voice_config.speed}x",
        ]
        return "\n".join(lines)

    # ── Backend Implementations ───────────────────────────

    def _synthesize_system(self, text: str, output_path: str) -> SynthesisResult:
        """Synthesize using OS-native TTS."""
        out_path = output_path or str(self._get_output_path(text, "wav"))

        try:
            import platform
            system = platform.system()

            if system == "Darwin":
                # macOS: use 'say' command
                subprocess.run(
                    ["say", "-o", out_path, "--data-format=LEI16@22050", text],
                    check=True, capture_output=True, timeout=30,
                )
            elif system == "Linux":
                # Linux: use espeak
                subprocess.run(
                    ["espeak", "-w", out_path, "-s", str(int(150 * self._voice_config.speed)), text],
                    check=True, capture_output=True, timeout=30,
                )
            else:
                return SynthesisResult(
                    text=text, audio_path="", backend="system",
                    error=f"System TTS not supported on {system}",
                )

            return SynthesisResult(
                text=text, audio_path=out_path, backend="system",
                duration_seconds=self._estimate_duration(text),
            )
        except FileNotFoundError:
            return SynthesisResult(
                text=text, audio_path="", backend="system",
                error="TTS command not found (install espeak or use macOS)",
            )
        except subprocess.TimeoutExpired:
            return SynthesisResult(
                text=text, audio_path="", backend="system",
                error="TTS synthesis timed out",
            )
        except Exception as e:
            return SynthesisResult(
                text=text, audio_path="", backend="system",
                error=str(e),
            )

    def _synthesize_chatterbox(self, text: str, output_path: str) -> SynthesisResult:
        """Synthesize using Chatterbox TTS (Resemble AI, MIT licensed).

        Chatterbox beats ElevenLabs in blind tests (63.75% preference),
        supports emotion control and zero-shot voice cloning from 7-20s audio.
        """
        out_path = output_path or str(self._get_output_path(text, "wav"))

        try:
            import chatterbox.tts
            import torch
            import soundfile as sf

            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = chatterbox.tts.ChatterboxTTS.from_pretrained(device=device)

            wav = model.generate(text)
            sf.write(out_path, wav.squeeze().cpu().numpy(), model.sr)

            return SynthesisResult(
                text=text, audio_path=out_path, backend="chatterbox",
                duration_seconds=len(wav.squeeze()) / model.sr,
            )
        except ImportError:
            return SynthesisResult(
                text=text, audio_path="", backend="chatterbox",
                error="Chatterbox not installed. Install: pip install chatterbox-tts",
            )
        except Exception as e:
            return SynthesisResult(
                text=text, audio_path="", backend="chatterbox",
                error=str(e),
            )

    def _synthesize_piper(self, text: str, output_path: str) -> SynthesisResult:
        """Synthesize using Piper TTS."""
        out_path = output_path or str(self._get_output_path(text, "wav"))

        try:
            model = self._voice_config.voice_id or "en_US-lessac-medium"
            cmd = [
                "piper",
                "--model", model,
                "--output_file", out_path,
                "--length_scale", str(1.0 / self._voice_config.speed),
            ]
            subprocess.run(
                cmd, input=text.encode(), check=True,
                capture_output=True, timeout=60,
            )

            return SynthesisResult(
                text=text, audio_path=out_path, backend="piper",
                duration_seconds=self._estimate_duration(text),
            )
        except FileNotFoundError:
            return SynthesisResult(
                text=text, audio_path="", backend="piper",
                error="Piper not installed. Install: pip install piper-tts",
            )
        except Exception as e:
            return SynthesisResult(
                text=text, audio_path="", backend="piper",
                error=str(e),
            )

    def _synthesize_kokoro(self, text: str, output_path: str) -> SynthesisResult:
        """Synthesize using Kokoro TTS."""
        out_path = output_path or str(self._get_output_path(text, "wav"))

        try:
            import kokoro
            voice = self._voice_config.voice_id or "af_heart"
            pipeline = kokoro.KPipeline(lang_code="a")
            generator = pipeline(text, voice=voice, speed=self._voice_config.speed)

            # Kokoro yields (graphemes, phonemes, audio) tuples
            audio_chunks = []
            for _, _, audio in generator:
                audio_chunks.append(audio)

            if audio_chunks:
                import soundfile as sf
                import numpy as np
                combined = np.concatenate(audio_chunks)
                sf.write(out_path, combined, 24000)

                return SynthesisResult(
                    text=text, audio_path=out_path, backend="kokoro",
                    duration_seconds=len(combined) / 24000,
                )
            else:
                return SynthesisResult(
                    text=text, audio_path="", backend="kokoro",
                    error="No audio generated",
                )
        except ImportError:
            return SynthesisResult(
                text=text, audio_path="", backend="kokoro",
                error="Kokoro not installed. Install: pip install kokoro soundfile",
            )
        except Exception as e:
            return SynthesisResult(
                text=text, audio_path="", backend="kokoro",
                error=str(e),
            )

    def _synthesize_elevenlabs(self, text: str, output_path: str) -> SynthesisResult:
        """Synthesize using ElevenLabs API."""
        out_path = output_path or str(self._get_output_path(text, "mp3"))

        if not self._api_key:
            return SynthesisResult(
                text=text, audio_path="", backend="elevenlabs",
                error="ElevenLabs API key not configured",
            )

        try:
            import urllib.request
            import urllib.error

            voice_id = self._voice_config.voice_id or "21m00Tcm4TlvDq8ikWAM"  # Rachel
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

            payload = json.dumps({
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "speed": self._voice_config.speed,
                },
            }).encode()

            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                audio_data = response.read()

            Path(out_path).write_bytes(audio_data)

            return SynthesisResult(
                text=text, audio_path=out_path, backend="elevenlabs",
                duration_seconds=self._estimate_duration(text),
            )
        except ImportError:
            return SynthesisResult(
                text=text, audio_path="", backend="elevenlabs",
                error="urllib not available",
            )
        except Exception as e:
            return SynthesisResult(
                text=text, audio_path="", backend="elevenlabs",
                error=str(e),
            )

    # ── Style Processing ──────────────────────────────────

    def _apply_style(self, text: str, style: VoiceStyle) -> str:
        """Apply voice style modifications to text for more natural delivery."""
        if style == VoiceStyle.BRIEFING:
            # Add pauses after section markers
            text = text.replace(". ", "... ")
        elif style == VoiceStyle.URGENT:
            # Emphasize key phrases
            text = text.replace("blocked", "BLOCKED")
            text = text.replace("overdue", "OVERDUE")
            text = text.replace("critical", "CRITICAL")
        elif style == VoiceStyle.ALERT:
            text = f"Attention. {text}"
        return text

    # ── Caching ───────────────────────────────────────────

    def _cache_key(self, text: str, style: VoiceStyle) -> str:
        """Generate a cache key for text+style combination."""
        content = f"{text}:{style.value}:{self._voice_config.backend.value}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _check_cache(self, text: str, style: VoiceStyle) -> SynthesisResult | None:
        """Check if a cached synthesis exists."""
        key = self._cache_key(text, style)
        for ext in ("wav", "mp3"):
            cached_path = self._voice_config.output_dir / f"{key}.{ext}"
            if cached_path.exists():
                return SynthesisResult(
                    text=text,
                    audio_path=str(cached_path),
                    backend=self._voice_config.backend.value,
                    cached=True,
                )
        return None

    def _get_output_path(self, text: str, ext: str) -> Path:
        """Generate output path (using cache key if caching enabled)."""
        if self._voice_config.cache_enabled:
            key = self._cache_key(text, VoiceStyle.NEUTRAL)
            return self._voice_config.output_dir / f"{key}.{ext}"
        return self._voice_config.output_dir / f"tts_{int(time.time())}.{ext}"

    # ── Utility ───────────────────────────────────────────

    @staticmethod
    def _estimate_duration(text: str) -> float:
        """Estimate audio duration from text length (avg 150 words/min)."""
        words = len(text.split())
        return words / 2.5  # ~150 words per minute

    @staticmethod
    def _check_system_tts() -> bool:
        """Check if system TTS is available."""
        import platform
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.run(["which", "say"], capture_output=True, check=True)
                return True
            elif system == "Linux":
                subprocess.run(["which", "espeak"], capture_output=True, check=True)
                return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return False

    @staticmethod
    def _check_piper() -> bool:
        """Check if Piper TTS is available."""
        try:
            subprocess.run(["which", "piper"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    @staticmethod
    def _check_kokoro() -> bool:
        """Check if Kokoro TTS is available."""
        try:
            import kokoro  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _check_chatterbox() -> bool:
        """Check if Chatterbox TTS is available."""
        try:
            import chatterbox  # noqa: F401
            return True
        except ImportError:
            return False
