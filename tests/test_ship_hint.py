"""Covers voice/ship_hint.build_ship_hint: the Whisper vocabulary hint has to fit
faster-whisper's 223-token prompt window, or the front of it is silently cut — which is
how Railen fell out of the old ~1,400-token hint and kept coming back as "railing".

The token counter here is a stand-in (one token per 4 characters, at least 1, plus one
per separator), so the tests pin the ranking and fitting logic, not Whisper's tokenizer.
"""
from voice.ship_hint import build_ship_hint


def _count(text):
    total = text.count(",")
    for word in text.split(","):
        total += max(1, len(word.strip()) // 4)
    return total


def test_stays_within_the_budget():
    names = [f"Shipname{index:03d}xx" for index in range(200)]
    hint = build_ship_hint(names, [], _count, budget=50)
    assert _count(" " + hint) <= 50


def test_priority_names_are_kept_and_placed_last():
    names = [f"Longshipname{index}" for index in range(100)]
    hint = build_ship_hint(names, ["Railen"], _count, budget=30)
    assert hint.split(", ")[-1] == "Railen"


def test_model_codes_and_common_words_are_left_out():
    hint = build_ship_hint(["Cutlass Black", "C2 Hercules Starlifter", "600i Touring"], [], _count, budget=200)
    words = hint.split(", ")
    assert "C2" not in words
    assert "600i" not in words
    # "Black" is one token by this counter (5 chars // 4 = 1): Whisper needs no help.
    assert "Black" not in words
    assert "Starlifter" in words


def test_each_word_appears_once():
    hint = build_ship_hint(["Constellation Mk IV Taurus", "Constellation Mk IV Andromeda"], [], _count)
    assert hint.split(", ").count("Constellation") == 1
