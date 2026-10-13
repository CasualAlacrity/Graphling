"""Where ALICE's shipped, read-only files live — the repo when running from source, the
PyInstaller bundle when running as the packaged ALICE.exe (packaging/alice.spec).

Only for files outside the Python packages themselves: assets that sit next to a module
(overlay/assets, launcher/assets) are found via that module's own __file__, which
PyInstaller keeps pointing inside the bundle. These are the repo-root files a module
reached with Path(__file__).parent.parent, which leaves the bundle once frozen.
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

PILOT_ENV_FILENAME = ".env-pilot"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def root() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def load_pilot_defaults() -> None:
    """A packaged install has no .env — the bundled .env-pilot (the same template
    setup.bat copies to .env) supplies the server URL and providers instead. Never
    overrides a variable already set, so the launcher's own settings and anything set
    in the environment still win."""
    if is_frozen():
        load_dotenv(root() / PILOT_ENV_FILENAME, override=False)
