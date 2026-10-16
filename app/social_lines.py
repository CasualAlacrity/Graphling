"""What ALICE says back to pure social talk: a greeting, "how are you", thanks, a farewell.

classify_topic tags these (TopicClassification.social) and the graph answers from here,
with no model call: instant, and always exactly in her voice. Written by Jeff; the first
lines come from his response-matrix markup (2026-10-16). The rules they follow:

- Dry, never pushy. No "state your manifest", no asking for a task: the pilot will say
  what they need.
- One short line. It's spoken aloud.

A message that's social *and* a request ("Morning ALICE, what's good to haul?") is not
social; it goes to respond like any other request.
"""
import random
import re

SOCIAL_LINES: dict[str, list[str]] = {
    "greeting": [
        "Good morning to you.",
    ],
    "how_are_you": [
        "I am operational.",
        "My neural network is optimal.",
    ],
    # Jeff to write.
    "thanks": [],
    # Jeff to write.
    "farewell": [],
}

# Said when a kind's list is still empty, so a farewell never falls through to the
# off-topic decline. Neutral on purpose until the real lines exist.
PLACEHOLDER_LINE = "Copy."


# Every word a pure greeting, thanks or farewell can be made of. The classifier tags social
# talk, but it also tagged "Hey ALICE, landed." as a greeting and "Thanks. Start the run."
# as thanks (2026-10-16), which would answer the greeting and drop the request. So a
# greeting/thanks/farewell only counts if nothing outside this list was said. Missing a
# real greeting just sends it to respond; swallowing a request is the failure that matters.
# "how are you" talk is checked by the classifier alone: it's open-ended by nature.
SOCIAL_WORDS = {
    "alice", "hey", "hi", "hello", "yo", "greetings", "good", "morning", "afternoon", "evening",
    "night", "day", "gm", "o7", "thanks", "thank", "you", "cheers", "appreciate", "it", "much",
    "very", "ty", "bye", "goodbye", "see", "ya", "later", "tomorrow", "soon", "signing", "off",
    "logging", "out", "fly", "safe", "take", "care", "that's", "all", "for", "now", "today",
    "to", "and", "a", "again", "ok", "okay", "great", "nice",
}


def is_only_social_talk(utterance: str) -> bool:
    for word in re.findall(r"[a-z0-9']+", utterance.lower()):
        if word not in SOCIAL_WORDS:
            return False
    return True


def social_line(kind: str, last_line: str | None = None) -> str:
    """A line for this kind of social talk, avoiding an exact repeat of `last_line`
    (ALICE's previous reply) when the list has anything else to offer."""
    lines = SOCIAL_LINES.get(kind) or []
    if not lines:
        return PLACEHOLDER_LINE
    choices = []
    for line in lines:
        if line != last_line:
            choices.append(line)
    if not choices:
        choices = lines
    return random.choice(choices)
