"""Covers the social path (2026-10-16): pure social talk (a greeting, "how are you",
thanks, a farewell) gets a short line from the social-reply prompt instead of the
off-topic decline, which used to answer "Good morning." with "Stick to the manifests."."""
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

import graph
from graph import State, route_topic, social_reply


def test_social_talk_goes_to_the_social_reply_even_if_classified_off_topic():
    state = State(messages=[HumanMessage("Good morning.")], on_topic=False, social=True)
    assert route_topic(state) == "social"


def test_requests_and_off_topic_still_route_as_before():
    assert route_topic(State(messages=[], on_topic=True)) == "respond"
    assert route_topic(State(messages=[], on_topic=False)) == "decline"


async def test_classify_marks_social_talk_on_topic(monkeypatch):
    async def classify(messages):
        return SimpleNamespace(on_topic=False, reason="small talk", social=True)

    monkeypatch.setattr(graph, "classifier_llm", SimpleNamespace(ainvoke=classify))

    result = await graph.classify_topic(State(messages=[HumanMessage("Good morning.")]))

    assert result["social"] is True
    assert result["on_topic"] is True


def _social_llm(monkeypatch, answer):
    seen = []

    async def invoke(messages):
        seen.append(messages)
        if isinstance(answer, Exception):
            raise answer
        return AIMessage(answer)

    monkeypatch.setattr(graph, "social_llm", SimpleNamespace(ainvoke=invoke))
    return seen


async def test_the_model_writes_the_line_from_the_pilots_words(monkeypatch):
    seen = _social_llm(monkeypatch, '"Morning. Systems nominal."')

    result = await social_reply(State(messages=[HumanMessage("Good morning.")], social=True))

    assert result["messages"][0].content == "Morning. Systems nominal."
    assert seen[0][-1].content == "Pilot said: Good morning."


async def test_a_failed_or_empty_answer_still_gets_a_reply(monkeypatch):
    for answer in (TimeoutError(), "  "):
        _social_llm(monkeypatch, answer)

        result = await social_reply(State(messages=[HumanMessage("How are you?")], social=True))

        assert result["messages"][0].content == graph.SOCIAL_FALLBACK_LINE

