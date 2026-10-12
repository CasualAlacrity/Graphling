"""Covers the usage/cost half of turn_metrics.py: splitting LangChain's usage_metadata
into disjoint uncached/cache-read/cache-write tokens, pricing each call by its own
provider+model, and TurnMetricsCallback recording one LLMCallUsage per LLM call --
classify_topic/decline included, the gap the old messages-based sum had.

The Anthropic usage shape below is copied from a real langchain-anthropic 1.4.8 response
(2026-10-06): writes land in ephemeral_5m_input_tokens while cache_creation stays 0."""
import json
import uuid
from types import SimpleNamespace

import pytest

import model_pricing
from turn_metrics import (
    LLMCallUsage,
    TurnMetricsCallback,
    TurnTimings,
    TurnUsage,
    call_cost_usd,
    turn_metrics_record,
    usage_from_metadata,
)

ANTHROPIC_WRITE = {
    "input_tokens": 13371, "output_tokens": 59, "total_tokens": 13430,
    "input_token_details": {
        "cache_read": 0, "cache_creation": 0, "ephemeral_5m_input_tokens": 13035, "ephemeral_1h_input_tokens": 0,
    },
}
ANTHROPIC_READ = {
    "input_tokens": 13366, "output_tokens": 71, "total_tokens": 13437,
    "input_token_details": {
        "cache_read": 13035, "cache_creation": 0, "ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0,
    },
}
OPENAI_READ = {
    "input_tokens": 10000, "output_tokens": 200, "total_tokens": 10200,
    "input_token_details": {"cache_read": 8000}, "output_token_details": {"reasoning": 0},
}


def _llm_result(usage_metadata: dict | None):
    message = SimpleNamespace(usage_metadata=usage_metadata)
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]])


def _metadata(node: str, provider: str, model: str) -> dict:
    return {"langgraph_node": node, "ls_provider": provider, "ls_model_name": model}


def test_anthropic_write_splits_out_of_input():
    tokens = usage_from_metadata(ANTHROPIC_WRITE)
    assert tokens == {
        "input_tokens": 336, "cache_read_tokens": 0, "cache_write_5m_tokens": 13035,
        "cache_write_1h_tokens": 0, "output_tokens": 59,
    }


def test_anthropic_read_splits_out_of_input():
    tokens = usage_from_metadata(ANTHROPIC_READ)
    assert tokens["input_tokens"] == 331
    assert tokens["cache_read_tokens"] == 13035
    assert tokens["cache_write_5m_tokens"] == 0


def test_cache_creation_and_ephemeral_split_not_double_counted():
    usage = {
        "input_tokens": 5000, "output_tokens": 10,
        "input_token_details": {
            "cache_creation": 4000, "ephemeral_5m_input_tokens": 3000, "ephemeral_1h_input_tokens": 1000,
        },
    }
    tokens = usage_from_metadata(usage)
    assert tokens["cache_write_5m_tokens"] == 3000
    assert tokens["cache_write_1h_tokens"] == 1000
    assert tokens["input_tokens"] == 1000


def test_missing_usage_is_all_zero():
    assert usage_from_metadata(None) == {
        "input_tokens": 0, "cache_read_tokens": 0, "cache_write_5m_tokens": 0,
        "cache_write_1h_tokens": 0, "output_tokens": 0,
    }


def test_haiku_cached_read_priced_at_tenth():
    call = LLMCallUsage(node="respond", provider="anthropic", model="claude-haiku-4-5-20251001",
                        **usage_from_metadata(ANTHROPIC_READ))
    expected = (331 * 1.00 + 13035 * 0.10 + 71 * 5.00) / 1_000_000
    assert call_cost_usd(call) == pytest.approx(expected)


def test_haiku_cache_write_priced_at_premium():
    call = LLMCallUsage(node="respond", provider="anthropic", model="claude-haiku-4-5-20251001",
                        **usage_from_metadata(ANTHROPIC_WRITE))
    expected = (336 * 1.00 + 13035 * 1.25 + 59 * 5.00) / 1_000_000
    assert call_cost_usd(call) == pytest.approx(expected)


def test_openai_cached_read_priced_at_half():
    call = LLMCallUsage(node="respond", provider="openai", model="gpt-4o-mini", **usage_from_metadata(OPENAI_READ))
    expected = (2000 * 0.15 + 8000 * 0.075 + 200 * 0.60) / 1_000_000
    assert call_cost_usd(call) == pytest.approx(expected)


def test_ollama_priced_by_call_duration(monkeypatch):
    monkeypatch.setattr(model_pricing, "ELECTRICITY_EUR_PER_KWH", 0.34)
    monkeypatch.setattr(model_pricing, "USD_PER_EUR", 1.10)
    call = LLMCallUsage(node="respond", provider="ollama", model="gemma4", latency_ms=3_600_000)
    # One busy hour at the measured 210 W above idle.
    assert call_cost_usd(call) == pytest.approx(0.210 * 0.34 * 1.10)


def test_unknown_hosted_model_is_unpriced():
    call = LLMCallUsage(node="respond", provider="openai", model="gpt-unknown", input_tokens=100)
    assert call_cost_usd(call) is None


async def test_callback_records_every_node_with_its_own_model():
    callback = TurnMetricsCallback()
    calls = [
        ("classify_topic", "anthropic", "claude-haiku-4-5", {"input_tokens": 400, "output_tokens": 20}),
        ("respond", "anthropic", "claude-haiku-4-5-20251001", ANTHROPIC_WRITE),
        ("respond", "anthropic", "claude-haiku-4-5-20251001", ANTHROPIC_READ),
    ]
    for node, provider, model, usage in calls:
        run_id = uuid.uuid4()
        await callback.on_chat_model_start({}, [], run_id=run_id, metadata=_metadata(node, provider, model))
        await callback.on_llm_end(_llm_result(usage), run_id=run_id)

    usage = callback.usage
    nodes = [call.node for call in usage.calls]
    assert nodes == ["classify_topic", "respond", "respond"]
    assert usage.prompt_tokens == 400 + 13371 + 13366
    assert usage.cache_read_tokens == 13035
    assert usage.cache_write_tokens == 13035
    assert usage.unpriced_calls == 0
    assert set(usage.cost_by_node) == {"classify_topic", "respond"}
    assert usage.cost_usd == pytest.approx(sum(call.cost_usd for call in usage.calls))


async def test_callback_tolerates_missing_response_and_metadata():
    callback = TurnMetricsCallback()
    run_id = uuid.uuid4()
    await callback.on_chat_model_start({}, [], run_id=run_id)
    await callback.on_llm_end(None, run_id=run_id)

    call = callback.usage.calls[0]
    assert (call.node, call.provider, call.model) == ("unknown", "unknown", "unknown")
    assert call.prompt_tokens == 0
    assert callback.usage.unpriced_calls == 1


def test_unpriced_call_does_not_blank_turn_cost():
    usage = TurnUsage(calls=[
        LLMCallUsage(node="respond", provider="openai", model="gpt-4o-mini", input_tokens=1000, cost_usd=0.00015),
        LLMCallUsage(node="classify_topic", provider="openai", model="gpt-unknown", input_tokens=100, cost_usd=None),
    ])
    assert usage.cost_usd == pytest.approx(0.00015)
    assert usage.unpriced_calls == 1


def test_cache_hit_rate():
    usage = TurnUsage(calls=[LLMCallUsage(node="respond", provider="anthropic", model="m",
                                          **usage_from_metadata(ANTHROPIC_READ))])
    assert usage.cache_hit_rate == pytest.approx(13035 / 13366)
    assert TurnUsage().cache_hit_rate is None


def test_metrics_record_is_json_with_totals():
    usage = TurnUsage(calls=[LLMCallUsage(node="respond", provider="openai", model="gpt-4o-mini",
                                          input_tokens=10, output_tokens=5, cost_usd=0.0001)])
    record = json.loads(json.dumps(turn_metrics_record(TurnTimings(respond_ms=[100.0]), usage, "jeff:abc")))
    assert record["thread_id"] == "jeff:abc"
    assert record["timings"]["respond_ms"] == [100.0]
    assert record["usage"]["cost_usd"] == pytest.approx(0.0001)
    assert record["usage"]["calls"][0]["prompt_tokens"] == 10


def _gateway_result(usage_metadata: dict, backend: str, model: str):
    message = SimpleNamespace(usage_metadata=usage_metadata)
    info = {"alice_backend": backend, "model_name": model}
    return SimpleNamespace(generations=[[SimpleNamespace(message=message, generation_info=info)]])


async def test_gateway_calls_are_priced_by_the_backend_that_answered():
    # LLM_PROVIDER=alice: LangChain only knows the role ("alice-chat" on "ollama"); the
    # gateway's final chunk says what really answered.
    callback = TurnMetricsCallback()
    for backend, model in [("frankencompute", "gemma4"), ("openai", "gpt-4o-mini")]:
        run_id = uuid.uuid4()
        await callback.on_chat_model_start({}, [], run_id=run_id, metadata=_metadata("respond", "ollama", "alice-chat"))
        await callback.on_llm_end(
            _gateway_result({"input_tokens": 1000, "output_tokens": 10}, backend, model), run_id=run_id,
        )

    local, fallback = callback.usage.calls
    assert (local.provider, local.model) == ("ollama", "gemma4")
    assert (fallback.provider, fallback.model) == ("openai", "gpt-4o-mini")
    assert fallback.cost_usd == pytest.approx((1000 * 0.15 + 10 * 0.60) / 1_000_000)
    assert callback.usage.unpriced_calls == 0
