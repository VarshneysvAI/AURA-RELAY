"""
AURA Relay — Voice & TTS Provider
Supports RealtimeTTS streaming with instant interruption (.stop())
and fallback synthesis.
"""
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Tuple
import httpx

from aura.config import get_config

logger = logging.getLogger("aura.tts")

AUDIO_DIR = Path("runtime/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# RealtimeTTS integration for in-memory, instant, interruptible audio
try:
    from RealtimeTTS import TextToAudioStream, SystemEngine
    _realtime_engine = SystemEngine()
    _realtime_stream = TextToAudioStream(_realtime_engine, tokenizer="rule-based")
    HAS_REALTIME_TTS = True
    logger.info("RealtimeTTS initialized with SystemEngine (rule-based, in-memory, instant stop)")
except Exception as e:
    _realtime_stream = None
    HAS_REALTIME_TTS = False
    logger.warning(f"RealtimeTTS not available ({e}). Falling back to browser speech.")


def stop_realtime_tts() -> bool:
    """Instantly halts audio playback on user interruption without deadlocking."""
    if not HAS_REALTIME_TTS or not _realtime_stream:
        return False
    try:
        def _do_stop():
            try:
                if hasattr(_realtime_stream, "is_playing") and _realtime_stream.is_playing():
                    _realtime_stream.stop()
                    logger.info("RealtimeTTS audio interrupted and stopped.")
            except Exception as e:
                logger.debug(f"RealtimeTTS stop exception: {e}")

        # Run stop in daemon thread with timeout so it NEVER hangs the HTTP or caller thread
        t = threading.Thread(target=_do_stop, daemon=True)
        t.start()
        t.join(timeout=0.25)
        return True
    except Exception as e:
        logger.debug(f"RealtimeTTS stop error: {e}")
        return False


def play_realtime_tts(text: str) -> bool:
    """Play text using RealtimeTTS directly in-memory to speakers asynchronously."""
    if not HAS_REALTIME_TTS or not _realtime_stream or not text.strip():
        return False

    def _do_play():
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass

            # If something is currently playing, halt it safely
            if getattr(_realtime_stream, "is_playing_flag", False) or (hasattr(_realtime_stream, "is_playing") and _realtime_stream.is_playing()):
                try:
                    _realtime_stream.stop()
                except Exception:
                    pass

            _realtime_stream.feed(text.strip())
            _realtime_stream.play_async()
            logger.info(f"RealtimeTTS playing: '{text[:40]}...'")
        except Exception as e:
            logger.warning(f"RealtimeTTS play error: {e}")

    threading.Thread(target=_do_play, daemon=True).start()
    return True


class NvidiaTTSProvider:
    """NVIDIA Magpie / Riva TTS provider with RealtimeTTS fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        voice: Optional[str] = None,
    ):
        cfg = get_config()
        self.api_key = api_key or cfg.nvidia_tts_api_key or os.getenv("NVIDIA_TTS_API_KEY")
        self.base_url = base_url or cfg.nvidia_tts_base_url or os.getenv("NVIDIA_TTS_BASE_URL")
        self.voice = voice or cfg.nvidia_tts_voice or "Magpie-Multilingual.EN-US.Aria"

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def _get_audio_filename(self, text: str, voice: str) -> str:
        content = f"{text.strip().lower()}:{voice}"
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return f"aura_{h}.wav"

    async def synthesize(self, text: str, voice: Optional[str] = None) -> Tuple[Optional[str], bool]:
        """
        Synthesize text to audio using RealtimeTTS in-memory streaming.
        Bypasses remote MP3/WAV generation and streams directly to speakers with instant stop.
        """
        clean_text = text.strip()
        if not clean_text:
            return None, False

        # If RealtimeTTS is available, play in-memory instantly
        if HAS_REALTIME_TTS:
            play_realtime_tts(clean_text)
            return "realtime", False

        # Fallback for frontend Web Speech API
        return None, False


_tts_provider: Optional[NvidiaTTSProvider] = None


def get_tts_provider() -> NvidiaTTSProvider:
    global _tts_provider
    if _tts_provider is None:
        _tts_provider = NvidiaTTSProvider()
    return _tts_provider
