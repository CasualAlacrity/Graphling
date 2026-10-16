"""Covers app/topic_decision.py (the Tev1 question and confidence floors) and
app/topic_client.py (asking it, and giving up quietly so classify_topic can fall back)."""
import httpx
import pytest

import topic_client
import topic_decision


@pytest.mark.parametrize(("choice", "confidence", "kind"), [
    ("social", 0.97, "social"),
    # "Cheers, loaded." came back social at 0.57 on heket: unsure social talk is a request.
    ("social", 0.57, "request"),
    ("off_topic", 0.95, "off_topic"),
    # "Can I rent a Cat near Lorville?" came back off_topic at 0.71.
    ("off_topic", 0.71, "request"),
    ("request", 0.20, "request"),
    ("something new", 0.99, "request"),
])
def test_unsure_social_or_off_topic_answers_count_as_requests(choice, confidence, kind):
    assert topic_decision.apply_floors(choice, confidence) == kind


def test_the_request_carries_the_shared_question():
    body = topic_decision.request_body("tev1:4b", "Hey ALICE, landed.")

    assert body["state"] == "Hey ALICE, landed."
    assert set(body["questions"]["kind"]["criteria"]) == {"request", "social", "off_topic"}


def _local_ollama(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.test")
    monkeypatch.setattr(topic_client.httpx, "AsyncClient", fake_client)


async def test_development_asks_the_local_ollama_and_applies_the_floors(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"answers": {"kind": {"choice": "social", "confidence": 0.4}}})

    _local_ollama(monkeypatch, handler)

    kind = await topic_client.topic_kind("Cheers, loaded.")

    assert str(seen[0].url) == "http://ollama.test/v1/systemone"
    assert kind == "request"


async def test_any_failure_means_no_decision(monkeypatch):
    _local_ollama(monkeypatch, lambda request: httpx.Response(404, json={"error": "model not found"}))

    assert await topic_client.topic_kind("Good morning.") is None


async def test_an_empty_topic_model_skips_the_decision(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_TOPIC_MODEL", "")

    assert await topic_client.topic_kind("Good morning.") is None


async def test_hosted_providers_have_no_decision_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    assert await topic_client.topic_kind("Good morning.") is None
