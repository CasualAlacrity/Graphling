"""Where ALICE's shipped, read-only files live — the repo when running from source, the
PyInstaller bundle when running as the packaged ALICE.exe (packaging/alice.spec).

Only for files outside the Python packages themselves: assets that sit next to a module
(overlay/assets, launcher/assets) are found via that module's own __file__, which
PyInstaller keeps pointing inside the bundle. These are repo-root files (.env-pilot,
and the prompt snapshot when running from source), which Path(__file__).parent.parent
would miss once frozen.
"""
import sys
import tomllib
from pathlib import Path

from dotenv import load_dotenv

PILOT_ENV_FILENAME = ".env-pilot"
# Written into the bundle by packaging/alice.spec from pyproject.toml's version.
VERSION_FILENAME = "VERSION"


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


def app_version() -> str | None:
    """ALICE's own version, always from pyproject.toml — the same number the build and
    the installer's file name use. Not importlib.metadata: an editable dev install
    records its version once, at install time, and goes stale on the next bump (it said
    0.1.0 here at 0.2.2), which would make a dev copy, or a build made without re-running
    setup.bat, report the wrong version and nag about phantom updates. The packaged app
    has no pyproject.toml, so the build bakes the number into a VERSION file instead."""
    try:
        if is_frozen():
            return (root() / VERSION_FILENAME).read_text(encoding="utf-8").strip() or None
        with open(root() / "pyproject.toml", "rb") as pyproject:
            return tomllib.load(pyproject)["project"]["version"]
    except (OSError, KeyError, ValueError):
        return None
