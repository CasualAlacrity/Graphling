"""Shared turn-level metrics — tokens, cost, latency — for one `graph.ainvoke()` call.

Used by both the eval harness (evals/agent_eval/) and production (voice/__init__.py)
so the two never compute this differently. Pure and side-effect-free: takes a
turn's already-returned messages and a wall-clock duration, returns a plain
TurnMetrics record. Callers decide what to do with it (write to a report file, log
to the ledger's metrics table, attach to a LangSmith run) — this module doesn't.
"""

import os

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel


class TurnMetrics(BaseModel):
    model_provider: str
    model_name: str
    tokens_in: int
    tokens_out: int
    tokens_total: int
    latency_ms: float
    cost_usd: float | None  # None only if there's no pricing/power entry for this model


# (provider, model) -> ($ per 1M input tokens, $ per 1M output tokens). Hosted APIs
# only -- a local model has no per-token bill, see LOCAL_MODEL_POWER_WATTS below for
# how Ollama entries are priced instead. Manually maintained: verify against each
# provider's own current pricing page before trusting a cost total for real -- these
# are the rates known when this table was written, not a live lookup.
MODEL_PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-4o-mini"): (0.15, 0.60),
    ("anthropic", "claude-haiku-4-5-20251001"): (1.00, 5.00),
}

# A local model isn't free -- electricity is the real cost, and it doesn't scale
# with tokens the way a hosted API's bill does. It scales with wall-clock time the
# GPU is actually busy, so that's what this prices: estimated system power draw (in
# watts) x this turn's own latency_ms x an electricity rate. This also means a
# turn's classify_topic call (also local, also real GPU time, but not counted in
# tokens_in/out -- see compute_turn_metrics' docstring) still gets priced correctly
# here, since latency_ms wraps the whole graph.ainvoke() call regardless.
#
# Watts are rough estimates, not measured: RTX 4080 reference TDP (320W) + ~100W
# guessed for the rest of the system (CPU/RAM/motherboard/PSU losses) while
# actively inferencing. Refine with a real wall-meter (Kill-A-Watt or similar)
# reading under load if more precision matters later.
LOCAL_MODEL_POWER_WATTS: dict[str, float] = {
    "gemma4": 420.0,
    "qwen2.5:14b": 420.0,  # same box/GPU as gemma4 -- a bigger model mostly shows up as
                           # more latency, not more watts, so the same draw estimate holds
    "qwen3.5:9b": 420.0,
    "gemma4:12b": 420.0,
}

# $/kWh -- Jeff's real rate, not a guess. Override via this env var if it changes
# or this ever runs on someone else's meter.
ELECTRICITY_RATE_USD_PER_KWH = float(os.getenv("ELECTRICITY_RATE_USD_PER_KWH", "0.34"))


def _local_cost_usd(model_name: str, latency_ms: float) -> float | None:
    watts = LOCAL_MODEL_POWER_WATTS.get(model_name)
    if watts is None:
        print(
            f"turn_metrics: no power estimate for local model {model_name!r} -- cost_usd "
            f"will be None. Add a wattage to LOCAL_MODEL_POWER_WATTS if this model is "
            f"going to be benchmarked for real."
        )
        return None

    kwh = (watts / 1000) * (latency_ms / 3_600_000)
    return kwh * ELECTRICITY_RATE_USD_PER_KWH


def _cost_usd(
    model_provider: str, model_name: str, tokens_in: int, tokens_out: int, latency_ms: float,
) -> float | None:
    if model_provider == "ollama":
        return _local_cost_usd(model_name, latency_ms)

    rates = MODEL_PRICING.get((model_provider, model_name))
    if rates is None:
        print(
            f"turn_metrics: no pricing entry for ({model_provider!r}, {model_name!r}) "
            f"-- cost_usd will be None. Add a rate to MODEL_PRICING if this model is "
            f"going to be benchmarked for real."
        )
        return None

    input_rate, output_rate = rates
    return (tokens_in / 1_000_000) * input_rate + (tokens_out / 1_000_000) * output_rate


def compute_turn_metrics(
    messages: list[BaseMessage], latency_ms: float, model_provider: str, model_name: str,
) -> TurnMetrics:
    """Sums usage_metadata across every AIMessage in `messages` -- a turn can involve
    several respond<->tools round trips, each its own LLM call, and all of them count
    toward the turn's real cost. An AIMessage with no usage_metadata (shouldn't happen
    for the three providers this project wires up, but not asserted here) contributes
    zero rather than raising, so one odd message can't take down the whole computation.

    Known gap, documented rather than solved: classify_topic's own structured-output
    call isn't in `messages` at all (its result is state, not a message), so its
    tokens aren't counted here. Turn-level latency is still accurate regardless, since
    it's expected to wrap the entire graph.ainvoke() call, not just the messages loop.
    """
    tokens_in = 0
    tokens_out = 0
    for message in messages:
        if isinstance(message, AIMessage) and message.usage_metadata:
            tokens_in += message.usage_metadata.get("input_tokens", 0)
            tokens_out += message.usage_metadata.get("output_tokens", 0)

    return TurnMetrics(
        model_provider=model_provider,
        model_name=model_name,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_total=tokens_in + tokens_out,
        latency_ms=latency_ms,
        cost_usd=_cost_usd(model_provider, model_name, tokens_in, tokens_out, latency_ms),
    )
