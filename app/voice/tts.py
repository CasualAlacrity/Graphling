import os
import time

from elevenlabs import AsyncElevenLabs


async def synthesize(text: str) -> tuple[bytes, float]:
    """Returns (mp3 audio bytes, tts_first_chunk_ms) for the given text, spoken in
    ALICE's voice. first_chunk_ms is time-to-first-byte from ElevenLabs
    (docs/voice-stage-timing.md) -- measurable today even though playback doesn't
    stream yet; the gap between it and the caller's own total-call timing is exactly
    what streaming playback would win back."""
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
