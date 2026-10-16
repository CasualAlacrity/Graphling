"""Is the pilot asking for something, just being social, or off-topic? Asked of Tev1, a
small "decision model" (ollama.com/library/tev1) that picks one of a few named options
and gives a probability for each, instead of writing text.

Shared by the server (server/topic_decision_service.py, which asks heket for every
pilot) and the desktop client when developing against a local Ollama (graph.py's
classify_topic). Pilots never call Tev1 directly, so the wording and the confidence
floors below change with a server deploy, not an installer.

Measured 2026-10-16 on 126 eval and response-matrix phrases (memory
project-decision-model-classify-topic): Tev1 4B 88-91% right versus 86% for gemma4 as
classifier, in ~30 ms on heket versus ~600 ms, and with the floors below it lost none of
the requests gemma4 kept swallowing ("Hey ALICE, landed." as a greeting).

Wording matters: dropping the "even if it also greets or thanks her" example flipped
"Hey ALICE, landed." to social. Re-measure after any change to QUESTION.
"""

REQUEST = "request"
SOCIAL = "social"
OFF_TOPIC = "off_topic"

QUESTION_NAME = "kind"
QUESTION = {
    QUESTION_NAME: {
        "type": "choice",
        "instructions": (
            "A Star Citizen pilot said this to ALICE, their in-game trading copilot. "
            "What kind of message is it?"
        ),
        "criteria": {
            REQUEST: (
                "It asks ALICE for something or tells her anything about the game, their ship, "
                "cargo, trading or where they are, even if it also greets or thanks her "
                "(e.g. 'Hey ALICE, landed.', 'Thanks, now where do I sell this?')."
            ),
            SOCIAL: (
                "Only social talk with nothing else in it: a greeting, asking how ALICE is, "
                "thanks, or a farewell."
            ),
            OFF_TOPIC: "About the real world, with no connection to Star Citizen.",
        },
    },
}

# Tev1's own confidence (how concentrated its probabilities are) below which a "social"
# or "off_topic" answer counts as a request instead. Mistaking a request for social talk
# drops it silently, and mistaking it for off-topic declines it, so both need to be sure;
# a request that wasn't one just gets a normal answer. Every request Tev1 got wrong in
# the 2026-10-16 runs was below these (social at most 0.57, off_topic at most 0.71 on
# heket's build), while real greetings came in at 0.89-0.99.
SOCIAL_MIN_CONFIDENCE = 0.6
OFF_TOPIC_MIN_CONFIDENCE = 0.8


def request_body(model: str, utterance: str) -> dict:
    """The body for Ollama's /v1/systemone endpoint."""
    return {"model": model, "state": utterance, "questions": QUESTION}


def read_answer(response_json: dict) -> tuple[str, float]:
    """(Tev1's choice, its confidence) from a /v1/systemone response."""
    answer = response_json["answers"][QUESTION_NAME]
    return answer["choice"], float(answer.get("confidence", 0.0))


def apply_floors(choice: str, confidence: float) -> str:
    """The kind of message ALICE should treat this as."""
    if choice == SOCIAL and confidence < SOCIAL_MIN_CONFIDENCE:
        return REQUEST
    if choice == OFF_TOPIC and confidence < OFF_TOPIC_MIN_CONFIDENCE:
        return REQUEST
    if choice not in (REQUEST, SOCIAL, OFF_TOPIC):
        return REQUEST
    return choice
