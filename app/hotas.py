"""Joystick/HOTAS buttons as push-to-talk and overlay keys.

pynput (voice_input, the overlay's GlobalHotKeys, the launcher's key capture) only sees
the keyboard; a VKB/Virpil/Thrustmaster button is a separate kind of Windows input it
never hears about. This reads sticks through SDL2 (pysdl2 + the pysdl2-dll binaries) on
one background thread, and hands every button press/release to whoever's listening:
the launcher while it captures a binding, voice_input and the overlay at runtime.

SDL rather than Windows' own joystick API (WinMM): WinMM only reports a device's first
32 buttons, and VKB setups routinely go past that with shift layers and virtual buttons.
SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS is what makes this work at all — without it SDL
only reports input while one of ALICE's own windows has focus, and the pilot's focus is
Star Citizen.

A binding is stored as a string alongside keyboard ones ("joy:<guid>:<n>:<button>:<name>")
so settings.json and the env-var hand-off stay one string per key. The GUID identifies
the device model across reboots and USB ports; <n> tells apart two identical devices
(a matched left/right stick pair can share a GUID) by their order among that GUID, which
is stable as long as they enumerate in the same order. The name is only for display.
"""
import ctypes
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field

PREFIX = "joy:"
# How long one wait for an SDL event blocks before checking whether to stop — also the
# worst-case added latency on a press, well under what a pilot could notice.
POLL_TIMEOUT_MS = 20


@dataclass(frozen=True)
class JoyBinding:
    guid: str
    ordinal: int
    button: int
    name: str = field(default="", compare=False)

    def to_string(self) -> str:
        return f"{PREFIX}{self.guid}:{self.ordinal}:{self.button}:{self.name}"

    def display(self) -> str:
        # Button first: it's the part a pilot needs, and the device name is what gets
        # shortened when space runs out (the launcher's key buttons).
        device = self.name or "Joystick"
        return f"Button {self.button + 1} · {device}"


def parse(hotkey: str) -> JoyBinding | None:
    """The JoyBinding a stored hotkey string names, or None for a keyboard key."""
    if not hotkey.startswith(PREFIX):
        return None
    parts = hotkey[len(PREFIX):].split(":", 3)
    if len(parts) < 3:
        return None
    try:
        return JoyBinding(parts[0], int(parts[1]), int(parts[2]), parts[3] if len(parts) > 3 else "")
    except ValueError:
        return None


Listener = Callable[[JoyBinding, bool], None]


class JoystickWatcher:
    """One SDL thread for the whole process, started on first use. Listeners are called
    from that thread as listener(binding, pressed) — they must hand off to their own
    thread (a threading.Event, a Qt signal) rather than do work there."""

    def __init__(self):
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # None until the thread has tried SDL; False when SDL couldn't start (no
        # binaries, no joystick subsystem) — ALICE then simply works keyboard-only.
        self.available: bool | None = None

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)
        self.start()

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="hotas", daemon=True)
            self._thread.start()

    def _emit(self, binding: JoyBinding, pressed: bool) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(binding, pressed)
            except Exception as exc:  # one bad listener mustn't kill the stick for the rest
                print(f"[HOTAS] listener failed: {exc}")

    def _run(self) -> None:
        try:
            with warnings.catch_warnings():
                # pysdl2 announces which SDL2 binaries it found, as a UserWarning.
                warnings.simplefilter("ignore")
                import sdl2
        except Exception as exc:
            self.available = False
            print(f"[HOTAS] Joystick support unavailable ({exc}); keyboard keys still work.")
            return

        sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")
        if sdl2.SDL_Init(sdl2.SDL_INIT_JOYSTICK) != 0:
            self.available = False
            print(f"[HOTAS] Joystick support unavailable ({sdl2.SDL_GetError().decode(errors='replace')}).")
            return
        self.available = True

        # instance id -> (handle, guid, ordinal, name)
        devices: dict[int, tuple] = {}
        # Each button's first press this session is logged, so a pilot's alice.log shows
        # exactly which button numbers their stick sends — VKB buttons can send more
        # than one (shift layers, virtual buttons), and a binding that never fires is
        # otherwise invisible. Bounded: one line per button, ever.
        seen_buttons: set[JoyBinding] = set()
        event = sdl2.SDL_Event()
        while not self._stop.is_set():
            if not sdl2.SDL_WaitEventTimeout(ctypes.byref(event), POLL_TIMEOUT_MS):
                continue
            if event.type == sdl2.SDL_JOYDEVICEADDED:
                self._open(sdl2, event.jdevice.which, devices)
            elif event.type == sdl2.SDL_JOYDEVICEREMOVED:
                device = devices.pop(event.jdevice.which, None)
                if device is not None:
                    sdl2.SDL_JoystickClose(device[0])
                    print(f"[HOTAS] Disconnected: {device[3]}")
            elif event.type in (sdl2.SDL_JOYBUTTONDOWN, sdl2.SDL_JOYBUTTONUP):
                device = devices.get(event.jbutton.which)
                if device is not None:
                    _, guid, ordinal, name = device
                    binding = JoyBinding(guid, ordinal, event.jbutton.button, name)
                    pressed = event.type == sdl2.SDL_JOYBUTTONDOWN
                    if pressed and binding not in seen_buttons:
                        seen_buttons.add(binding)
                        print(f"[HOTAS] First press: {binding.display()} ({binding.to_string()})")
                    self._emit(binding, pressed)
        sdl2.SDL_Quit()

    @staticmethod
    def _open(sdl2, device_index: int, devices: dict) -> None:
        handle = sdl2.SDL_JoystickOpen(device_index)
        if not handle:
            return
        instance_id = sdl2.SDL_JoystickInstanceID(handle)
        if instance_id in devices:
            return
        buffer = ctypes.create_string_buffer(33)
        sdl2.SDL_JoystickGetGUIDString(sdl2.SDL_JoystickGetGUID(handle), buffer, 33)
        guid = buffer.value.decode()
        raw_name = sdl2.SDL_JoystickName(handle)
        name = raw_name.decode(errors="replace").strip() if raw_name else "Joystick"
        ordinal = sum(1 for device in devices.values() if device[1] == guid)
        devices[instance_id] = (handle, guid, ordinal, name)
        print(f"[HOTAS] Connected: {name}")


_watcher = JoystickWatcher()


def watcher() -> JoystickWatcher:
    return _watcher
