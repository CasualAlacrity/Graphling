"""Covers the pending_confirmation skip-classify_topic mechanism (docs/intent/
run-model.md's voice correction tool discussion, 2026-10-01): _just_ran_confirmation_
prone_tool's turn-scoping logic, and route_start's routing decision. The actual
graph-level behavior (classify_topic genuinely skipped, flag self-clears, the State(...)
vs plain-dict input gotcha this surfaced) was verified live against the real compiled
graph before writing these -- not duplicated here since that needs a real LLM."""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph import State, _just_ran_confirmation_prone_tool, route_start


def _tool_call_ai_message(tool_name: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": tool_name, "args": {}, "id": "call_1"}])


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
