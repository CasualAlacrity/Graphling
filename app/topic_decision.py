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


# ---- tool groups (experiment, 2026-10-17) -------------------------------------------------
#
# Every ALICE reply carries all 19 tool descriptions, ~9,650 of its ~11,000 prompt tokens.
# Asked in the same Tev1 call as the topic question, GROUP_QUESTION picks the kind of request
# so respond can bind only those tools. Off by default (ALICE_TOOL_ROUTING, topic_client.py)
# until the response matrix shows it keeps tool choices right without slowing replies.
#
# Measured on heket over 79 matrix lines with a known tool: one ranked choice kept ~1.3
# groups per request, where a yes/no question per group kept 3.4 (Tev1's yes/no scores
# overlapped: needed groups median 0.64, unneeded up to 0.52). With the rules in
# chosen_groups it missed a needed group once in 79 (covered by GROUP_COMPANIONS) and cut
# tool tokens ~55%, for ~10 ms more per decision.

TOOL_GROUPS = {
    "trade_run": (
        "start_trade_run", "mark_arrived", "update_leg_details", "mark_cargo_acquired", "confirm_cargo_loaded",
        "confirm_cargo_unloaded", "mark_cargo_sold", "trade_run_status", "trade_advisor",
        "cargo_packing_suggestion",
    ),
    "prices": ("price_lookup",),
    "routes": ("best_route",),
    "travel": ("estimate_travel_time",),
    "timers": ("start_timer", "check_timer", "cancel_timer"),
    "mining": ("mining_location_lookup", "refinery_yield_lookup"),
}

GROUP_QUESTION_NAME = "group"
NONE_OF_THESE = "other"
GROUP_QUESTION = {
    GROUP_QUESTION_NAME: {
        "type": "choice",
        "instructions": "A Star Citizen pilot said this to ALICE, their trading copilot. Which kind of request is it?",
        "criteria": {
            "trade_run": (
                "The pilot's own trade run: starting one, arriving, buying, loading, unloading or "
                "selling its cargo, its status, whether it is still a good run, or which cargo "
                "crates to use."
            ),
            "prices": (
                "What a commodity, item, ship or vehicle costs to buy, sell or rent, or where to "
                "buy, sell or rent one."
            ),
            "routes": "A trade route or what to haul, or refining a route search.",
            "travel": "How far away a place is or how long it takes to get there.",
            "timers": "Setting a timer or reminder, or how long is left on one.",
            "mining": "Mining or refining: where to find an ore, or which refinery to use.",
            NONE_OF_THESE: "None of these.",
        },
    },
}

# A group counts at this probability or more. Low on purpose: missing a tool is the
# failure that matters, an extra group only costs tokens.
GROUP_MIN_PROBABILITY = 0.1
# "None of these" at this or more means Tev1 can't place the request (often a follow-up
# like "What about Pyro?" that only makes sense with the conversation): every tool.
NONE_OF_THESE_ALL_TOOLS = 0.3
# Tiny groups (~700 tokens together) that ride along with any choice.
ALWAYS_GROUPS = ("travel", "timers")
# Groups another group always brings: committing a trade run means picking its route
# ("Best black market run from Seraphim" was placed as trade_run, not routes).
GROUP_COMPANIONS = {"trade_run": ("routes",)}


def request_body_with_groups(model: str, utterance: str) -> dict:
    """request_body plus GROUP_QUESTION, answered in the same call."""
    body = request_body(model, utterance)
    questions = dict(body["questions"])
    questions.update(GROUP_QUESTION)
    body["questions"] = questions
    return body


def read_group_probabilities(response_json: dict) -> dict[str, float]:
    """Option -> probability for GROUP_QUESTION (groups plus NONE_OF_THESE), or {} if the
    answer is missing."""
    answer = response_json.get("answers", {}).get(GROUP_QUESTION_NAME)
    if not answer:
        return {}
    probabilities = {}
    for option, probability in (answer.get("probabilities") or {}).items():
        probabilities[option] = float(probability)
    return probabilities


def chosen_groups(probabilities: dict[str, float]) -> list[str] | None:
    """The groups respond should get, in TOOL_GROUPS order (the same set always makes the
    same prompt, so heket can cache it), or None for every tool."""
    if not probabilities or probabilities.get(NONE_OF_THESE, 0.0) >= NONE_OF_THESE_ALL_TOOLS:
        return None
    wanted = set()
    for group in TOOL_GROUPS:
        if probabilities.get(group, 0.0) >= GROUP_MIN_PROBABILITY:
            wanted.add(group)
    if not wanted:
        return None
    for group in list(wanted):
        wanted.update(GROUP_COMPANIONS.get(group, ()))
    wanted.update(ALWAYS_GROUPS)
    ordered = []
    for group in TOOL_GROUPS:
        if group in wanted:
            ordered.append(group)
    return ordered


def groups_of_tools(tool_names: list[str]) -> set[str]:
    """The groups these tools belong to."""
    groups = set()
    for group, tools in TOOL_GROUPS.items():
        for name in tool_names:
            if name in tools:
                groups.add(group)
    return groups
