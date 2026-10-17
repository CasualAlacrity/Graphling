"""Covers graph.py's active-run context: respond is told the pilot's in-progress trade runs,
so "Landed." or "Bought 640 at 14.2" reach the trade tools instead of a question back
(Jeff's response-matrix markup, 2026-10-16: "this needs context pieces")."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import graph
from graph import State

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "agent_eval"))
import matrix_scenarios  # noqa: E402


@pytest.fixture
def seen_prompts(monkeypatch):
    prompts = []

    async def invoke(messages):
        prompts.append(messages)
        return AIMessage("ok")

    monkeypatch.setattr(graph, "llm", SimpleNamespace(ainvoke=invoke))
    monkeypatch.setattr(graph, "_persona_messages", lambda: [SystemMessage("persona")])
    return prompts


def _ledger(monkeypatch, scenario):
    ledger = matrix_scenarios.FakeLedger(matrix_scenarios._runs_for(scenario))
    monkeypatch.setattr(graph.ledger_client, "get_in_progress_runs", ledger.get_in_progress_runs)
    return ledger


async def test_the_note_describes_every_active_run(monkeypatch):
    _ledger(monkeypatch, matrix_scenarios.TWO_RUNS)

    note = await graph._active_runs_note()

    assert note.startswith(graph.ACTIVE_RUNS_HEADER)
    assert "Copper" in note and "Laranite" in note
    assert "Admin - Seraphim" in note


async def test_no_runs_or_an_unreadable_ledger_means_no_note(monkeypatch):
    _ledger(monkeypatch, matrix_scenarios.NO_RUN)
    assert await graph._active_runs_note() == ""

    async def broken():
        raise ConnectionError("ledger down")

    monkeypatch.setattr(graph.ledger_client, "get_in_progress_runs", broken)
    assert await graph._active_runs_note() == ""


async def test_respond_puts_the_note_right_after_the_persona(monkeypatch, seen_prompts):
    state = State(messages=[HumanMessage("Landed.")], active_runs="RUNS NOTE", active_runs_turn=1)

    result = await graph.respond(state)

    prompt = seen_prompts[0]
    assert prompt[0].content == "persona"
    assert prompt[1].content == "RUNS NOTE"
    assert result["active_runs_turn"] == 1


async def test_respond_reads_the_runs_itself_when_the_topic_check_was_skipped(monkeypatch, seen_prompts):
    _ledger(monkeypatch, matrix_scenarios.BUYING)
    # A stale note from the previous pilot turn: this turn skipped classify_topic.
    state = State(messages=[HumanMessage("Bought 640"), AIMessage("At 14.2?"), HumanMessage("Yes")],
                  active_runs="OLD NOTE", active_runs_turn=1)

    result = await graph.respond(state)

    assert "Copper" in seen_prompts[0][1].content
    assert result["active_runs_turn"] == 2


async def test_without_runs_the_prompt_is_unchanged(monkeypatch, seen_prompts):
    _ledger(monkeypatch, matrix_scenarios.NO_RUN)

    await graph.respond(State(messages=[HumanMessage("Best route from Orison")]))

    assert [message.content for message in seen_prompts[0]] == ["persona", "Best route from Orison"]


async def test_respond_binds_only_the_routed_groups_for_this_turn(monkeypatch, seen_prompts):
    bound = []

    def fake_llm_for(groups):
        bound.append(groups)
        return graph.llm

    monkeypatch.setattr(graph, "_llm_for", fake_llm_for)
    _ledger(monkeypatch, matrix_scenarios.NO_RUN)

    await graph.respond(State(messages=[HumanMessage("Ping me in 15")], tool_groups=["timers"], tool_groups_turn=1))
    # Groups decided for an earlier turn don't carry into this one.
    await graph.respond(State(messages=[HumanMessage("a"), AIMessage("b"), HumanMessage("c")],
                              tool_groups=["timers"], tool_groups_turn=1))

    assert bound == [["timers"], None]


def test_a_follow_up_keeps_the_groups_of_last_turns_tools():
    messages = [
        HumanMessage("Find me the best route from Orison in my Railen"),
        AIMessage("", tool_calls=[{"name": "best_route", "args": {}, "id": "1"}]),
        AIMessage("Best route is RMC to Terra Mills."),
        HumanMessage("Yeah, do it"),
    ]

    assert graph._with_last_turns_groups(["trade_run", "travel"], messages) == ["trade_run", "routes", "travel"]
    assert graph._with_last_turns_groups(None, messages) is None


def test_routed_models_only_carry_their_groups_tools():
    routed = graph._llm_for(["timers"])
    names = []
    for tool in routed.kwargs["tools"]:
        names.append(tool["function"]["name"])

    assert sorted(names) == ["cancel_timer", "check_timer", "start_timer"]
    assert graph._llm_for(["timers"]) is routed
    assert graph._llm_for(None) is graph.llm


async def test_respond_retries_an_empty_reply_once_then_falls_back(monkeypatch):
    answers = [AIMessage(""), AIMessage("")]

    async def invoke(messages):
        return answers.pop(0)

    monkeypatch.setattr(graph, "llm", SimpleNamespace(ainvoke=invoke))
    monkeypatch.setattr(graph, "_persona_messages", lambda: [SystemMessage("persona")])
    _ledger(monkeypatch, matrix_scenarios.NO_RUN)

    result = await graph.respond(State(messages=[HumanMessage("Yeah, do it")]))

    assert answers == []
    assert result["messages"][0].content == graph.reply_recovery.NOTHING_TO_SAY
