"""Is there a newer ALICE than this one? For the launcher's "update available" banner.

Reads the stable download link's redirect rather than a separate version file: the
publish script points https://heyalice.help/download/ALICE-Setup.exe at the newest
ALICE-Setup-<version>.exe (docs/deploy.md, "Installer downloads"), so the version the
launcher announces is by construction the one the link hands out — there's no second
file to fall out of step if one publish step fails. The redirect is never followed, so
checking downloads nothing.

Deliberately notify-only: ALICE never downloads or runs an installer by itself. The
pilot clicks through to their browser and installs over the top (settings and logs
live elsewhere and survive).
"""
import re

import httpx

STABLE_DOWNLOAD_URL = "https://heyalice.help/download/ALICE-Setup.exe"
TIMEOUT_S = 5.0
_VERSIONED_NAME = re.compile(r"ALICE-Setup-(\d+(?:\.\d+)*)\.exe$")


def version_from_location(location: str) -> str | None:
    match = _VERSIONED_NAME.search(location)
    return match.group(1) if match else None


def is_newer(candidate: str, current: str) -> bool:
    """Numeric, part by part: 0.2.10 is newer than 0.2.9; 0.3 equals 0.3.0."""
    def parts(version):
        numbers = [int(part) for part in version.split(".")]
        while numbers and numbers[-1] == 0:
            numbers.pop()
        return numbers

    return parts(candidate) > parts(current)


async def latest_version(client: httpx.AsyncClient | None = None) -> str | None:
    """The newest published version, or None if it can't be found out right now
    (offline, server down, redirect not set up) — the caller just shows nothing."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
    try:
        response = await client.head(STABLE_DOWNLOAD_URL)
        if not response.is_redirect:
            return None
        return version_from_location(response.headers.get("location", ""))
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            await client.aclose()


async def available_update(current: str | None) -> str | None:
    """The newer version to tell the pilot about, or None."""
    if not current:
        return None
    latest = await latest_version()
    if latest is None or not is_newer(latest, current):
        return None
    return latest
