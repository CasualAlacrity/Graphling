"""One-shot "press the key you want" capture for the launcher's PTT/overlay key buttons.

Captured with pynput, not Qt's own key events, on purpose: pynput is what listens for
these keys at runtime (voice_input for PTT, GlobalHotKeys for the overlay), so naming
the key the way pynput sees it means what the pilot pressed here is exactly what ALICE
listens for later — no Qt-to-pynput key translation table to get wrong.

Joystick/HOTAS buttons are captured the same way, through hotas.py — the same reader
that listens for them at runtime. Whichever comes first, a key or a stick button, wins.
"""
from pynput import keyboard
from PySide6.QtCore import QObject, Signal

import hotas

CANCEL_KEY = keyboard.Key.esc

_DISPLAY_NAMES = {
    "shift": "Left Shift", "shift_l": "Left Shift", "shift_r": "Right Shift",
    "ctrl": "Left Ctrl", "ctrl_l": "Left Ctrl", "ctrl_r": "Right Ctrl",
    "alt": "Left Alt", "alt_l": "Left Alt", "alt_r": "Right Alt", "alt_gr": "AltGr",
    "cmd": "Left Win", "cmd_l": "Left Win", "cmd_r": "Right Win",
    "caps_lock": "Caps Lock", "page_up": "Page Up", "page_down": "Page Down",
    "num_lock": "Num Lock", "scroll_lock": "Scroll Lock", "print_screen": "Print Screen",
}


def ptt_name(key) -> str:
    """The PTT_HOTKEY string for a pynput key — voice_input._parse_hotkey's format."""
    if isinstance(key, keyboard.Key):
        return key.name
    if key.char:
        return key.char.lower()
    return f"<{key.vk}>"


def overlay_name(key) -> str:
    """The OVERLAY_HOTKEY string for a pynput key — GlobalHotKeys' format."""
    if isinstance(key, keyboard.Key):
        return f"<{key.name}>"
    if key.char:
        return key.char.lower()
    return f"<{key.vk}>"


def display_name(hotkey: str) -> str:
    """What the launcher shows for a stored key string, either format: "shift_r" and
    "<shift_r>" are both "Right Shift", "<f3>" is "F3", "v" is "V", and a stick button
    is "<device> · Button 5"."""
    joy_binding = hotas.parse(hotkey)
    if joy_binding is not None:
        return joy_binding.display()
    name = hotkey.strip("<>") if hotkey.startswith("<") else hotkey
    if name.isdigit():
        return f"Key {name}"
    if name in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[name]
    return name.replace("_", " ").title() if len(name) > 1 else name.upper()


class KeyCapture(QObject):
    """Listens for exactly one key press or stick button, then stops. captured(key)
    carries the pynput key; captured_joystick(binding) the stored string for a stick
    button; cancelled fires on Esc. Signals cross from the listener threads back to the
    Qt thread on their own (queued connection), so the slots can touch widgets."""

    captured = Signal(object)
    captured_joystick = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None
        self._joy_listening = False

    def start(self) -> None:
        self.stop()
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.start()
        hotas.watcher().add_listener(self._on_joy)
        self._joy_listening = True

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._joy_listening:
            hotas.watcher().remove_listener(self._on_joy)
            self._joy_listening = False

    def _on_joy(self, binding: hotas.JoyBinding, pressed: bool) -> None:
        if pressed and self._joy_listening:
            # Stop listening straight away: one press can arrive alongside others
            # (a shifted button, a trigger's two stages) before the Qt slot runs.
            self._joy_listening = False
            hotas.watcher().remove_listener(self._on_joy)
            self.captured_joystick.emit(binding.to_string())

    def _on_press(self, key):
        if key == CANCEL_KEY:
            self.cancelled.emit()
        else:
            self.captured.emit(key)
        return False  # stops the listener — one key per capture
