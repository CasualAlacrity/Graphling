"""Shared turn-level metrics — tokens, cost, latency — for one `graph.ainvoke()` call.

Used by both the eval harness (evals/agent_eval/) and production (voice/__init__.py)
so the two never compute this differently. Pure and side-effect-free: takes a
turn's already-returned messages and a wall-clock duration, returns a plain
TurnMetrics record. Callers decide what to do with it (write to a report file, log
to the ledger's metrics table, attach to a LangSmith run) — this module doesn't.
"""

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel


class TurnMetrics(BaseModel):
    model_provider: str
    model_name: str
    tokens_in: int
    tokens_out: int
    tokens_total: int
    latency_ms: float
    cost_usd: float | None  # None only if (model_provider, model_name) isn't in MODEL_PRICING


# (provider, model) -> ($ per 1M input tokens, $ per 1M output tokens). Ollama is
# local compute, priced at 0 on purpose -- latency/tokens still tell the real story
# for a local model, just not in dollars. Manually maintained: verify against each
# provider's own current pricing page before trusting a cost total for real --
# these are the rates known when this table was written, not a live lookup.
MODEL_PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-4o-mini"): (0.15, 0.60),
    ("anthropic", "claude-haiku-4-5-20251001"): (1.00, 5.00),
    ("ollama", "gemma4"): (0.0, 0.0),
}


def _cost_usd(model_provider: str, model_name: str, tokens_in: int, tokens_out: int) -> float | None:
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
        cost_usd=_cost_usd(model_provider, model_name, tokens_in, tokens_out),
    )
