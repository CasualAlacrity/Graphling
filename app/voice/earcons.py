"""ALICE's push-to-talk chirps: a rising two-note chirp when recording starts, the same
two notes falling when it stops — so a pilot knows ALICE heard the key without looking
away from the cockpit (docs/intent/voice-latency.md, "Audio vocabulary").

Synthesised here, not sound files: nothing to license or bundle, and per that doc the
earcons are ALICE's own — no Star Trek or Star Citizen sounds. Kept short and quiet so
they read as a click of confirmation, not an announcement, and so a mic near speakers
picks up as little of the start chirp as possible.
"""
import os

import numpy as np
import sounddevice as sd

import audio_devices

SAMPLE_RATE = 44100
NOTE_SECONDS = 0.055
FADE_SECONDS = 0.008
VOLUME = 0.22
# A5 and E6 — a perfect fifth: bright, unambiguous direction, not musical enough to
# get tiresome on the hundredth press.
LOW_HZ = 880.0
HIGH_HZ = 1318.5


def _note(frequency: float) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * NOTE_SECONDS)) / SAMPLE_RATE
    # A little of the second harmonic softens the pure sine's "test tone" edge.
    wave = np.sin(2 * np.pi * frequency * t) + 0.25 * np.sin(4 * np.pi * frequency * t)
    fade = int(SAMPLE_RATE * FADE_SECONDS)
    envelope = np.ones_like(t)
    envelope[:fade] = np.linspace(0.0, 1.0, fade)
    envelope[-fade:] = np.linspace(1.0, 0.0, fade)
    return (wave * envelope * VOLUME / 1.25).astype("float32")


START_CHIRP = np.concatenate([_note(LOW_HZ), _note(HIGH_HZ)])
STOP_CHIRP = np.concatenate([_note(HIGH_HZ), _note(LOW_HZ)])


def enabled() -> bool:
    return os.getenv("ALICE_PTT_SOUNDS", "1") != "0"


def play(chirp: np.ndarray) -> None:
    """Non-blocking — recording starts (or transcription begins) while it plays. Never
    raises: a chirp failing to play must not cost the pilot their utterance."""
    if not enabled():
        return
    try:
        sd.play(chirp, samplerate=SAMPLE_RATE, device=audio_devices.resolve(os.getenv("ALICE_OUTPUT_DEVICE"), "output"))
    except Exception as exc:
        print(f"[Voice] Couldn't play the PTT sound ({exc}).")
