"""Covers vocabulary/transcript.correct_transcript: known, unambiguous mishearings are
fixed in the raw transcript, whole words only, and ambiguous ones are left for the
resolver ("Raven" may really be the Sabre Raven)."""
from vocabulary.transcript import correct_transcript


def test_a_known_mishearing_is_fixed_in_free_text():
    assert correct_transcript("Uh, I'll take my railing.") == "Uh, I'll take my Railen."


def test_case_does_not_matter():
    assert correct_transcript("RAILING to Orison") == "Railen to Orison"


def test_only_whole_words_are_fixed():
    assert correct_transcript("derailing the railings") == "derailing the railings"


def test_an_ambiguous_mishearing_is_left_for_the_resolver():
    assert correct_transcript("How fast is the Raven?") == "How fast is the Raven?"


def test_text_without_mishearings_is_unchanged():
    assert correct_transcript("Where can I sell Iron in Stanton?") == "Where can I sell Iron in Stanton?"
