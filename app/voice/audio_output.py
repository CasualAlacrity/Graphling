"""
Plays the audio voice/tts.py returns -- MP3 from ElevenLabs or WAV from Kokoro (via the
server). Uses soundfile to decode (it reads both) and sounddevice to play -- no temp files.
"""
import io
import os

import sounddevice as sd
import soundfile as sf

import audio_devices


def play_audio(audio_bytes: bytes) -> None:
    """Decode audio bytes and play through the pilot's chosen output device
    (ALICE_OUTPUT_DEVICE, from the launcher; the system default otherwise). Blocks until
    done. Empty bytes (no voice this turn, see voice/tts.py) play nothing."""
    if not audio_bytes:
        return
    buffer = io.BytesIO(audio_bytes)
    data, sample_rate = sf.read(buffer, dtype="float32")
    sd.play(data, samplerate=sample_rate, device=audio_devices.resolve(os.getenv("ALICE_OUTPUT_DEVICE"), "output"))
    sd.wait()
