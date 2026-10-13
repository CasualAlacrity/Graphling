"""
Push-to-talk voice capture + Whisper STT.

Flow:
  - Hold PTT key → mic opens
  - Release PTT key → recording stops → Whisper transcribes → returns text

load_whisper() is slow (a few seconds; the first launch also downloads the model).
Call it once at startup.

Whisper runs through faster-whisper (CTranslate2, 8-bit weights on the CPU), not
openai-whisper: measured 2026-10-12, `small` here needs ~700 MB of RAM on an x86 CPU
against ~820 MB for openai-whisper's `base`, hears station and commodity names much
better ("best route out of Port Tressler", not "Best Root Out of Portressler"), and
drops PyTorch from the client install. It's slower per utterance (~1-3 s for `small`
on a CPU, depending on the machine), so WHISPER_MODEL=base is the faster fallback.
It never uses the GPU, so it takes nothing from the game.
"""
import os
import threading
import time

import numpy as np
import sounddevice as sd

# The model downloads from Hugging Face on first launch. On Windows that prints two
# alarming-looking but harmless warnings: no symlink support for a normal user (files
# are copied instead), and "unauthenticated requests" (a public model needs no token).
# Pilots can't act on either, so silence them. Set before huggingface_hub is imported.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

from faster_whisper import WhisperModel  # noqa: E402 -- after the env defaults above
from pynput import keyboard

_model: WhisperModel | None = None
_model_size: str | None = None

# What the last capture looked like, for the transcript log (transcript_log.py):
# audio_s / rms of the recording, and why it was thrown away (None if it wasn't).
last_capture: dict = {}

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


def load_whisper(model_size: str | None = None) -> None:
    """Load the Whisper model into memory. Call once at startup. WHISPER_MODEL picks the
    size: small (default, best with names), base (faster, lighter), or the English-only
    small.en / base.en."""
    global _model, _model_size
    model_size = model_size or os.getenv("WHISPER_MODEL", "small")
    _model_size = model_size
    print(f"[Voice] Loading Whisper '{model_size}' model (the first launch downloads it)...")
    _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    print("[Voice] Whisper ready")


def _parse_hotkey(hotkey_str: str) -> keyboard.Key | keyboard.KeyCode:
    """Convert a config string like 'shift_r' to a pynput Key. "<123>" is a raw virtual
    key code — what the launcher's key capture stores for a key with no name or
    character (launcher/key_capture.py)."""
    if hotkey_str.startswith("<") and hotkey_str.endswith(">") and hotkey_str[1:-1].isdigit():
        return keyboard.KeyCode.from_vk(int(hotkey_str[1:-1]))
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


def _input_device() -> str | None:
    """The pilot's chosen microphone (ALICE_MIC_DEVICE, set from the launcher), or None
    for the system default. Falls back to the default when the saved device is gone
    (unplugged headset) rather than failing every capture."""
    name = os.getenv("ALICE_MIC_DEVICE")
    if not name:
        return None
    try:
        sd.query_devices(name, "input")
    except ValueError:
        print(f"[Voice] Microphone '{name}' not found; using the system default.")
        return None
    return name


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

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=_input_device()) as stream:
        while not _released.is_set():
            chunk, _ = stream.read(1024)
            frames.append(chunk)

    print("[Voice] Recording stopped")
    if not frames:
        return np.array([], dtype="float32")

    return np.concatenate(frames, axis=0).flatten()


def transcribe(audio: np.ndarray, initial_prompt: str | None = None) -> tuple[str, float]:
    """Run Whisper on a float32 16kHz numpy array. Returns (transcribed text, whisper_ms)
    -- whisper_ms times only the model.transcribe() call itself (docs/voice-stage-timing.md),
    not the silence/duration gates above it, and is 0.0 for any early return.

    initial_prompt seeds Whisper's decoder with expected vocabulary (e.g. ship names) —
    it biases recognition toward correctly rendering unusual proper nouns, it doesn't
    force them into the output. See app/voice/__init__.py for how the prompt is built.
    """
    if _model is None:
        raise RuntimeError("Whisper model not loaded — call load_whisper() at startup")

    last_capture.clear()
    last_capture.update(mode="voice", whisper_model=_model_size)
    if len(audio) == 0:
        last_capture.update(audio_s=0.0, rms=0.0, ignored="no audio")
        return "", 0.0

    duration_seconds = len(audio) / SAMPLE_RATE
    rms = float(np.sqrt(np.mean(np.square(audio))))
    print(f"[Voice] Captured {duration_seconds:.2f}s, rms {rms:.4f}")
    last_capture.update(audio_s=round(duration_seconds, 2), rms=round(rms, 4))

    if duration_seconds < MIN_SPEECH_SECONDS:
        print(f"[Voice] Ignored — shorter than {MIN_SPEECH_SECONDS}s")
        last_capture["ignored"] = f"shorter than {MIN_SPEECH_SECONDS}s"
        return "", 0.0

    if rms < SILENCE_RMS_THRESHOLD:
        print(f"[Voice] Ignored — quieter than {SILENCE_RMS_THRESHOLD}")
        last_capture["ignored"] = f"quieter than rms {SILENCE_RMS_THRESHOLD}"
        return "", 0.0

    start = time.monotonic()
    segments, _ = _model.transcribe(audio, language="en", initial_prompt=initial_prompt, beam_size=5)
    # segments is lazy: decoding happens while it's consumed, so the join is inside the timing.
    text = " ".join(segment.text.strip() for segment in segments).strip()
    whisper_ms = (time.monotonic() - start) * 1000
    print(f"[Voice] Transcribed: {text!r}")
    return text, whisper_ms


def listen_once(hotkey: str | None = None, initial_prompt: str | None = None) -> tuple[str, float]:
    """
    Full PTT cycle: wait for key → record → transcribe → return (text, whisper_ms).
    Falls back to typed input if PTT_MODE=text (useful when the OS hasn't
    granted the running process Accessibility permission yet — e.g. PyCharm
    on macOS, until it's added under System Settings > Privacy & Security) —
    whisper_ms is 0.0 in that case, since no model call happens.
    """
    if os.getenv("PTT_MODE", "hotkey") == "text":
        last_capture.clear()
        last_capture.update(mode="typed")
        return input("[Voice] Type your query: ").strip(), 0.0

    audio = record_until_release(hotkey)
    return transcribe(audio, initial_prompt=initial_prompt)
