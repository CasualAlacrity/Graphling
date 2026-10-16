import os
import time

import httpx
from elevenlabs import AsyncElevenLabs

from auth.session_auth import PilotSessionAuth

# TTS_PROVIDER=alice: speech comes from ALICE's own server (server/routes/tts.py), which
# runs Kokoro on heket and holds any ElevenLabs key -- the shipped product.
# elevenlabs (default): straight to ElevenLabs with this machine's own key, as before.
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs").strip().lower()
# Kokoro answers a typical reply in ~0.1 s; the server's own ElevenLabs fallback can
# take a few seconds. Past this, ALICE stays quiet for the turn rather than hang.
ALICE_TTS_TIMEOUT_S = 30.0


async def synthesize(text: str) -> tuple[bytes, float]:
    """Returns (audio bytes, tts_first_chunk_ms) for the given text, spoken in ALICE's
    voice. The bytes are a whole file (WAV from Kokoro, MP3 from ElevenLabs) that
    voice/audio_output.play_audio decodes either way. Empty bytes = no voice this turn
    (the server had no backend); the caller still shows the reply.

    first_chunk_ms is time-to-first-byte (docs/voice-stage-timing.md) -- the gap
    between it and the caller's own total-call timing is what streaming playback would
    win back."""
    if TTS_PROVIDER == "alice":
        return await _synthesize_via_alice(text)
    return await _synthesize_via_elevenlabs(text)


async def _synthesize_via_alice(text: str) -> tuple[bytes, float]:
    api_url = os.getenv("ALICE_API_URL", "http://localhost:8000").rstrip("/")
    start = time.monotonic()
    first_chunk_ms = None
    chunks = []
    try:
        async with httpx.AsyncClient(auth=PilotSessionAuth(), timeout=ALICE_TTS_TIMEOUT_S) as client:
            async with client.stream("POST", f"{api_url}/tts", json={"text": text}) as response:
                if response.status_code != 200:
                    await response.aread()
                    print(f"[ALICE] No voice this turn: server TTS returned {response.status_code} "
                          f"{response.text[:200]!r}")
                    return b"", 0.0
                async for chunk in response.aiter_bytes():
                    if first_chunk_ms is None:
                        first_chunk_ms = (time.monotonic() - start) * 1000
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        print(f"[ALICE] No voice this turn: server TTS unreachable ({exc!r})")
        return b"", 0.0
    return b"".join(chunks), first_chunk_ms or 0.0


async def _synthesize_via_elevenlabs(text: str) -> tuple[bytes, float]:
    client = AsyncElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    chunks = []
    start = time.monotonic()
    first_chunk_ms = None
    async for chunk in client.text_to_speech.convert(
        voice_id=os.getenv("ELEVENLABS_VOICE_ID"),
        text=text,
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
    ):
        if first_chunk_ms is None:
            first_chunk_ms = (time.monotonic() - start) * 1000
        chunks.append(chunk)
    return b"".join(chunks), first_chunk_ms or 0.0
