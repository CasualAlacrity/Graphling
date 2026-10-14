"""How far the voice side has got with starting up, for the launcher to show.

Voice starts on its own thread after the overlay is up (overlay_app.start_voice), and
its slow part — downloading and loading the Whisper model, ~0.5 GB on a first launch —
used to be one log line in the launcher's small status text, easily read as ALICE
having hung. The voice thread report()s each stage here; the launcher registers one
listener and turns it into its Start button text and progress bar. Qt-free on purpose,
so the voice side doesn't import any UI.
"""
from collections.abc import Callable

DOWNLOADING_VOICE = "downloading_voice"
LOADING_VOICE = "loading_voice"
READY = "ready"

# listener(stage, done_bytes, total_bytes) — the byte counts only for DOWNLOADING_VOICE,
# and total_bytes is None when the download's size couldn't be found out up front.
Listener = Callable[[str, int | None, int | None], None]

_listener: Listener | None = None


def set_listener(listener: Listener | None) -> None:
    global _listener
    _listener = listener


def report(stage: str, done_bytes: int | None = None, total_bytes: int | None = None) -> None:
    if _listener is not None:
        try:
            _listener(stage, done_bytes, total_bytes)
        except Exception as exc:  # a display problem mustn't stop ALICE starting
            print(f"[ALICE] Couldn't show startup progress ({exc}).")
