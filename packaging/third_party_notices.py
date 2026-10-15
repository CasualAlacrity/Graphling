"""Write THIRD_PARTY_NOTICES.txt: the license text of everything shipped inside ALICE.

Run by packaging/alice.spec on every installer build, so the notices always match the
exact package versions installed in the build's .venv. Run it by hand to check the output:

    .venv/bin/python packaging/third_party_notices.py [output path]

What goes in:
1. Every package that requirements.txt resolves to in this venv (its Requires-Dist chain,
   with platform markers evaluated, so a Windows build lists Windows dependencies), plus
   PyInstaller, whose bootloader is part of ALICE.exe. This includes the server-only
   packages (fastapi, uvicorn, ...) even though the client never imports them. Listing a
   package that isn't shipped is harmless; missing one that is shipped is not.
2. Each package's own license files, read from its installed metadata.
3. For packages whose wheels carry no license file, the text kept in notices/packages/
   (fetched from the project's repository). A package with neither stops the build, so a
   new dependency can't ship without its notice.
4. notices/bundled-components.txt: native libraries inside those packages (Qt, FFmpeg,
   PortAudio, ...) that have no metadata of their own, followed by the GPL/LGPL texts.
5. The Python runtime's own license.
"""
import importlib.metadata
import os
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PACKAGING_DIR = Path(__file__).resolve().parent
REPO = PACKAGING_DIR.parent
NOTICES_DIR = PACKAGING_DIR / "notices"
DEFAULT_OUTPUT = PACKAGING_DIR / "build" / "THIRD_PARTY_NOTICES.txt"

# Shipped, but not reachable from requirements.txt.
EXTRA_ROOTS = ["pyinstaller"]

LICENSE_NAME_HINTS = ("LICENSE", "LICENCE", "COPYING", "NOTICE")
# Files that match the hints but aren't license text: PySide6's wheels carry the *Qt
# commercial* license reference, which isn't the license ALICE uses Qt under (LGPL-3.0).
SKIPPED_LICENSE_FILES = {"LicenseRef-Qt-Commercial.txt"}
LICENSE_FILE_SUFFIXES = {"", ".txt", ".md", ".rst", ".lgpl", ".gpl", ".psf", ".apache", ".apache2",
                         ".mit", ".bsd", ".mpl-2.0"}

# Packages whose wheels carry no license file -> the text in notices/packages/ to use.
# PySide6 and Shiboken6 are LGPL-3.0, which incorporates GPL-3.0, so both texts go in.
FALLBACK_TEXTS = {
    "ctranslate2": ["packages/ctranslate2.txt"],
    "flatbuffers": ["packages/flatbuffers.txt"],
    "langchain-community": ["packages/langchain-community.txt"],
    "langchain-core": ["packages/langchain.txt"],
    "langchain-text-splitters": ["packages/langchain.txt"],
    "langsmith": ["packages/langsmith.txt"],
    "pyobjc-core": ["packages/pyobjc.txt"],
    "pyobjc-framework-applicationservices": ["packages/pyobjc.txt"],
    "pyside6": ["licenses/LGPL-3.0.txt", "licenses/GPL-3.0.txt"],
    "pyside6-addons": ["licenses/LGPL-3.0.txt", "licenses/GPL-3.0.txt"],
    "pyside6-essentials": ["licenses/LGPL-3.0.txt", "licenses/GPL-3.0.txt"],
    "shiboken6": ["licenses/LGPL-3.0.txt", "licenses/GPL-3.0.txt"],
    "tokenizers": ["packages/tokenizers.txt"],
}

# Appended after bundled-components.txt, in this order.
COMPONENT_TEXTS = [
    ("PortAudio", "packages/portaudio.txt"),
    ("Silero VAD", "packages/silero-vad.txt"),
    ("Whisper", "packages/whisper.txt"),
    ("GNU General Public License v2.0", "licenses/GPL-2.0.txt"),
    ("GNU Lesser General Public License v2.1", "licenses/LGPL-2.1.txt"),
    ("GNU General Public License v3.0", "licenses/GPL-3.0.txt"),
    ("GNU Lesser General Public License v3.0", "licenses/LGPL-3.0.txt"),
]

RULE = "=" * 79


def requirement_roots() -> list[Requirement]:
    roots = []
    with open(REPO / "requirements.txt", encoding="utf-8") as requirements:
        for line in requirements:
            line = line.split("#")[0].strip()
            if line:
                roots.append(Requirement(line))
    for name in EXTRA_ROOTS:
        roots.append(Requirement(name))
    return roots


def resolve_installed(roots: list[Requirement]) -> dict[str, importlib.metadata.Distribution]:
    """Every installed distribution reachable from roots, keyed by canonical name."""
    marker_env = default_environment()
    marker_env["extra"] = ""
    found = {}
    pending = list(roots)
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        if name in found:
            continue
        try:
            dist = importlib.metadata.distribution(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            # PyInstaller is only installed by build-installer.bat; everything else
            # missing means the venv doesn't match requirements.txt.
            if name in EXTRA_ROOTS:
                continue
            raise SystemExit(f"third_party_notices: {requirement.name} is in requirements.txt "
                             f"but not installed in this venv.") from None
        found[name] = dist
        for dependency in dist.requires or []:
            child = Requirement(dependency)
            if child.marker is not None and not child.marker.evaluate(marker_env):
                continue
            pending.append(child)
    return found


def is_license_file(path) -> bool:
    filename = path.name
    if filename in SKIPPED_LICENSE_FILES:
        return False
    if Path(filename).suffix.lower() not in LICENSE_FILE_SUFFIXES:
        return False
    in_licenses_dir = "licenses" in path.parts or "licensing" in path.parts
    named_like_license = any(hint in filename.upper() for hint in LICENSE_NAME_HINTS)
    return in_licenses_dir or named_like_license


def license_texts(dist: importlib.metadata.Distribution) -> list[tuple[str, str]]:
    """(file name, text) for each license file the distribution ships."""
    texts = []
    seen_texts = set()
    for path in dist.files or []:
        if not is_license_file(path):
            continue
        located = Path(dist.locate_file(path))
        if not located.is_file():
            continue
        text = located.read_text(encoding="utf-8", errors="replace").strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        texts.append((str(path), text))
    return texts


def fallback_texts(name: str) -> list[tuple[str, str]]:
    texts = []
    for relative in FALLBACK_TEXTS.get(name, []):
        text = (NOTICES_DIR / relative).read_text(encoding="utf-8").strip()
        texts.append((relative, text))
    return texts


def license_label(dist: importlib.metadata.Distribution) -> str:
    expression = dist.metadata.get("License-Expression")
    if expression:
        return expression
    legacy = (dist.metadata.get("License") or "").strip()
    # Some packages paste the whole license into this field; the text below has it anyway.
    if legacy and "\n" not in legacy and len(legacy) < 80:
        return legacy
    for classifier in dist.metadata.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            return classifier.split("::")[-1].strip()
    return "see text below"


def homepage(dist: importlib.metadata.Distribution) -> str:
    if dist.metadata.get("Home-page"):
        return dist.metadata["Home-page"]
    for entry in dist.metadata.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository", "source code"):
            return url.strip()
    return f"https://pypi.org/project/{dist.metadata['Name']}/"


def python_runtime_license() -> str:
    candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace").strip()
    raise SystemExit(f"third_party_notices: can't find the Python runtime's LICENSE under {sys.base_prefix}.")


def section(title: str, body: str) -> str:
    return f"{RULE}\n{title}\n{RULE}\n\n{body}\n\n"


def build_notices() -> str:
    dists = resolve_installed(requirement_roots())

    parts = [
        "ALICE - third-party software notices\n\n"
        "ALICE is built with the open-source software listed below. Each component is used\n"
        "under its own license, reproduced here. ALICE's own code is not covered by these\n"
        "licenses.\n\n"
    ]

    missing = []
    for name in sorted(dists):
        dist = dists[name]
        texts = license_texts(dist)
        if not texts:
            texts = fallback_texts(name)
        if not texts:
            missing.append(name)
            continue
        header = (f"{dist.metadata['Name']} {dist.version}\n"
                  f"License: {license_label(dist)}\n"
                  f"Source:  {homepage(dist)}")
        bodies = []
        for filename, text in texts:
            bodies.append(f"--- {filename} ---\n\n{text}")
        parts.append(section(header, "\n\n".join(bodies)))

    if missing:
        raise SystemExit("third_party_notices: no license text for " + ", ".join(missing) +
                         ". Add the project's LICENSE to packaging/notices/packages/ and map it in "
                         "FALLBACK_TEXTS.")

    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    parts.append(section(f"Python {version} runtime\nLicense: PSF-2.0\nSource:  https://www.python.org/",
                         python_runtime_license()))

    components = (NOTICES_DIR / "bundled-components.txt").read_text(encoding="utf-8").strip()
    parts.append(f"{RULE}\n{components}\n\n")
    for title, relative in COMPONENT_TEXTS:
        text = (NOTICES_DIR / relative).read_text(encoding="utf-8").strip()
        parts.append(section(title, text))

    return "".join(parts)


def write_notices(output: Path = DEFAULT_OUTPUT) -> Path:
    output = Path(output)
    os.makedirs(output.parent, exist_ok=True)
    output.write_text(build_notices(), encoding="utf-8")
    return output


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    print(write_notices(target))
