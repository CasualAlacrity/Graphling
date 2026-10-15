"""Covers the pending_confirmation skip-classify_topic mechanism (docs/intent/
run-model.md's voice correction tool discussion, 2026-10-01): _just_ran_confirmation_
prone_tool's turn-scoping logic, and route_start's routing decision. The actual
graph-level behavior (classify_topic genuinely skipped, flag self-clears, the State(...)
vs plain-dict input gotcha this surfaced) was verified live against the real compiled
graph before writing these -- not duplicated here since that needs a real LLM.

Also covers last_touched_leg_id's resolution helpers (_confirmation_tool_call,
_resolve_last_touched_leg_id) -- the leg-resolution design resolved 2026-10-01, see
run-model.md's leg-resolution note."""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from db.models import LegType
from graph import (
    State,
    _confirmation_tool_call,
    _just_ran_confirmation_prone_tool,
    _resolve_last_touched_leg_id,
    resolver,
    route_start,
)


def _tool_call_ai_message(tool_name: str, args: dict | None = None) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": tool_name, "args": args or {}, "id": "call_1"}])


class _FakeLeg:
    def __init__(self, leg_id):
        self.id = leg_id


def test_detects_mark_cargo_acquired_just_ran():
    messages = [
        HumanMessage("Bought 640 at 14.2"),
        _tool_call_ai_message("mark_cargo_acquired"),
        ToolMessage(content="Recorded.", tool_call_id="call_1"),
    ]
    assert _just_ran_confirmation_prone_tool(messages) is True


def test_detects_mark_cargo_sold_just_ran():
    messages = [
        HumanMessage("Sold it all"),
        _tool_call_ai_message("mark_cargo_sold"),
        ToolMessage(content="Recorded.", tool_call_id="call_1"),
    ]
    assert _just_ran_confirmation_prone_tool(messages) is True


def test_false_for_an_unrelated_tool():
    messages = [
        HumanMessage("Cheapest Laranite?"),
        _tool_call_ai_message("commodity_price_lookup"),
        ToolMessage(content="Baijini Point.", tool_call_id="call_1"),
    ]
    assert _just_ran_confirmation_prone_tool(messages) is False


def test_false_when_last_message_is_not_a_tool_message():
    # A completed turn always ends on a plain AIMessage, never a dangling ToolMessage --
    # this is what keeps the scan from reaching into an older, unrelated turn.
    messages = [
        HumanMessage("Bought 640 at 14.2"),
        _tool_call_ai_message("mark_cargo_acquired"),
        ToolMessage(content="Recorded.", tool_call_id="call_1"),
        AIMessage(content="Got it — 640 at 14.2, logged."),
    ]
    assert _just_ran_confirmation_prone_tool(messages) is False


def test_ignores_tool_calls_from_an_earlier_completed_turn():
    # The exact bug this logic has to avoid: a mark_cargo_acquired call from a turn
    # that already finished must not leak into a later, unrelated turn's check.
    messages = [
        HumanMessage("Bought 640 at 14.2"),
        _tool_call_ai_message("mark_cargo_acquired"),
        ToolMessage(content="Recorded.", tool_call_id="call_1"),
        AIMessage(content="Got it — 640 at 14.2, logged."),
        HumanMessage("Good morning."),
    ]
    assert _just_ran_confirmation_prone_tool(messages) is False


def test_false_for_empty_messages():
    assert _just_ran_confirmation_prone_tool([]) is False


def test_false_when_no_ai_message_precedes_the_tool_message():
    # Defensive -- shouldn't happen in a real graph run, but must not crash.
    messages = [ToolMessage(content="orphaned", tool_call_id="call_1")]
    assert _just_ran_confirmation_prone_tool(messages) is False


def test_route_start_skips_classify_when_pending_confirmation():
    state = State(messages=[HumanMessage("No, 614.")], pending_confirmation=True)
    assert route_start(state) == "respond"


def test_route_start_goes_to_classify_topic_normally():
    state = State(messages=[HumanMessage("What's the cheapest Laranite?")], pending_confirmation=False)
    assert route_start(state) == "classify_topic"


# --- answers to ALICE's own question skip classify_topic (2026-10-15) ---------

def test_route_start_skips_classify_when_alice_just_asked_a_question():
    """"Uh, I'll take my railing" answered ALICE's ship question and was declined."""
    state = State(messages=[
        HumanMessage("Give me a route from Orison"),
        AIMessage("Which ship are you flying?"),
        HumanMessage("Uh, I'll take my railing."),
    ])
    assert route_start(state) == "respond"


def test_route_start_skips_classify_when_a_tool_asked_and_alice_paraphrased():
    state = State(messages=[
        HumanMessage("Give me a route from Orison"),
        _tool_call_ai_message("best_route"),
        ToolMessage(content="Which ship are you flying?", tool_call_id="call_1"),
        AIMessage("I need to know which ship you are flying to find a route."),
        HumanMessage("Railen"),
    ])
    assert route_start(state) == "respond"


def test_route_start_still_classifies_after_a_decline_that_ends_in_a_question():
    state = State(messages=[
        HumanMessage("Tell me a joke"),
        AIMessage("Focus on the manifest. What are we actually moving?"),
        HumanMessage("Tell me another joke"),
    ], on_topic=False)
    assert route_start(state) == "classify_topic"


def test_route_start_classifies_when_alice_made_a_statement():
    state = State(messages=[
        HumanMessage("Where's Iron selling best?"),
        AIMessage("Orison is paying 3,400 aUEC per SCU for Iron."),
        HumanMessage("Tell me a joke"),
    ])
    assert route_start(state) == "classify_topic"


def test_only_the_previous_turn_counts():
    """A question two turns back doesn't make today's utterance an answer."""
    state = State(messages=[
        HumanMessage("Give me a route"),
        AIMessage("Which ship are you flying?"),
        HumanMessage("Railen"),
        AIMessage("The best route is RMC to Seer's Canyon."),
        HumanMessage("Tell me a joke"),
    ])
    assert route_start(state) == "classify_topic"


# --- _confirmation_tool_call -------------------------------------------------

def test_confirmation_tool_call_returns_the_triggering_call():
    ai_message = _tool_call_ai_message("mark_cargo_acquired", args={"commodity": "Laranite"})
    messages = [
        HumanMessage("Bought 640 at 14.2"),
        ai_message,
        ToolMessage(content="Recorded.", tool_call_id="call_1"),
    ]
    assert _confirmation_tool_call(messages) is ai_message.tool_calls[0]


def test_confirmation_tool_call_none_for_an_unrelated_tool():
    messages = [
        HumanMessage("Cheapest Laranite?"),
        _tool_call_ai_message("commodity_price_lookup"),
        ToolMessage(content="Baijini Point.", tool_call_id="call_1"),
    ]
    assert _confirmation_tool_call(messages) is None


def test_confirmation_tool_call_none_when_last_message_is_not_a_tool_message():
    messages = [
        HumanMessage("Bought 640 at 14.2"),
        _tool_call_ai_message("mark_cargo_acquired"),
        ToolMessage(content="Recorded.", tool_call_id="call_1"),
        AIMessage(content="Got it — 640 at 14.2, logged."),
    ]
    assert _confirmation_tool_call(messages) is None


def test_confirmation_tool_call_none_for_empty_messages():
    assert _confirmation_tool_call([]) is None


# --- _resolve_last_touched_leg_id --------------------------------------------

async def test_resolve_last_touched_leg_id_uses_the_triggering_calls_own_args(monkeypatch):
    captured = {}

    async def fake_resolve_leg(leg_type=None, commodity=None, terminal=None):
        captured["leg_type"] = leg_type
        captured["commodity"] = commodity
        captured["terminal"] = terminal
        return _FakeLeg(leg_id=42)

    monkeypatch.setattr(resolver, "resolve_leg", fake_resolve_leg)

    messages = [
        HumanMessage("Bought 640 of Laranite at Baijini"),
        _tool_call_ai_message("mark_cargo_acquired", args={"commodity": "Laranite", "terminal": "Baijini"}),
        ToolMessage(content="Recorded.", tool_call_id="call_1"),
    ]
    leg_id = await _resolve_last_touched_leg_id(messages)

    assert leg_id == 42
    assert captured == {"leg_type": LegType.ACQUISITION, "commodity": "Laranite", "terminal": "Baijini"}


async def test_resolve_last_touched_leg_id_none_when_no_confirmation_tool_ran(monkeypatch):
    async def fail_if_called(**kwargs):
        raise AssertionError("resolve_leg should never be called here")

    monkeypatch.setattr(resolver, "resolve_leg", fail_if_called)

    messages = [
        HumanMessage("Cheapest Laranite?"),
        _tool_call_ai_message("commodity_price_lookup"),
        ToolMessage(content="Baijini Point.", tool_call_id="call_1"),
    ]
    assert await _resolve_last_touched_leg_id(messages) is None


async def test_resolve_last_touched_leg_id_none_on_ambiguous_match(monkeypatch):
    async def fake_resolve_leg(**kwargs):
        raise resolver.AmbiguousLegError([])

    monkeypatch.setattr(resolver, "resolve_leg", fake_resolve_leg)

    messages = [
        HumanMessage("Bought 640"),
        _tool_call_ai_message("mark_cargo_acquired"),
        ToolMessage(content="Which run do you mean?", tool_call_id="call_1"),
    ]
    assert await _resolve_last_touched_leg_id(messages) is None


async def test_resolve_last_touched_leg_id_none_on_value_error(monkeypatch):
    async def fake_resolve_leg(**kwargs):
        raise ValueError("no run with that leg")

    monkeypatch.setattr(resolver, "resolve_leg", fake_resolve_leg)

    messages = [
        HumanMessage("Sold it all"),
        _tool_call_ai_message("mark_cargo_sold"),
        ToolMessage(content="No matching leg.", tool_call_id="call_1"),
    ]
    assert await _resolve_last_touched_leg_id(messages) is None
