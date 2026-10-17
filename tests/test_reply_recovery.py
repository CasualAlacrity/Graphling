"""Covers reply_recovery.py with the exact replies the response-matrix baseline caught
(2026-10-17): tool calls written out as text, and empty replies."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from reply_recovery import NOTHING_TO_SAY, fallback_line, is_empty, recover_tool_call

TOOLS = {"commodity_price_lookup", "mark_cargo_acquired", "mark_cargo_sold", "start_timer"}


@pytest.mark.parametrize(("text", "name", "args"), [
    ('commodity_price_lookup{commodity:<|"|>Laranite<|"|>}', "commodity_price_lookup", {"commodity": "Laranite"}),
    ('commodity_price_lookup(commodity="Agricium")', "commodity_price_lookup", {"commodity": "Agricium"}),
    ("mark_cargo_acquired(commodity='Copper', terminal='Admin - Seraphim', quantity_scu=640, price_per_unit=3198)",
     "mark_cargo_acquired",
     {"commodity": "Copper", "terminal": "Admin - Seraphim", "quantity_scu": 640, "price_per_unit": 3198}),
    ('mark_cargo_sold{commodity:<|"|>Copper<|"|>,quantity_scu:640}', "mark_cargo_sold",
     {"commodity": "Copper", "quantity_scu": 640}),
])
def test_a_tool_call_written_as_text_becomes_a_real_call(text, name, args):
    recovered = recover_tool_call(AIMessage(content=text), TOOLS)

    assert recovered.content == ""
    assert len(recovered.tool_calls) == 1
    assert recovered.tool_calls[0]["name"] == name
    assert recovered.tool_calls[0]["args"] == args


@pytest.mark.parametrize("text", [
    "ArcCorp Mining Area 056, on Wala, is selling Laranite for 7,047 aUEC per SCU.",
    'delete_everything(now=True)',            # not one of ALICE's tools
    'commodity_price_lookup(print("hi"))',    # not plain keyword arguments
    "Try commodity_price_lookup(commodity='Laranite') later.",  # a call inside a sentence
])
def test_anything_else_is_left_alone(text):
    message = AIMessage(content=text)
    assert recover_tool_call(message, TOOLS) is message


def test_an_empty_reply_is_empty_but_a_call_is_not():
    assert is_empty(AIMessage(content="  "))
    assert not is_empty(AIMessage(content="", tool_calls=[{"name": "start_timer", "args": {}, "id": "1"}]))


def test_the_fallback_says_the_turns_short_tool_result():
    messages = [
        HumanMessage("Loading takes 15, ping me"),
        AIMessage(content="", tool_calls=[{"name": "start_timer", "args": {}, "id": "1"}]),
        ToolMessage(content="Timer 'Loading' set for 15:00.", tool_call_id="1"),
    ]
    assert fallback_line(messages) == "Timer 'Loading' set for 15:00."


def test_the_fallback_asks_again_without_a_short_tool_result():
    assert fallback_line([HumanMessage("Yeah, do it")]) == NOTHING_TO_SAY
    long_result = [HumanMessage("Best route"), ToolMessage(content="x" * 500, tool_call_id="1")]
    assert fallback_line(long_result) == NOTHING_TO_SAY
    # A previous turn's tool result never stands in for this turn's answer.
    earlier = [ToolMessage(content="Timer set.", tool_call_id="1"), HumanMessage("And now?")]
    assert fallback_line(earlier) == NOTHING_TO_SAY
