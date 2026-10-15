"""The ship-name vocabulary hint Whisper gets with every utterance.

Whisper's initial_prompt biases it toward spelling unusual proper nouns right ("Railen",
not "railing"). But faster-whisper only keeps the *last* 223 tokens of it
(transcribe.py: `previous_tokens[-(max_length // 2 - 1):]`). The old hint, every
vehicle name, was ~1,400 tokens, so Whisper only ever saw the last sixth of the
alphabet. Railen was cut, which is why it kept coming back as "railing".

So the hint is ranked and fitted to the budget:
1. Words of ships with a known mishearing (aliases.MISHEARINGS) always go in.
2. Then words from cargo-capable ships, the ones pilots name most, rarest first. A word
   the tokenizer splits into more pieces is one Whisper is less likely to spell right
   unaided; a word that is already one token ("Black", "Star") needs no hint at all,
   and model codes ("600i", "C2") are left out too.
3. Most important words go *last*, so if the count is ever off, truncation from the
   front drops the least useful ones.
"""
from collections.abc import Callable

# faster-whisper keeps the last max_length // 2 - 1 = 223 prompt tokens. Stay under it
# with some room, since the decoder also counts the separator before the prompt.
TOKEN_BUDGET = 200


def _unique_words(names: list[str]) -> list[str]:
    words = []
    seen = set()
    for name in names:
        for word in name.split():
            key = word.casefold()
            if key in seen:
                continue
            seen.add(key)
            words.append(word)
    return words


def build_ship_hint(
        ship_names: list[str], priority_names: list[str], count_tokens: Callable[[str], int],
        budget: int = TOKEN_BUDGET,
) -> str:
    """A comma-separated hint of at most `budget` tokens (by `count_tokens`, which should
    be the Whisper model's own tokenizer), most important words last."""
    priority = _unique_words(priority_names)
    priority_keys = set()
    for word in priority:
        priority_keys.add(word.casefold())

    ranked = []
    for index, word in enumerate(_unique_words(ship_names)):
        if word.casefold() in priority_keys:
            continue
        # Model codes ("600i", "C2", "LX") are numbers and letters Whisper already
        # writes fine, and there are dozens of them; the budget is for names.
        if len(word) <= 2 or any(character.isdigit() for character in word):
            continue
        pieces = count_tokens(" " + word)
        if pieces < 2:
            continue
        # Rarest (most pieces) first; ties keep catalog order.
        ranked.append((-pieces, index, word))
    ranked.sort()

    important_first = list(priority)
    for _pieces, _index, word in ranked:
        important_first.append(word)

    chosen = []
    for word in important_first:
        candidate = chosen + [word]
        if count_tokens(" " + ", ".join(reversed(candidate))) > budget:
            # Priority words are never dropped for a rarer one; the rest just stop here.
            if word in priority:
                continue
            break
        chosen = candidate

    return ", ".join(reversed(chosen))
