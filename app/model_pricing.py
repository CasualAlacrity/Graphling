"""Hosted-model token prices, shared by the desktop client (turn_metrics.py) and the
server (server/llm_gateway_service.py, which prices fallback calls for llm_usage).

Its own module, not part of turn_metrics.py, because turn_metrics imports langchain_core
and the server image deliberately doesn't install LangChain (see app/server/requirements.txt).
"""
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
    ("anthropic", "claude-sonnet-5-5"): ModelRates(
        input=2.00, output=10.00, cache_read=0.20, cache_write_5m=2.50, cache_write_1h=4.00,
    ),
}
