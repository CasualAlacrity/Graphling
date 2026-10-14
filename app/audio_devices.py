"""Microphone/speaker lookup by name, shared by the launcher's pickers and the voice loop.

Windows lists every physical device once per host API (MME, DirectSound, WASAPI, WDM-KS),
under the same name or nearly. Both halves here stick to the default host API — what
sounddevice opens when no device is given, so a picked device behaves like the default
one did — and resolve() matches names exactly. sounddevice's own name lookup is a
substring match that raises "Multiple devices found" for exactly this duplication,
which would quietly drop every pilot back to the system default.

Kept outside the voice package so the launcher can list devices without importing
voice/ (whose __init__ pulls in Whisper and the whole LangGraph stack).
"""
import sounddevice as sd


def _default_hostapi() -> int | None:
    try:
        return sd.default.hostapi
    except Exception:
        return None


def device_names(kind: str) -> list[str]:
    """Names of every "input" or "output" device on the default host API, for a picker."""
    channels_key = f"max_{kind}_channels"
    hostapi = _default_hostapi()
    names = []
    try:
        devices = sd.query_devices()
    except Exception:  # no audio subsystem at all — the picker just offers the default
        return names
    for device in devices:
        if device[channels_key] <= 0 or device["name"] in names:
            continue
        if hostapi is not None and device["hostapi"] != hostapi:
            continue
        names.append(device["name"])
    return names


def resolve(name: str | None, kind: str) -> int | None:
    """The device index for a saved name, or None for the system default — including
    when the saved device is gone (an unplugged headset), rather than failing."""
    if not name:
        return None
    channels_key = f"max_{kind}_channels"
    hostapi = _default_hostapi()
    fallback = None
    try:
        devices = sd.query_devices()
    except Exception:
        return None
    for index, device in enumerate(devices):
        if device["name"] != name or device[channels_key] <= 0:
            continue
        if hostapi is None or device["hostapi"] == hostapi:
            return index
        if fallback is None:
            fallback = index
    if fallback is None:
        print(f"[Voice] {kind.title()} device '{name}' not found; using the system default.")
    return fallback
