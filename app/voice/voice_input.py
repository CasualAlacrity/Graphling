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
import fnmatch
import os
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

# The model downloads from Hugging Face on first launch. On Windows that prints two
# alarming-looking but harmless warnings: no symlink support for a normal user (files
# are copied instead), and "unauthenticated requests" (a public model needs no token).
# Pilots can't act on either, so silence them. Set before huggingface_hub is imported.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
# Plain HTTP rather than Hugging Face's Xet transfer: Xet assembles the file elsewhere and
# writes it in one go at the end, so the launcher's download progress (see
# _download_with_progress) sat at 0% the whole time, then jumped to done. Measured
# 2026-10-14: HTTP grows the cache file steadily and was no slower (base, ~145 MB, 6 s).
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import huggingface_hub  # noqa: E402 -- after the env defaults above
from faster_whisper import WhisperModel  # noqa: E402
from faster_whisper.utils import _MODELS as WHISPER_REPOS  # noqa: E402
from faster_whisper.utils import download_model  # noqa: E402
from huggingface_hub import constants as hf_constants  # noqa: E402 -- a lazy submodule, not an attribute
from pynput import keyboard

import audio_devices
import hotas
import startup_status
from voice import earcons

# The files faster-whisper's download_model fetches (its allow_patterns), for sizing the
# download up front.
WHISPER_FILE_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
PROGRESS_INTERVAL_S = 0.5

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
    model_path = _ensure_downloaded(model_size)
    startup_status.report(startup_status.LOADING_VOICE)
    print(f"[Voice] Loading the speech model ({model_size})...")
    _model = WhisperModel(model_path, device="cpu", compute_type="int8")
    print("[Voice] Speech model ready")


def _ensure_downloaded(model_size: str) -> str:
    """The model's local folder, downloading it first if this machine doesn't have it —
    with progress reported to the launcher, since on a first launch this is ~0.5 GB and
    the single biggest wait a new pilot sees."""
    try:
        return download_model(model_size, local_files_only=True)
    except Exception:
        pass  # not downloaded yet (or only partly — the download below resumes it)
    return _download_with_progress(model_size)


def _download_with_progress(model_size: str) -> str:
    repo_id = WHISPER_REPOS.get(model_size, model_size)
    total = _download_size(repo_id)
    size_text = f"{total / 1e6:.0f} MB" if total else "a few hundred MB"
    print(f"[Voice] First launch: downloading the speech model ({size_text}). This only happens once.")
    startup_status.report(startup_status.DOWNLOADING_VOICE, 0, total)

    repo_dir = Path(hf_constants.HF_HUB_CACHE) / f"models--{repo_id.replace('/', '--')}"
    finished = threading.Event()

    def _report_progress():
        # Measured from the cache folder's size rather than a progress callback —
        # faster-whisper turns huggingface_hub's own progress bars off. Kept monotonic
        # and capped at the expected total: a finished file can briefly exist twice
        # (download, then its final copy), which would overshoot.
        highest = 0
        while not finished.wait(PROGRESS_INTERVAL_S):
            done = min(_folder_bytes(repo_dir), total) if total else _folder_bytes(repo_dir)
            highest = max(highest, done)
            startup_status.report(startup_status.DOWNLOADING_VOICE, highest, total)

    reporter = threading.Thread(target=_report_progress, daemon=True)
    reporter.start()
    try:
        return download_model(model_size)
    finally:
        finished.set()
        reporter.join()


def _download_size(repo_id: str) -> int | None:
    """Bytes the download will fetch — the same files faster-whisper's download_model
    asks for. None if Hugging Face can't be asked; progress then shows MB only."""
    try:
        info = huggingface_hub.HfApi().model_info(repo_id, files_metadata=True)
    except Exception:
        return None
    total = 0
    for sibling in info.siblings or []:
        if sibling.size and any(fnmatch.fnmatch(sibling.rfilename, pattern) for pattern in WHISPER_FILE_PATTERNS):
            total += sibling.size
    return total or None


def _folder_bytes(folder: Path) -> int:
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass  # a file renamed or removed between listing and measuring
    return total


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
# Set instead of _target_key when the PTT key is a joystick button (hotas.py).
_target_joy: hotas.JoyBinding | None = None
_joy_listening = False
_pressed = threading.Event()
_released = threading.Event()
_listener: keyboard.Listener | None = None


def _on_press(k):
    if k == _target_key and not _pressed.is_set():
        _pressed.set()


def _on_release(k):
    if k == _target_key:
        _released.set()


def _on_joy(binding: hotas.JoyBinding, pressed: bool) -> None:
    if binding != _target_joy:
        return
    if pressed:
        if not _pressed.is_set():
            _pressed.set()
    else:
        _released.set()


def _ensure_joy_listener() -> None:
    global _joy_listening
    if not _joy_listening:
        hotas.watcher().add_listener(_on_joy)
        _joy_listening = True


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


def _input_device() -> int | None:
    """The pilot's chosen microphone (ALICE_MIC_DEVICE, set from the launcher), or None
    for the system default — see audio_devices.resolve for the Windows duplicate-name
    handling and the unplugged-device fallback."""
    return audio_devices.resolve(os.getenv("ALICE_MIC_DEVICE"), "input")


def _open_input_stream() -> sd.InputStream:
    return sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=_input_device())


def record_until_release(hotkey: str | None = None) -> np.ndarray:
    """
    Blocks until the PTT key is pressed, records while held, stops on release.
    Returns raw audio as a float32 numpy array at 16kHz.
    """
    global _target_key, _target_joy
    key_str = hotkey or os.getenv("PTT_HOTKEY", "shift_r")
    _target_joy = hotas.parse(key_str)
    _target_key = None if _target_joy is not None else _parse_hotkey(key_str)
    _pressed.clear()
    _released.clear()

    if _target_joy is not None:
        _ensure_joy_listener()
        key_label = f"{_target_joy.display()}, {_target_joy.to_string()}"
    else:
        _ensure_listener()
        key_label = key_str

    print(f"[Voice] Waiting for PTT key ({key_label})...")

    frames: list[np.ndarray] = []

    # Opened now, started on the press: constructing the stream is the slow part (the
    # device open can take a few hundred ms on Windows), and doing it after the press
    # meant the first words were spoken before the mic was live. start() is near-instant.
    # Not started until the press, so nothing is captured -- and the OS mic indicator
    # shouldn't light -- while waiting.
    stream = _open_input_stream()
    try:
        _pressed.wait()
        pressed_at = time.monotonic()
        try:
            stream.start()
        except sd.PortAudioError:
            # The device went away while idle (unplugged, sleep/resume): reopen once.
            stream.close()
            stream = _open_input_stream()
            stream.start()
        live_ms = (time.monotonic() - pressed_at) * 1000
        # Chirp after the mic is live, not before: sd.play opens an output stream first,
        # which used to delay the start of recording by however long that took.
        earcons.play(earcons.START_CHIRP)
        print(f"[Voice] Recording... (mic live {live_ms:.0f} ms after the press)")

        while not _released.is_set():
            chunk, _ = stream.read(1024)
            frames.append(chunk)
    finally:
        stream.close()

    earcons.play(earcons.STOP_CHIRP)
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


def prompt_token_count(text: str) -> int:
    """How many tokens the loaded Whisper model's own tokenizer makes of `text`, for
    fitting the vocabulary hint to faster-whisper's prompt limit (voice/ship_hint.py)."""
    if _model is None:
        raise RuntimeError("Whisper model not loaded — call load_whisper() at startup")
    return len(_model.hf_tokenizer.encode(text, add_special_tokens=False).ids)


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
