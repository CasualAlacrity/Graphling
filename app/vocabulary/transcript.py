"""Fixes known, unambiguous speech-to-text mishearings in a transcript before the model
reads it ("my railing" -> "my Railen"). See aliases.TRANSCRIPT_CORRECTIONS for which
mishearings qualify and why the ambiguous ones don't."""
import re

from vocabulary.aliases import TRANSCRIPT_CORRECTIONS


def _pattern(heard: str) -> re.Pattern:
    # Whole words only, so "railings" or "derailing" are left alone; any spacing between
    # the words of a multi-word entry.
    words = []
    for word in heard.split():
        words.append(re.escape(word))
    return re.compile(r"\b" + r"\s+".join(words) + r"\b", re.IGNORECASE)


_CORRECTIONS = []
for _heard, _meant in TRANSCRIPT_CORRECTIONS.items():
    _CORRECTIONS.append((_pattern(_heard), _meant))


def correct_transcript(text: str) -> str:
    corrected = text
    for pattern, meant in _CORRECTIONS:
        corrected = pattern.sub(meant, corrected)
    return corrected
