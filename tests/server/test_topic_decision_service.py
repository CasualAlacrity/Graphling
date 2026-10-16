"""Covers server/topic_decision_service.py and its /llm/decide/topic route."""
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from db.models import User
from server import llm_gateway_service as gateway
from server import topic_decision_service as service
from server.config import settings
from server.dependencies import get_current_user
from server.main import app

USER_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(gateway, "state", gateway.HeketState())
    monkeypatch.setattr(settings, "heket_topic_model", "tev1:4b")
    rows = []

    async def record_usage(**fields):
        rows.append(fields)

    monkeypatch.setattr(gateway, "record_usage", record_usage)
    return rows


def _heket(monkeypatch, handler):
    def fake_client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(gateway, "_http_client", fake_client)


def _answer(choice, confidence):
    return httpx.Response(200, json={
        "answers": {"kind": {"choice": choice, "confidence": confidence}},
        "usage": {"input_tokens": 235, "output_tokens": 0},
    })


async def test_asks_heket_and_applies_the_floors(monkeypatch, fresh):
    seen = []

    def handler(request):
        seen.append(request)
        return _answer("social", 0.57)

    _heket(monkeypatch, handler)

    result = await service.decide("Cheers, loaded.", USER_ID)

    assert seen[0].url.path == "/v1/systemone"
    assert result == {"kind": "request", "choice": "social", "confidence": 0.57}
    assert fresh[0]["role"] == "alice-topic"
    assert fresh[0]["prompt_tokens"] == 235
    assert fresh[0]["status"] == "ok"


async def test_a_failure_is_unavailable_and_leaves_heket_up(monkeypatch, fresh):
    _heket(monkeypatch, lambda request: httpx.Response(404, json={"error": "model not found"}))

    with pytest.raises(service.TopicDecisionUnavailable):
        await service.decide("Good morning.", USER_ID)

    assert gateway.state.down_reason is None
    assert fresh[0]["status"] == "error"


async def test_a_down_heket_or_no_model_is_unavailable_without_calling(monkeypatch, fresh):
    def handler(request):
        raise AssertionError("heket was called")

    _heket(monkeypatch, handler)
    gateway.state.mark_down("down", float("inf"))
    with pytest.raises(service.TopicDecisionUnavailable):
        await service.decide("Good morning.", USER_ID)

    gateway.state.mark_up()
    monkeypatch.setattr(settings, "heket_topic_model", "")
    with pytest.raises(service.TopicDecisionUnavailable):
        await service.decide("Good morning.", USER_ID)


def test_route_answers_the_kind_and_503s_when_unavailable(monkeypatch):
    _heket(monkeypatch, lambda request: _answer("request", 0.89))
    app.dependency_overrides[get_current_user] = lambda: User(id=USER_ID, discord_id="123")
    try:
        client = TestClient(app)
        ok = client.post("/llm/decide/topic", json={"utterance": "Hey ALICE, landed."})
        monkeypatch.setattr(settings, "heket_topic_model", "")
        unavailable = client.post("/llm/decide/topic", json={"utterance": "Hey ALICE, landed."})
        empty = client.post("/llm/decide/topic", json={"utterance": ""})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert ok.status_code == 200
    assert ok.json()["kind"] == "request"
    assert unavailable.status_code == 503
    assert empty.status_code == 422


def test_route_requires_authentication():
    assert TestClient(app).post("/llm/decide/topic", json={"utterance": "Hi"}).status_code in (401, 403)
