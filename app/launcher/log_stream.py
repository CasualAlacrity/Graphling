"""Where ALICE's print() output goes once there's no console.

A packaged, windowed install has no terminal: sys.stdout/sys.stderr are None, so every
"[ALICE] ..." status line and every traceback would vanish. install() replaces both with
a tee that (1) appends each line to alice.log in the pilot's data folder — what "Open
logs" shows and what a pilot can send when something breaks — (2) emits it to the
launcher's status line, and (3) still writes to the real console when there is one, so
running from a terminal during development looks the same as before.
"""
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from turn_metrics import alice_data_dir

LOG_FILENAME = "alice.log"


class LogSignals(QObject):
    # Emitted from whichever thread printed (voice runs on its own) — Qt queues it onto
    # the GUI thread for any widget slot connected to it.
    line = Signal(str)


signals = LogSignals()


class _Tee:
    def __init__(self, original, log_file, lock):
        self._original = original
        self._log_file = log_file
        self._lock = lock
        self._pending = ""

    def write(self, text: str) -> int:
        if self._original is not None:
            try:
                self._original.write(text)
            except (OSError, ValueError):
                pass
        with self._lock:
            self._pending += text
            *lines, self._pending = self._pending.split("\n")
            for line in lines:
                self._log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n")
            self._log_file.flush()
        for line in lines:
            if line.strip():
                signals.line.emit(line.strip())
        return len(text)

    def flush(self) -> None:
        if self._original is not None:
            try:
                self._original.flush()
            except (OSError, ValueError):
                pass

    def isatty(self) -> bool:
        return False


def install() -> None:
    log_dir = alice_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    # Appended, not overwritten — the last session's log is often the one a pilot wants
    # to send after a crash, and they'll usually have restarted ALICE by then.
    log_file = open(log_dir / LOG_FILENAME, "a", encoding="utf-8")  # noqa: SIM115 -- lives as long as the app
    log_file.write(f"\n===== ALICE started {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    lock = threading.Lock()
    sys.stdout = _Tee(sys.stdout, log_file, lock)
    sys.stderr = _Tee(sys.stderr, log_file, lock)
