import os

from elevenlabs import AsyncElevenLabs


async def synthesize(text: str) -> bytes:
    """Returns mp3 audio bytes for the given text, spoken in Uplink's voice."""
    client = AsyncElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    chunks = []
    async for chunk in client.text_to_speech.convert(
        voice_id=os.getenv("ELEVENLABS_VOICE_ID"),
        text=text,
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
    ):
        chunks.append(chunk)
    return b"".join(chunks)
