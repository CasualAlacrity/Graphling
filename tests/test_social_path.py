"""Covers the social path (2026-10-16): pure social talk (a greeting, "how are you",
thanks, a farewell) gets a short line from the social-reply prompt instead of the
off-topic decline, which used to answer "Good morning." with "Stick to the manifests."."""
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import graph
from graph import State, route_topic, social_reply


@pytest.fixture(autouse=True)
def no_ledger(monkeypatch):
    """classify_topic reads the active runs alongside the topic decision; never the real ledger here."""
    async def no_runs():
        return ""

    monkeypatch.setattr(graph, "_active_runs_note", no_runs)


def test_social_talk_goes_to_the_social_reply_even_if_classified_off_topic():
    state = State(messages=[HumanMessage("Good morning.")], on_topic=False, social=True)
    assert route_topic(state) == "social"


def test_requests_and_off_topic_still_route_as_before():
    assert route_topic(State(messages=[], on_topic=True)) == "respond"
    assert route_topic(State(messages=[], on_topic=False)) == "decline"


def _topic(monkeypatch, kind):
    async def decide(utterance):
        return None if kind is None else graph.topic_client.TopicDecision(kind)

    monkeypatch.setattr(graph.topic_client, "decide", decide)


def _classifier(monkeypatch, on_topic):
    calls = []

    async def classify(messages):
        calls.append(messages)
        return SimpleNamespace(on_topic=on_topic, reason="classifier")

    monkeypatch.setattr(graph, "classifier_llm", SimpleNamespace(ainvoke=classify))
    return calls


async def test_the_decision_model_decides_social_talk(monkeypatch):
    _topic(monkeypatch, "social")
    classifier_calls = _classifier(monkeypatch, on_topic=False)

    result = await graph.classify_topic(State(messages=[HumanMessage("Good morning.")]))

    assert result["social"] is True
    assert result["on_topic"] is True
    assert classifier_calls == []


async def test_the_decision_model_decides_requests_and_off_topic(monkeypatch):
    _classifier(monkeypatch, on_topic=True)

    _topic(monkeypatch, "request")
    request = await graph.classify_topic(State(messages=[HumanMessage("Hey ALICE, landed.")]))
    _topic(monkeypatch, "off_topic")
    off_topic = await graph.classify_topic(State(messages=[HumanMessage("Who won the Super Bowl?")]))

    assert (request["on_topic"], request["social"]) == (True, False)
    assert (off_topic["on_topic"], off_topic["social"]) == (False, False)
    assert off_topic["reason"] == graph.OFF_TOPIC_REASON


async def test_without_the_decision_model_the_classifier_never_says_social(monkeypatch):
    _topic(monkeypatch, None)
    calls = _classifier(monkeypatch, on_topic=True)

    result = await graph.classify_topic(State(messages=[HumanMessage("Good morning.")]))

    assert len(calls) == 1
    assert result["social"] is False
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

