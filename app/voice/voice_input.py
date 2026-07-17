"""
Push-to-talk voice capture + Whisper STT.

Flow:
  - Hold PTT key → mic opens
  - Release PTT key → recording stops → Whisper transcribes → returns text

load_whisper() is slow (~5s first call). Call it once at startup.
"""
import os
import threading

import numpy as np
import sounddevice as sd
import whisper
from pynput import keyboard

_model: whisper.Whisper | None = None

SAMPLE_RATE = 16000  # Whisper expects 16kHz


def load_whisper(model_size: str = "base") -> None:
    """Load Whisper model into memory. Call once at startup."""
    global _model
    print(f"[Voice] Loading Whisper '{model_size}' model...")
    _model = whisper.load_model(model_size)
    print("[Voice] Whisper ready")


def _parse_hotkey(hotkey_str: str) -> keyboard.Key | keyboard.KeyCode:
    """Convert a config string like 'shift_r' to a pynput Key."""
    try:
        return keyboard.Key[hotkey_str]
    except KeyError:
        return keyboard.KeyCode.from_char(hotkey_str)


def record_until_release(hotkey: str | None = None) -> np.ndarray:
    """
    Blocks until the PTT key is pressed, records while held, stops on release.
    Returns raw audio as a float32 numpy array at 16kHz.
    """
    key_str = hotkey or os.getenv("PTT_HOTKEY", "shift_r")
    target_key = _parse_hotkey(key_str)

    pressed = threading.Event()
    released = threading.Event()

    def on_press(k):
        if k == target_key and not pressed.is_set():
            pressed.set()

    def on_release(k):
        if k == target_key:
            released.set()

    print(f"[Voice] Waiting for PTT key ({key_str})...")

    frames: list[np.ndarray] = []

    # Single listener stays open for the entire press→release cycle
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        pressed.wait()
        print("[Voice] Recording...")

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
            while not released.is_set():
                chunk, _ = stream.read(1024)
                frames.append(chunk)

        listener.stop()

    print("[Voice] Recording stopped")
    if not frames:
        return np.array([], dtype="float32")

    return np.concatenate(frames, axis=0).flatten()


def transcribe(audio: np.ndarray) -> str:
    """Run Whisper on a float32 16kHz numpy array. Returns transcribed text."""
    if _model is None:
        raise RuntimeError("Whisper model not loaded — call load_whisper() at startup")

    if len(audio) == 0:
        return ""

    result = _model.transcribe(audio, fp16=False, language="en")
    text = result["text"].strip()
    print(f"[Voice] Transcribed: {text!r}")
    return text


def listen_once(hotkey: str | None = None) -> str:
    """
    Full PTT cycle: wait for key → record → transcribe → return text.
    Falls back to typed input if PTT_MODE=text (useful when the OS hasn't
    granted the running process Accessibility permission yet — e.g. PyCharm
    on macOS, until it's added under System Settings > Privacy & Security).
    """
    if os.getenv("PTT_MODE", "hotkey") == "text":
        return input("[Voice] Type your query: ").strip()

    audio = record_until_release(hotkey)
    return transcribe(audio)
