"""
Plays MP3 audio bytes returned by ElevenLabs TTS.
Uses soundfile to decode and sounddevice to play — no temp files needed.
"""
import io

import sounddevice as sd
import soundfile as sf


def play_audio(mp3_bytes: bytes) -> None:
    """Decode MP3 bytes and play through the default output device. Blocks until done."""
    buffer = io.BytesIO(mp3_bytes)
    data, sample_rate = sf.read(buffer, dtype="float32")
    sd.play(data, samplerate=sample_rate)
    sd.wait()
