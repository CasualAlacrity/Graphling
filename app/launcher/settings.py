"""The pilot's own launcher settings — PTT key, overlay key, microphone, speech model —
saved as JSON in ALICE's per-pilot data folder (%APPDATA%\\ALICE on Windows), next to
the transcript log. Replaces editing .env by hand, which a packaged install won't have.

The rest of ALICE still reads these as environment variables (PTT_HOTKEY,
OVERLAY_HOTKEY, WHISPER_MODEL, ALICE_MIC_DEVICE), so apply_to_env() is the whole
hand-off: the launcher sets them just before starting voice + overlay, and nothing
downstream had to learn about this file. A developer's .env still works — settings
saved here win over it once the pilot has changed them in the launcher.
"""
import json
import os
from pathlib import Path

from pydantic import BaseModel

from turn_metrics import alice_data_dir

WHISPER_MODELS = ["small", "base"]


class PilotSettings(BaseModel):
    # pynput key name (keyboard.Key member, e.g. "shift_r", or a single character) —
    # the format voice_input._parse_hotkey already reads.
    ptt_hotkey: str = "shift_r"
    # pynput GlobalHotKeys format: "<f3>" for a named key, the bare character otherwise.
    overlay_hotkey: str = "<f3>"
    # sounddevice input device name; None means the system default.
    mic_device: str | None = None
    whisper_model: str = "small"


def settings_path() -> Path:
    return alice_data_dir() / "settings.json"


def load() -> PilotSettings:
    """Saved settings, or the defaults seeded from the environment (a developer's .env)
    when nothing's been saved yet. A corrupt file falls back the same way rather than
    stopping ALICE from starting."""
    path = settings_path()
    if path.exists():
        try:
            return PilotSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return PilotSettings(
        ptt_hotkey=os.getenv("PTT_HOTKEY", "shift_r"),
        overlay_hotkey=os.getenv("OVERLAY_HOTKEY", "<f3>"),
        mic_device=os.getenv("ALICE_MIC_DEVICE") or None,
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
    )


def save(settings: PilotSettings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def apply_to_env(settings: PilotSettings) -> None:
    os.environ["PTT_HOTKEY"] = settings.ptt_hotkey
    os.environ["OVERLAY_HOTKEY"] = settings.overlay_hotkey
    os.environ["WHISPER_MODEL"] = settings.whisper_model
    if settings.mic_device:
        os.environ["ALICE_MIC_DEVICE"] = settings.mic_device
    else:
        os.environ.pop("ALICE_MIC_DEVICE", None)
