# PyInstaller recipe for ALICE.exe — run by scripts/windows/build-installer.bat, which
# then wraps the output folder into ALICE-Setup.exe (packaging/alice.iss).
#
# One folder (dist/ALICE/), not --onefile: a one-file exe unpacks itself to a temp folder
# on every launch (slow with Qt + Whisper's libraries) and is the shape antivirus tools
# flag most often. The installer is what makes it feel like one thing to the pilot.
import os
import tomllib

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

REPO = os.path.abspath(os.path.join(SPECPATH, ".."))
APP = os.path.join(REPO, "app")

datas = [
    # Assets found via their module's own __file__ — same relative spot in the bundle.
    (os.path.join(APP, "overlay", "assets"), os.path.join("overlay", "assets")),
    (os.path.join(APP, "launcher", "assets"), os.path.join("launcher", "assets")),
    # Repo-root file, found via bundle.root() (app/bundle.py). Deliberately NOT the
    # prompt snapshot: pilots go through ALICE's gateway, which swaps the prompts in
    # server-side (server/prompt_service.py), so their text never ships in the installer.
    (os.path.join(REPO, ".env-pilot"), "."),
]
# ALICE's version, from pyproject.toml — read back by bundle.app_version() for the
# launcher's version label and update check. Written fresh on every build so it can't
# go stale the way an editable install's package metadata does.
with open(os.path.join(REPO, "pyproject.toml"), "rb") as pyproject:
    VERSION = tomllib.load(pyproject)["project"]["version"]
VERSION_FILE = os.path.join(workpath, "VERSION")
os.makedirs(workpath, exist_ok=True)
with open(VERSION_FILE, "w", encoding="utf-8") as version_file:
    version_file.write(VERSION)
datas.append((VERSION_FILE, "."))

# faster-whisper ships its voice-activity model (Silero VAD, ONNX) as package data.
datas += collect_data_files("faster_whisper")

binaries = collect_dynamic_libs("ctranslate2")
# SDL2 for joystick/HOTAS buttons (app/hotas.py): pysdl2 finds the DLLs inside the
# sdl2dll package's own folder, so they go there in the bundle too.
binaries += collect_dynamic_libs("sdl2dll")
datas += collect_data_files("sdl2dll")

hiddenimports = [
    # pynput picks its keyboard/mouse backend by platform at import time, which
    # PyInstaller's static analysis can't see.
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    "pynput._util.win32",
    # tiktoken (via langchain-openai) finds its encodings as a namespace plugin.
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    # Imported inside hotas.py's reader thread, not at module top.
    "sdl2",
    "sdl2dll",
]

a = Analysis(
    [os.path.join(SPECPATH, "alice_entry.py")],
    pathex=[APP],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Never needed by ALICE, but pulled in whenever a build machine's venv still has
    # them installed: openai-whisper (replaced by faster-whisper) brings PyTorch and
    # numba/llvmlite, ~530 MB of the first trial build.
    excludes=["tkinter", "torch", "whisper", "numba", "llvmlite", "tensorboard"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ALICE",
    icon=os.path.join(SPECPATH, "alice.ico"),
    # No console window — the launcher is the UI, and alice.log (launcher/log_stream.py)
    # catches what used to print to the console.
    console=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="ALICE",
    upx=False,
)
