"""Covers server/llm_gateway_service.py and server/routes/llm.py: request shaping, the
Ollama <-> OpenAI translation on the fallback path, and the routing decision (pass
through to frankencompute vs fall back). Outbound HTTP goes through httpx.MockTransport
via the service's _http_client seam -- no real box, no real OpenAI -- and record_usage
is swapped for a list, so nothing touches Postgres.
"""
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from db.models import User
from server import llm_gateway_service as gateway
from server.config import settings
from server.dependencies import get_current_user
from server.main import app

USER_ID = uuid.uuid4()

CHAT_PAYLOAD = {
    "model": "alice-chat",
    "messages": [{"role": "user", "content": "Where's Laranite cheap?"}],
    "stream": True,
    "think": None,
    "keep_alive": 0,
    "options": {"temperature": 0.2, "num_ctx": 131072, "num_gpu": 0},
}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(gateway, "state", gateway.FrankencomputeState())
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_max_inflight", 2)
    monkeypatch.setattr(settings, "frankencompute_model_digest", "")


@pytest.fixture
def usage_rows(monkeypatch):
    rows = []

    async def fake_record_usage(**fields):
        rows.append(fields)

    monkeypatch.setattr(gateway, "record_usage", fake_record_usage)
    return rows


def route_http(monkeypatch, handler):
    """Every outbound request the service makes goes to handler(request) instead."""
    def fake_client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(gateway, "_http_client", fake_client)


def ndjson(*chunks):
    return "".join(json.dumps(chunk) + "\n" for chunk in chunks)


async def collect(relay):
    body = b""
    async for part in relay.body:
        body += part
    return [json.loads(line) for line in body.decode().splitlines() if line.strip()]


# ---- request shaping ----

def test_frankencompute_payload_resolves_the_role_and_strips_box_wide_settings():
    role = gateway.roles()["alice-chat"]

    forwarded = gateway.frankencompute_payload(CHAT_PAYLOAD, role)

    assert forwarded["model"] == settings.frankencompute_chat_model
    assert forwarded["think"] is True
    assert "keep_alive" not in forwarded
    assert forwarded["options"] == {"temperature": 0.2}


def test_classify_role_has_thinking_off():
    assert gateway.roles()["alice-classify"].think is False


# ---- Ollama -> OpenAI translation ----

def test_openai_request_keeps_tool_call_pairing():
    payload = {
        "messages": [
            {"role": "system", "content": "You are ALICE."},
            {"role": "user", "content": "Price of Laranite?"},
            {"role": "assistant", "content": "", "thinking": "hmm", "tool_calls": [
                {"type": "function", "id": "abc", "function": {"name": "commodity_price_lookup",
                                                               "arguments": {"commodity": "Laranite"}}},
            ]},
            {"role": "tool", "tool_call_id": "abc", "content": "{\"cheapest_to_buy\": null}"},
        ],
        "tools": [{"type": "function", "function": {"name": "commodity_price_lookup", "parameters": {}}}],
        "options": {"temperature": 0.1, "num_predict": 200, "num_ctx": 9999},
    }

    request = gateway.openai_request(payload, "gpt-4o-mini")

    assistant = request["messages"][2]
    assert assistant["tool_calls"][0]["id"] == "abc"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"commodity": "Laranite"}
    assert "thinking" not in assistant
    assert request["messages"][3] == {"role": "tool", "tool_call_id": "abc", "content": "{\"cheapest_to_buy\": null}"}
    assert request["tools"] == payload["tools"]
    assert request["temperature"] == 0.1
    assert request["max_tokens"] == 200
    assert "num_ctx" not in request


def test_openai_request_maps_a_json_schema_format_to_response_format():
    schema = {"type": "object", "properties": {"on_topic": {"type": "boolean"}}}

    request = gateway.openai_request({"messages": [], "format": schema}, "gpt-4o-mini")

    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["schema"] == schema


def test_ollama_final_chunk_parses_tool_call_arguments_and_tags_the_backend():
    openai_response = {
        "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "x", "type": "function",
             "function": {"name": "best_route", "arguments": "{\"origin\": \"Seraphim\"}"}},
        ]}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 30},
    }

    chunk = gateway.ollama_final_chunk(openai_response, "gpt-4o-mini")

    assert chunk["done"] is True
    assert chunk["message"]["content"] == ""
    assert chunk["message"]["tool_calls"] == [{"function": {"name": "best_route", "arguments": {"origin": "Seraphim"}}}]
    assert chunk["prompt_eval_count"] == 1200
    assert chunk["eval_count"] == 30
    assert chunk["alice_backend"] == "openai"


def test_openai_cost_counts_cached_tokens_at_the_cache_rate():
    rates = gateway.MODEL_PRICING[("openai", "gpt-4o-mini")]

    cost = gateway.openai_cost_usd("gpt-4o-mini", prompt_tokens=1000, cache_read_tokens=400, completion_tokens=100)

    assert cost == pytest.approx((600 * rates.input + 400 * rates.cache_read + 100 * rates.output) / 1_000_000)
    assert gateway.openai_cost_usd("some-unpriced-model", 10, 0, 10) is None


# ---- routing ----

async def test_healthy_frankencompute_is_passed_through(monkeypatch, usage_rows):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"}, text=ndjson(
            {"model": "gemma4", "message": {"role": "assistant", "content": "Try "}, "done": False},
            {"model": "gemma4", "message": {"role": "assistant", "content": "Area18."}, "done": True,
             "prompt_eval_count": 900, "eval_count": 12},
        ))

    route_http(monkeypatch, handler)

    relay = await gateway.open_chat(CHAT_PAYLOAD, USER_ID)
    chunks = await collect(relay)

    assert seen["url"].endswith("/api/chat")
    assert seen["body"]["model"] == settings.frankencompute_chat_model
    assert relay.status_code == 200
    assert [chunk["message"]["content"] for chunk in chunks] == ["Try ", "Area18."]
    assert chunks[-1]["alice_backend"] == "frankencompute"
    assert usage_rows[0]["backend"] == "frankencompute"
    assert usage_rows[0]["prompt_tokens"] == 900
    assert usage_rows[0]["fallback_reason"] is None
    assert gateway.state.inflight == 0


async def test_unreachable_frankencompute_falls_back_and_benches_the_box(monkeypatch, usage_rows):
    def handler(request):
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Area18 is cheapest."}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 8},
            })
        raise httpx.ConnectError("connection refused", request=request)

    route_http(monkeypatch, handler)

    relay = await gateway.open_chat(CHAT_PAYLOAD, USER_ID)
    chunks = await collect(relay)

    assert len(chunks) == 1
    assert chunks[0]["message"]["content"] == "Area18 is cheapest."
    assert chunks[0]["alice_backend"] == "openai"
    assert usage_rows[0]["backend"] == "openai"
    assert usage_rows[0]["fallback_reason"] == "down"
    assert usage_rows[0]["cost_usd"] > 0
    assert gateway.state.down_reason == "down"
    assert gateway.state.down_until > 0


async def test_a_frankencompute_5xx_falls_back(monkeypatch, usage_rows):
    def handler(request):
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})
        return httpx.Response(500, text="model crashed")

    route_http(monkeypatch, handler)

    await collect(await gateway.open_chat(CHAT_PAYLOAD, USER_ID))

    assert usage_rows[0]["fallback_reason"] == "5xx"


async def test_benched_frankencompute_is_skipped_without_trying_it(monkeypatch, usage_rows):
    tried = []

    def handler(request):
        tried.append(request.url.host)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    route_http(monkeypatch, handler)
    gateway.state.mark_down("timeout", until=float("inf"))

    await collect(await gateway.open_chat(CHAT_PAYLOAD, USER_ID))

    assert tried == ["api.openai.com"]
    assert usage_rows[0]["fallback_reason"] == "timeout"


async def test_overflow_goes_to_fallback(monkeypatch, usage_rows):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    route_http(monkeypatch, handler)
    gateway.state.inflight = 2

    await collect(await gateway.open_chat(CHAT_PAYLOAD, USER_ID))

    assert usage_rows[0]["fallback_reason"] == "overflow"


async def test_no_fallback_key_means_no_backend(monkeypatch, usage_rows):
    monkeypatch.setattr(settings, "openai_api_key", "")
    gateway.state.mark_down("down", until=float("inf"))

    with pytest.raises(gateway.NoBackendAvailable):
        await gateway.open_chat(CHAT_PAYLOAD, USER_ID)


async def test_unknown_role_is_rejected():
    with pytest.raises(gateway.UnknownRole):
        await gateway.open_chat({**CHAT_PAYLOAD, "model": "gpt-4o"}, USER_ID)


# ---- health probe ----

async def test_probe_marks_a_digest_mismatch_as_down(monkeypatch):
    monkeypatch.setattr(settings, "frankencompute_model_digest", "dc35e8d9c606")

    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "gemma4:latest", "digest": "eb2177adf764aaaa"}]})

    route_http(monkeypatch, handler)

    await gateway.probe_once()

    assert gateway.state.down_reason == "digest_mismatch"


async def test_probe_marks_the_pinned_digest_as_up(monkeypatch):
    monkeypatch.setattr(settings, "frankencompute_model_digest", "dc35e8d9c606")
    gateway.state.mark_down("down", until=float("inf"))

    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "gemma4:latest", "digest": "dc35e8d9c606bbbb"}]})

    route_http(monkeypatch, handler)

    await gateway.probe_once()

    assert gateway.state.down_reason is None
    assert gateway.state.down_until == 0.0


# ---- HTTP route ----

def test_route_requires_authentication():
    response = TestClient(app).post("/llm/api/chat", json=CHAT_PAYLOAD)

    assert response.status_code in (401, 403)


def test_route_rejects_an_unknown_role_and_an_oversized_body():
    app.dependency_overrides[get_current_user] = lambda: User(id=USER_ID, discord_id="123")
    try:
        client = TestClient(app)
        unknown = client.post("/llm/api/chat", json={**CHAT_PAYLOAD, "model": "gpt-4o"})
        oversized = client.post("/llm/api/chat", content=b"x" * (600 * 1024))
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert unknown.status_code == 400
    assert oversized.status_code == 413
