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

# Whisper does not return an empty string for silence — given a near-silent or very short
# buffer it falls back on its language prior and emits confident nonsense. A stray PTT tap,
# or holding the key while typing, produces a hallucinated instruction rather than nothing,
# and ALICE then acts on it. These two gates reject that audio before it reaches the model.
#
# Both thresholds are mic-dependent — transcribe() prints the measured duration and RMS on
# every capture so they can be tuned against real hardware rather than guessed at.
MIN_SPEECH_SECONDS = 0.35
SILENCE_RMS_THRESHOLD = 0.005


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


_target_key: keyboard.Key | keyboard.KeyCode | None = None
_pressed = threading.Event()
_released = threading.Event()
_listener: keyboard.Listener | None = None


def _on_press(k):
    if k == _target_key and not _pressed.is_set():
        _pressed.set()


def _on_release(k):
    if k == _target_key:
        _released.set()


def _ensure_listener() -> None:
    # One Listener for the whole process lifetime, not one per press/release cycle —
    # recreating a pynput Listener on a background thread every utterance, alongside
    # the overlay's own separate GlobalHotKeys listener running on the main Qt thread,
    # crashed with SIGTRAP the moment a second Listener got created. Two independent
    # low-level macOS event taps in one process, one of them repeatedly torn down and
    # rebuilt, is what tripped it — a single long-lived listener sidesteps that
    # entirely, and is the normal pynput usage pattern anyway.
    global _listener
    if _listener is None:
        _listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
        _listener.start()


def record_until_release(hotkey: str | None = None) -> np.ndarray:
    """
    Blocks until the PTT key is pressed, records while held, stops on release.
    Returns raw audio as a float32 numpy array at 16kHz.
    """
    global _target_key
    key_str = hotkey or os.getenv("PTT_HOTKEY", "shift_r")
    _target_key = _parse_hotkey(key_str)
    _pressed.clear()
    _released.clear()

    _ensure_listener()

    print(f"[Voice] Waiting for PTT key ({key_str})...")

    frames: list[np.ndarray] = []

    _pressed.wait()
    print("[Voice] Recording...")

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        while not _released.is_set():
            chunk, _ = stream.read(1024)
            frames.append(chunk)

    print("[Voice] Recording stopped")
    if not frames:
        return np.array([], dtype="float32")

    return np.concatenate(frames, axis=0).flatten()


def transcribe(audio: np.ndarray, initial_prompt: str | None = None) -> str:
    """Run Whisper on a float32 16kHz numpy array. Returns transcribed text.

    initial_prompt seeds Whisper's decoder with expected vocabulary (e.g. ship names) —
    it biases recognition toward correctly rendering unusual proper nouns, it doesn't
    force them into the output. See app/voice/__init__.py for how the prompt is built.
    """
    if _model is None:
        raise RuntimeError("Whisper model not loaded — call load_whisper() at startup")

    if len(audio) == 0:
        return ""

    duration_seconds = len(audio) / SAMPLE_RATE
    rms = float(np.sqrt(np.mean(np.square(audio))))
    print(f"[Voice] Captured {duration_seconds:.2f}s, rms {rms:.4f}")

    if duration_seconds < MIN_SPEECH_SECONDS:
        print(f"[Voice] Ignored — shorter than {MIN_SPEECH_SECONDS}s")
        return ""

    if rms < SILENCE_RMS_THRESHOLD:
        print(f"[Voice] Ignored — quieter than {SILENCE_RMS_THRESHOLD}")
        return ""

    result = _model.transcribe(audio, fp16=False, language="en", initial_prompt=initial_prompt)
    text = result["text"].strip()
    print(f"[Voice] Transcribed: {text!r}")
    return text


def listen_once(hotkey: str | None = None, initial_prompt: str | None = None) -> str:
    """
    Full PTT cycle: wait for key → record → transcribe → return text.
    Falls back to typed input if PTT_MODE=text (useful when the OS hasn't
    granted the running process Accessibility permission yet — e.g. PyCharm
    on macOS, until it's added under System Settings > Privacy & Security).
    """
    if os.getenv("PTT_MODE", "hotkey") == "text":
        return input("[Voice] Type your query: ").strip()

    audio = record_until_release(hotkey)
    return transcribe(audio, initial_prompt=initial_prompt)
