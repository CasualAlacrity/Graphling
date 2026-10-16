"""Covers the social path (2026-10-16): pure social talk (a greeting, "how are you",
thanks, a farewell) gets a line from social_lines.py instead of the off-topic decline,
which used to answer "Good morning." with "Stick to the manifests."."""
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

import graph
import social_lines
from graph import State, route_topic, social_reply


def test_social_talk_goes_to_the_social_line_even_if_classified_off_topic():
    state = State(messages=[HumanMessage("Good morning.")], on_topic=False, social="greeting")
    assert route_topic(state) == "social"


def test_requests_and_off_topic_still_route_as_before():
    assert route_topic(State(messages=[], on_topic=True)) == "respond"
    assert route_topic(State(messages=[], on_topic=False)) == "decline"


async def test_classify_marks_social_talk_on_topic(monkeypatch):
    async def classify(messages):
        return SimpleNamespace(on_topic=False, reason="small talk", social="greeting")

    monkeypatch.setattr(graph, "classifier_llm", SimpleNamespace(ainvoke=classify))

    result = await graph.classify_topic(State(messages=[HumanMessage("Good morning.")]))

    assert result["social"] == "greeting"
    assert result["on_topic"] is True


async def test_classify_maps_none_to_no_social_talk(monkeypatch):
    async def classify(messages):
        return SimpleNamespace(on_topic=True, reason="trade question", social="none")

    monkeypatch.setattr(graph, "classifier_llm", SimpleNamespace(ainvoke=classify))

    result = await graph.classify_topic(State(messages=[HumanMessage("Best route from Orison?")]))

    assert result["social"] is None


async def test_the_reply_comes_from_the_pool_for_that_kind():
    state = State(messages=[HumanMessage("How are you?")], social="how_are_you")

    result = await social_reply(state)

    assert result["messages"][0].content in social_lines.SOCIAL_LINES["how_are_you"]


def test_never_repeats_the_previous_line_when_there_is_another():
    for _ in range(20):
        assert social_lines.social_line("how_are_you", last_line="I am operational.") != "I am operational."


async def test_social_reply_avoids_alices_last_line(monkeypatch):
    monkeypatch.setitem(social_lines.SOCIAL_LINES, "greeting", ["One.", "Two."])
    state = State(
        messages=[HumanMessage("Morning."), AIMessage("One."), HumanMessage("Morning again.")],
        social="greeting",
    )

    for _ in range(10):
        result = await social_reply(state)
        assert result["messages"][0].content == "Two."


def test_an_empty_list_uses_the_placeholder_not_the_decline():
    assert social_lines.social_line("farewell") == social_lines.PLACEHOLDER_LINE


async def test_a_question_inside_a_greeting_is_a_request_not_social_talk(monkeypatch):
    async def classify(messages):
        return SimpleNamespace(on_topic=True, reason="greeting", social="greeting")

    monkeypatch.setattr(graph, "classifier_llm", SimpleNamespace(ainvoke=classify))

    result = await graph.classify_topic(State(messages=[HumanMessage("Hey ALICE, cheapest Laranite?")]))

    assert result["social"] is None
    assert result["on_topic"] is True


async def test_how_are_you_stays_social_despite_the_question_mark(monkeypatch):
    async def classify(messages):
        return SimpleNamespace(on_topic=False, reason="small talk", social="how_are_you")

    monkeypatch.setattr(graph, "classifier_llm", SimpleNamespace(ainvoke=classify))

    result = await graph.classify_topic(State(messages=[HumanMessage("Hey, how are you?")]))

    assert result["social"] == "how_are_you"


def test_only_social_words_count_as_social_talk():
    for utterance in ("Good morning.", "Hey ALICE.", "Thanks ALICE.", "Signing off, fly safe.", "Good night.", "o7"):
        assert social_lines.is_only_social_talk(utterance), utterance
    for utterance in ("Hey ALICE, landed.", "Thanks. Start the run.", "Morning ALICE, what's good to haul?"):
        assert not social_lines.is_only_social_talk(utterance), utterance
