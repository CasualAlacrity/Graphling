"""Covers hotas.py's binding strings and dispatch, and the joystick paths into PTT
(voice_input) and the launcher's key labels. No real stick or SDL involved — bindings
are built directly and pushed through the same listener calls the SDL thread makes."""
import hotas
from launcher.key_capture import display_name
from voice import voice_input

STICK = hotas.JoyBinding("030000003412000001a0000000000000", 0, 11, "VKB-Sim Gladiator NXT EVO R")


def test_binding_round_trips_through_its_string():
    assert hotas.parse(STICK.to_string()) == STICK
    assert hotas.parse(STICK.to_string()).name == STICK.name


def test_device_name_with_colons_survives():
    binding = hotas.JoyBinding("abc", 1, 3, "Virpil: Constellation Alpha")
    assert hotas.parse(binding.to_string()).name == "Virpil: Constellation Alpha"


def test_keyboard_keys_are_not_bindings():
    for key in ("shift_r", "<f3>", "v", "<123>"):
        assert hotas.parse(key) is None


def test_malformed_binding_is_rejected():
    assert hotas.parse("joy:abc:notanumber:3:Stick") is None
    assert hotas.parse("joy:abc") is None


def test_name_is_ignored_when_matching():
    # A renamed device (driver update, VKBDevCfg profile) is still the same button.
    assert hotas.JoyBinding(STICK.guid, 0, 11, "Something else") == STICK


def test_second_identical_stick_is_a_different_binding():
    assert hotas.JoyBinding(STICK.guid, 1, 11, STICK.name) != STICK


def test_display_counts_buttons_from_one():
    assert STICK.display() == "Button 12 · VKB-Sim Gladiator NXT EVO R"
    assert display_name(STICK.to_string()) == STICK.display()


def test_watcher_dispatches_to_listeners_and_survives_a_bad_one():
    watcher = hotas.JoystickWatcher()
    seen = []

    def _broken(binding, pressed):
        raise RuntimeError("boom")

    watcher._listeners = [_broken, lambda binding, pressed: seen.append((binding, pressed))]
    watcher._emit(STICK, True)
    assert seen == [(STICK, True)]


def test_stick_button_drives_push_to_talk(monkeypatch):
    monkeypatch.setattr(voice_input, "_target_joy", STICK)
    voice_input._pressed.clear()
    voice_input._released.clear()

    voice_input._on_joy(hotas.JoyBinding(STICK.guid, 0, 4), True)  # a different button
    assert not voice_input._pressed.is_set()

    voice_input._on_joy(STICK, True)
    assert voice_input._pressed.is_set() and not voice_input._released.is_set()
    voice_input._on_joy(STICK, False)
    assert voice_input._released.is_set()


def test_stick_button_ignored_when_ptt_is_a_keyboard_key(monkeypatch):
    monkeypatch.setattr(voice_input, "_target_joy", None)
    voice_input._pressed.clear()
    voice_input._on_joy(STICK, True)
    assert not voice_input._pressed.is_set()
