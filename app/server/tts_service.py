"""The TTS gateway (docs/llm-gateway.md, TTS section) -- every spoken ALICE reply goes
through here, so no client holds an ElevenLabs key or reaches frankencompute directly.

Per request:
1. Kokoro on frankencompute (frankencompute/kokoro_server.py), unless it failed in the
   last DOWN_FOR_S seconds. It returns a whole WAV in ~0.1 s for a typical reply, so
   there is nothing to gain from streaming yet.
2. On a connect error, timeout or 5xx, fall back to ElevenLabs -- only if a key is
   configured, since it is a different voice. No fallback = NoBackendAvailable (503).
3. Every attempt that reaches a backend writes one tts_usage row.

Same single-process, in-memory state assumption as llm_gateway_service.
"""
import logging
import time
import uuid
from dataclasses import dataclass

import httpx

from db.models import TtsUsage
from db.session import SessionLocal
from model_pricing import local_energy_cost_usd
from server.config import settings

log = logging.getLogger(__name__)

FRANKENCOMPUTE = "frankencompute"
ELEVENLABS = "elevenlabs"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

CONNECT_TIMEOUT_S = 2.0
# Kokoro takes ~0.1 s for a typical reply on the 4080; 15 s is a hung box, not a slow one.
FRANKENCOMPUTE_TIMEOUT_S = 15.0
ELEVENLABS_TIMEOUT_S = 30.0
DOWN_FOR_S = 30.0


def _http_client(**kwargs) -> httpx.AsyncClient:
    """The one seam tests swap for an httpx.MockTransport."""
    return httpx.AsyncClient(**kwargs)


class NoBackendAvailable(RuntimeError):
    pass


class FrankencomputeFailed(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class TtsState:
    def __init__(self) -> None:
        self.down_until = 0.0
        self.down_reason: str | None = None


state = TtsState()


@dataclass
class Speech:
    audio: bytes
    media_type: str  # audio/wav from Kokoro, audio/mpeg from ElevenLabs
    backend: str


def elevenlabs_configured() -> bool:
    return bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id)


async def speak(text: str, user_id: uuid.UUID) -> Speech:
    started = time.monotonic()
    fallback_reason = None
    # Benching only helps when there's somewhere else to go; without a fallback, trying
    # the box again is the only way ALICE gets her voice back.
    if started < state.down_until and elevenlabs_configured():
        fallback_reason = state.down_reason or "down"
    if fallback_reason is None:
        try:
            return await _speak_frankencompute(text, user_id, started)
        except FrankencomputeFailed as failure:
            fallback_reason = failure.reason
            state.down_until = time.monotonic() + DOWN_FOR_S
            state.down_reason = failure.reason
            log.warning("frankencompute TTS failed (%s); falling back", failure.reason)

    if not elevenlabs_configured():
        raise NoBackendAvailable(f"frankencompute TTS unavailable ({fallback_reason}) and no fallback is configured")
    return await _speak_elevenlabs(text, user_id, time.monotonic(), fallback_reason)


async def _speak_frankencompute(text: str, user_id: uuid.UUID, started: float) -> Speech:
    try:
        async with _http_client(
            base_url=settings.frankencompute_tts_url,
            timeout=httpx.Timeout(FRANKENCOMPUTE_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        ) as client:
            response = await client.post("/tts", json={"text": text, "voice": settings.tts_voice})
    except httpx.TimeoutException as exc:
        raise FrankencomputeFailed("timeout") from exc
    except httpx.TransportError as exc:
        raise FrankencomputeFailed("down") from exc

    latency_ms = (time.monotonic() - started) * 1000
    if response.status_code >= 500:
        await _record_error(user_id, FRANKENCOMPUTE, settings.tts_voice, text, latency_ms, None)
        raise FrankencomputeFailed("5xx")
    if response.status_code != 200:
        # A 4xx is our request's fault (empty, too long), not the box's -- falling back
        # would only fail the same way and burn ElevenLabs characters doing it.
        await _record_error(user_id, FRANKENCOMPUTE, settings.tts_voice, text, latency_ms, None)
        raise NoBackendAvailable(f"frankencompute TTS rejected the request: {response.status_code}")

    audio_seconds = _float_header(response, "X-Audio-Seconds")
    state.down_until = 0.0
    state.down_reason = None
    await record_usage(
        user_id=user_id, backend=FRANKENCOMPUTE, voice=settings.tts_voice, characters=len(text),
        audio_seconds=audio_seconds, latency_ms=latency_ms, status="ok", fallback_reason=None,
        cost_usd=local_energy_cost_usd(latency_ms),
    )
    return Speech(audio=response.content, media_type="audio/wav", backend=FRANKENCOMPUTE)


async def _speak_elevenlabs(text: str, user_id: uuid.UUID, started: float, fallback_reason: str) -> Speech:
    voice = settings.elevenlabs_voice_id
    try:
        async with _http_client(timeout=ELEVENLABS_TIMEOUT_S) as client:
            response = await client.post(
                ELEVENLABS_TTS_URL.format(voice_id=voice),
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": settings.elevenlabs_api_key},
                json={"text": text, "model_id": settings.elevenlabs_model},
            )
    except httpx.HTTPError as exc:
        latency_ms = (time.monotonic() - started) * 1000
        await _record_error(user_id, ELEVENLABS, voice, text, latency_ms, fallback_reason)
        raise NoBackendAvailable(f"ElevenLabs request failed: {exc!r}") from exc

    latency_ms = (time.monotonic() - started) * 1000
    if response.status_code != 200:
        await _record_error(user_id, ELEVENLABS, voice, text, latency_ms, fallback_reason)
        raise NoBackendAvailable(f"ElevenLabs returned {response.status_code}: {response.text[:300]}")

    await record_usage(
        user_id=user_id, backend=ELEVENLABS, voice=voice, characters=len(text), audio_seconds=None,
        latency_ms=latency_ms, status="ok", fallback_reason=fallback_reason,
        cost_usd=len(text) / 1000 * settings.elevenlabs_usd_per_1k_chars,
    )
    return Speech(audio=response.content, media_type="audio/mpeg", backend=ELEVENLABS)


def _float_header(response: httpx.Response, name: str) -> float | None:
    try:
        return float(response.headers[name])
    except (KeyError, ValueError):
        return None


async def _record_error(
    user_id: uuid.UUID, backend: str, voice: str, text: str, latency_ms: float, fallback_reason: str | None,
) -> None:
    await record_usage(
        user_id=user_id, backend=backend, voice=voice, characters=len(text), audio_seconds=None,
        latency_ms=latency_ms, status="error", fallback_reason=fallback_reason, cost_usd=None,
    )


async def record_usage(**fields) -> None:
    """Best-effort: a failed usage write is logged, never turned into a silent ALICE."""
    try:
        async with SessionLocal() as session:
            session.add(TtsUsage(**fields))
            await session.commit()
    except Exception:
        log.exception("failed to record tts_usage row")
