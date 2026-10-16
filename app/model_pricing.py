"""Hosted-model token prices and heket's electricity price, shared by the desktop
client (turn_metrics.py) and the server (server/llm_gateway_service.py and
server/tts_service.py, which price every call for llm_usage / tts_usage).

Its own module, not part of turn_metrics.py, because turn_metrics imports langchain_core
and the server image deliberately doesn't install LangChain (see app/server/requirements.txt).
"""
import os

from pydantic import BaseModel


class ModelRates(BaseModel):
    """$ per 1M tokens. Cached-input rates are separate because both hosted providers
    bill them differently from plain input: Anthropic reads at 0.1x and writes at 1.25x
    (5-minute TTL) or 2x (1-hour); OpenAI reads at 0.5x with no write premium, so its
    write rates just equal `input`."""
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float


_HAIKU_4_5 = ModelRates(input=1.00, output=5.00, cache_read=0.10, cache_write_5m=1.25, cache_write_1h=2.00)

# (provider, model) -> rates. Keyed by the model name as CONFIGURED (.env's *_MODEL vars,
# which is what LangChain reports as ls_model_name), not whatever dated snapshot the API
# echoes back. Hosted APIs only -- Ollama is priced by electricity, see below. Manually
# maintained: verify against each provider's current pricing page whenever an entry is
# added or a bill doesn't match. Checked 2026-10-06 (Anthropic from the claude-api
# reference; gpt-4o-mini from the rates in use since this table was first written --
# re-verify against OpenAI's page before trusting a production cost).
MODEL_PRICING: dict[tuple[str, str], ModelRates] = {
    ("openai", "gpt-4o-mini"): ModelRates(
        input=0.15, output=0.60, cache_read=0.075, cache_write_5m=0.15, cache_write_1h=0.15,
    ),
    ("anthropic", "claude-haiku-4-5-20251001"): _HAIKU_4_5,
    ("anthropic", "claude-haiku-4-5"): _HAIKU_4_5,
    # Prompts of 100K tokens or fewer -- ALICE's are ~20K. Above 100K the rate is $0.50 / $2.50
    # (5x); not modeled here. From the claude-api reference, 2026-10-09.
    ("anthropic", "claude-haiku-5-5"): ModelRates(
        input=0.10, output=0.50, cache_read=0.01, cache_write_5m=0.125, cache_write_1h=0.20,
    ),
    ("anthropic", "claude-sonnet-5-5"): ModelRates(
        input=2.00, output=10.00, cache_read=0.20, cache_write_5m=2.50, cache_write_1h=4.00,
    ),
}


# ---- Local hardware (heket) ----
#
# A local call costs electricity, which scales with how long the box is busy, not with
# tokens. Measured 2026-10-11 with a smart plug at the wall (docs/heket-bench.md):
# the box idles at ~80 W whether or not a model is loaded, and a gemma4 turn used
# ~0.17 Wh above idle over ~2.9 s of model time, i.e. ~210 W on top of idle while busy.
#
# Per call this prices only that MARGINAL energy. The idle draw (~80 W, ~EUR 20/month)
# is a fixed cost of having the box on at all -- report it as its own line, not spread
# across calls, or cost per call would depend on how busy the month was.
#
# Known overcount: with parallel slots (OLLAMA_NUM_PARALLEL=3) overlapping calls each
# bill their whole duration, though they share the GPU. Small at today's concurrency.
LOCAL_BUSY_WATTS_ABOVE_IDLE = 210.0
LOCAL_IDLE_WATTS = 80.0

# Jeff's tariff is in euros; every cost column is in USD because the hosted providers
# bill in USD. USD_PER_EUR is approximate -- set the env var to the current rate when a
# precise figure matters (it moves a cost that is fractions of a cent per call).
ELECTRICITY_EUR_PER_KWH = float(os.getenv("ELECTRICITY_EUR_PER_KWH", "0.34"))
USD_PER_EUR = float(os.getenv("USD_PER_EUR", "1.10"))


def local_energy_cost_usd(busy_ms: float, watts: float = LOCAL_BUSY_WATTS_ABOVE_IDLE) -> float:
    """Electricity for `busy_ms` of heket work, above idle, in USD."""
    kwh = (watts / 1000) * (busy_ms / 3_600_000)
    return kwh * ELECTRICITY_EUR_PER_KWH * USD_PER_EUR
