"""Covers _windowed_history -- what respond/classify_topic are sent out of the full
stored thread. The properties that matter: it cuts only at a HumanMessage (never
orphaning a ToolMessage from its tool_call, which both hosted APIs reject), stubs only
tool results older than the full-result window, and leaves the stored messages alone."""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graph import STALE_TOOL_RESULT, _windowed_history


def _tool_turn(n: int) -> list:
    call_id = f"call_{n}"
    return [
        HumanMessage(f"question {n}"),
        AIMessage(content="", tool_calls=[{"name": "commodity_price_lookup", "args": {}, "id": call_id}]),
        ToolMessage(content=f"result {n}", tool_call_id=call_id),
        AIMessage(f"answer {n}"),
    ]


def _thread(turn_count: int) -> list:
    messages = []
    for n in range(turn_count):
        messages.extend(_tool_turn(n))
    return messages


def _tool_contents(messages: list) -> list[str]:
    contents = []
    for message in messages:
        if isinstance(message, ToolMessage):
            contents.append(message.content)
    return contents


def test_short_thread_is_sent_whole():
    messages = _thread(2)
    assert _windowed_history(messages, turns=6, full_result_turns=2) == messages


def test_zero_turns_disables_windowing():
    messages = _thread(10)
    assert _windowed_history(messages, turns=0, full_result_turns=2) == messages


def test_window_starts_on_a_human_message():
    window = _windowed_history(_thread(10), turns=3, full_result_turns=2)
    assert isinstance(window[0], HumanMessage)
    assert window[0].content == "question 7"
    assert len(window) == 12


def test_only_older_tool_results_are_stubbed():
    window = _windowed_history(_thread(10), turns=3, full_result_turns=2)
    assert _tool_contents(window) == [STALE_TOOL_RESULT, "result 8", "result 9"]


def test_stubbed_result_keeps_its_tool_call_id():
    window = _windowed_history(_thread(10), turns=3, full_result_turns=1)
    stubbed = [m for m in window if isinstance(m, ToolMessage) and m.content == STALE_TOOL_RESULT]
    assert [m.tool_call_id for m in stubbed] == ["call_7", "call_8"]


def test_current_turn_mid_tool_loop_keeps_its_result():
    # respond being called back after tools: the trailing ToolMessage is this turn's own.
    messages = _thread(3) + _tool_turn(3)[:3]
    window = _windowed_history(messages, turns=2, full_result_turns=1)
    assert window[-1].content == "result 3"


def test_stored_messages_are_not_mutated():
    messages = _thread(10)
    _windowed_history(messages, turns=3, full_result_turns=1)
    assert _tool_contents(messages) == [f"result {n}" for n in range(10)]
