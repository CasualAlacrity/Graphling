"""Shared turn-level health metrics -- time, tokens, cost -- for one `graph.ainvoke()` call.

Used by both the eval harness (evals/agent_eval/) and production (voice/__init__.py)
so the two never compute this differently. TurnMetricsCallback observes every LLM and
tool call LangChain makes during a turn and fills two plain records: TurnTimings
(per-stage durations, docs/voice-stage-timing.md) and TurnUsage (per-call tokens and
cost). Callers decide what to do with them -- the eval writes a report, voice appends
a JSONL line via log_turn_metrics(). That JSONL line's shape is the contract the
eventual server-side analytics DB / Grafana board ingests (docs/todo.md), so fields
get added to it, never renamed.

Usage is captured per LLM call from the callback, not summed from the turn's
AIMessages: classify_topic's and decline's calls never land in state.messages, so a
messages-based sum silently missed them -- a real, billed call on every hosted turn.
"""

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.callbacks import AsyncCallbackHandler
from pydantic import BaseModel, Field, computed_field

from model_pricing import MODEL_PRICING

# A local model isn't free -- electricity is the real cost, and it doesn't scale
# with tokens the way a hosted API's bill does. It scales with wall-clock time the
# GPU is actually busy, so that's what this prices: estimated system power draw (in
# watts) x that one LLM call's own duration x an electricity rate. Per call, not per
# turn: a turn's wall-clock also includes tool time (UEX/wiki/ledger HTTP), when the
# GPU is idle.
#
# Watts are rough estimates, not measured: RTX 4080 reference TDP (320W) + ~100W
# guessed for the rest of the system (CPU/RAM/motherboard/PSU losses) while
# actively inferencing. Refine with a real wall-meter (Kill-A-Watt or similar)
# reading under load if more precision matters later.
#
# Every local model measured so far gets the same estimate -- it's the same box/GPU
# regardless of which model is loaded, and a bigger model mostly shows up as more
# latency, not more watts. DEFAULT_LOCAL_POWER_WATTS is that shared estimate; entries
# below only exist for a model that's genuinely known to differ (none do yet).
DEFAULT_LOCAL_POWER_WATTS = 420.0
LOCAL_MODEL_POWER_WATTS: dict[str, float] = {}

# $/kWh -- Jeff's real rate, not a guess. Override via this env var if it changes
# or this ever runs on someone else's meter.
ELECTRICITY_RATE_USD_PER_KWH = float(os.getenv("ELECTRICITY_RATE_USD_PER_KWH", "0.34"))

_warned_unpriced: set[tuple[str, str]] = set()


class LLMCallUsage(BaseModel):
    """One LLM call's tokens and cost. Token fields are disjoint -- input_tokens is
    UNCACHED input only, so input + cache_read + cache_write_5m + cache_write_1h is the
    whole prompt. (LangChain's usage_metadata["input_tokens"] is that whole-prompt total
    instead; usage_from_metadata() splits it.)"""
    node: str
    provider: str
    model: str
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float | None = None  # None = hosted model with no MODEL_PRICING entry

    @computed_field
    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_5m_tokens + self.cache_write_1h_tokens


def usage_from_metadata(usage_metadata: dict | None) -> dict[str, int]:
    """LangChain's usage_metadata -> LLMCallUsage's disjoint token fields.

    Both hosted providers report input_tokens as the whole prompt, cached parts
    included, with the cached parts broken out in input_token_details. Anthropic
    reports cache writes under ephemeral_5m/1h_input_tokens (cache_creation was 0 when
    checked live 2026-10-06, langchain-anthropic 1.4.8) -- max() of the two forms, so
    a future version filling both doesn't double count."""
    usage_metadata = usage_metadata or {}
    details = usage_metadata.get("input_token_details") or {}

    cache_read = details.get("cache_read") or 0
    write_1h = details.get("ephemeral_1h_input_tokens") or 0
    write_5m_split = details.get("ephemeral_5m_input_tokens") or 0
    write_total = max(details.get("cache_creation") or 0, write_5m_split + write_1h)
    write_5m = write_total - write_1h

    prompt_tokens = usage_metadata.get("input_tokens") or 0
    uncached = max(0, prompt_tokens - cache_read - write_total)
    return {
        "input_tokens": uncached,
        "cache_read_tokens": cache_read,
        "cache_write_5m_tokens": write_5m,
        "cache_write_1h_tokens": write_1h,
        "output_tokens": usage_metadata.get("output_tokens") or 0,
    }


def call_cost_usd(call: LLMCallUsage) -> float | None:
    if call.provider == "ollama":
        watts = LOCAL_MODEL_POWER_WATTS.get(call.model, DEFAULT_LOCAL_POWER_WATTS)
        kwh = (watts / 1000) * (call.latency_ms / 3_600_000)
        return kwh * ELECTRICITY_RATE_USD_PER_KWH

    rates = MODEL_PRICING.get((call.provider, call.model))
    if rates is None:
        key = (call.provider, call.model)
        if key not in _warned_unpriced:
            _warned_unpriced.add(key)
            print(
                f"turn_metrics: no pricing entry for {key!r} -- its calls are counted in "
                f"unpriced_calls, not cost_usd. Add it to MODEL_PRICING."
            )
        return None

    dollars = 0.0
    dollars += call.input_tokens * rates.input
    dollars += call.cache_read_tokens * rates.cache_read
    dollars += call.cache_write_5m_tokens * rates.cache_write_5m
    dollars += call.cache_write_1h_tokens * rates.cache_write_1h
    dollars += call.output_tokens * rates.output
    return dollars / 1_000_000


class TurnUsage(BaseModel):
    """Every LLM call one turn made, plus the totals a dashboard actually plots. Totals
    are computed fields so they land in model_dump()/the JSONL line without a caller
    having to remember to compute them."""
    calls: list[LLMCallUsage] = Field(default_factory=list)

    def _sum(self, field: str) -> int:
        total = 0
        for call in self.calls:
            total += getattr(call, field)
        return total

    @computed_field
    @property
    def input_tokens(self) -> int:
        return self._sum("input_tokens")

    @computed_field
    @property
    def cache_read_tokens(self) -> int:
        return self._sum("cache_read_tokens")

    @computed_field
    @property
    def cache_write_tokens(self) -> int:
        return self._sum("cache_write_5m_tokens") + self._sum("cache_write_1h_tokens")

    @computed_field
    @property
    def prompt_tokens(self) -> int:
        return self._sum("prompt_tokens")

    @computed_field
    @property
    def output_tokens(self) -> int:
        return self._sum("output_tokens")

    @computed_field
    @property
    def cache_hit_rate(self) -> float | None:
        """Share of prompt tokens served from cache -- the number that says whether
        caching is actually working (0 on a hosted turn = a silent cache invalidator)."""
        if self.prompt_tokens == 0:
            return None
        return self.cache_read_tokens / self.prompt_tokens

    @computed_field
    @property
    def cost_usd(self) -> float:
        """Priced calls only -- see unpriced_calls. Never None: a dashboard summing
        cost across turns shouldn't have one unknown model blank out the whole total."""
        total = 0.0
        for call in self.calls:
            if call.cost_usd is not None:
                total += call.cost_usd
        return total

    @computed_field
    @property
    def unpriced_calls(self) -> int:
        """Calls with no MODEL_PRICING entry -- nonzero means cost_usd is an undercount.
        Worth an alert on its own: it's real spend nobody's tracking."""
        count = 0
        for call in self.calls:
            if call.cost_usd is None:
                count += 1
        return count

    @computed_field
    @property
    def cost_by_node(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for call in self.calls:
            totals[call.node] = totals.get(call.node, 0.0) + (call.cost_usd or 0.0)
        return totals

    def summary_line(self) -> str:
        parts = [
            f"prompt={self.prompt_tokens}",
            f"cached={self.cache_read_tokens}",
            f"written={self.cache_write_tokens}",
            f"out={self.output_tokens}",
            f"cost=${self.cost_usd:.5f}",
        ]
        if self.cache_hit_rate is not None:
            parts.append(f"hit={self.cache_hit_rate:.0%}")
        if self.unpriced_calls:
            parts.append(f"UNPRICED={self.unpriced_calls}")
        return " ".join(parts)


class TurnTimings(BaseModel):
    """Per-stage durations for one turn (docs/voice-stage-timing.md) -- pure data, same
    philosophy as TurnUsage above. A caller builds one via TurnMetricsCallback (the
    in-graph stages) and fills the out-of-graph fields itself (whisper/tts/round_trip)
    around the calls those stages belong to."""

    whisper_ms: float | None = None
    classify_ms: float | None = None
    # decline_topic's reject_llm call -- a real, measurable stage not in this doc's
    # original stage table, added because it's otherwise silently missing from any
    # declined turn's timings.
    decline_ms: float | None = None
    respond_ms: list[float] = Field(default_factory=list)
    tool_ms: list[tuple[str, float]] = Field(default_factory=list)
    graph_ms: float | None = None
    tts_first_chunk_ms: float | None = None
    tts_total_ms: float | None = None
    round_trip_ms: float | None = None

    def summary_line(self) -> str:
        parts = []
        if self.whisper_ms is not None:
            parts.append(f"whisper={self.whisper_ms:.0f}")
        if self.classify_ms is not None:
            parts.append(f"classify={self.classify_ms:.0f}")
        if self.decline_ms is not None:
            parts.append(f"decline={self.decline_ms:.0f}")
        if self.respond_ms:
            parts.append(f"respond=[{'+'.join(f'{ms:.0f}' for ms in self.respond_ms)}]")
        if self.tool_ms:
            parts.append(f"tools=[{', '.join(f'{name}={ms:.0f}' for name, ms in self.tool_ms)}]")
        if self.graph_ms is not None:
            parts.append(f"graph={self.graph_ms:.0f}")
        if self.tts_first_chunk_ms is not None:
            parts.append(f"tts_first={self.tts_first_chunk_ms:.0f}")
        if self.tts_total_ms is not None:
            parts.append(f"tts_total={self.tts_total_ms:.0f}")
        if self.round_trip_ms is not None:
            parts.append(f"round_trip={self.round_trip_ms:.0f}")
        return "ms " + " ".join(parts)


def _usage_metadata(response) -> dict | None:
    """usage_metadata off an on_llm_end LLMResult -- the AIMessage inside its first
    generation. None for anything without one (a test passing None, a non-chat model)."""
    generations = getattr(response, "generations", None)
    if not generations or not generations[0]:
        return None
    message = getattr(generations[0][0], "message", None)
    return getattr(message, "usage_metadata", None)


def _answered_by(response) -> tuple[str, str] | None:
    """(provider, model) of what actually answered a gateway call (LLM_PROVIDER=alice),
    or None for any other provider. The gateway tags its final chunk with alice_backend
    and the real model name (server/llm_gateway_service.py); ChatOllama copies the final
    chunk into generation_info. Without this, every gateway call would be priced as the
    role name ("alice-chat") on electricity, a fallback call to OpenAI included."""
    generations = getattr(response, "generations", None)
    if not generations or not generations[0]:
        return None
    info = getattr(generations[0][0], "generation_info", None) or {}
    backend = info.get("alice_backend")
    if backend is None:
        return None
    provider = "ollama" if backend == "frankencompute" else backend
    return provider, info.get("model_name") or info.get("model") or "unknown"


class TurnMetricsCallback(AsyncCallbackHandler):
    """Fresh one per turn, passed via graph.ainvoke's config={"callbacks": [...]}. Fills
    .timings (per-stage durations) and .usage (one LLMCallUsage per LLM call).
    LangGraph tags every node's LLM/tool runs with metadata["langgraph_node"] -- that's
    how an LLM call gets bucketed into classify_ms/respond_ms/decline_ms without graph.py
    needing to know this exists. Node code is never touched (docs/voice-stage-timing.md's
    hard constraint): this only observes start/end events LangChain already emits.

    Keyed by run_id rather than assuming one call at a time, since respond<->tools can
    round-trip more than once in a single turn and each round trip gets its own run_id.

    Provider/model per call come from metadata["ls_provider"]/["ls_model_name"], which
    LangChain attaches to every chat-model run -- so a classifier on a different model
    than respond (*_CLASSIFICATION_MODEL) gets priced at its own rates."""

    def __init__(self) -> None:
        self.timings = TurnTimings()
        self.usage = TurnUsage()
        self._llm_start: dict[uuid.UUID, float] = {}
        self._llm_node: dict[uuid.UUID, str] = {}
        self._llm_model: dict[uuid.UUID, tuple[str, str]] = {}
        self._tool_start: dict[uuid.UUID, float] = {}
        self._tool_name: dict[uuid.UUID, str] = {}

    async def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs,
    ) -> None:
        metadata = metadata or {}
        self._llm_start[run_id] = time.monotonic()
        self._llm_node[run_id] = metadata.get("langgraph_node", "unknown")
        self._llm_model[run_id] = (metadata.get("ls_provider", "unknown"), metadata.get("ls_model_name", "unknown"))

    async def on_llm_end(self, response, *, run_id, parent_run_id=None, tags=None, **kwargs) -> None:
        start = self._llm_start.pop(run_id, None)
        node = self._llm_node.pop(run_id, None)
        provider, model = self._llm_model.pop(run_id, ("unknown", "unknown"))
        if start is None:
            return
        provider, model = _answered_by(response) or (provider, model)
        duration_ms = (time.monotonic() - start) * 1000
        if node == "classify_topic":
            self.timings.classify_ms = duration_ms
        elif node == "respond":
            self.timings.respond_ms.append(duration_ms)
        elif node == "decline":
            self.timings.decline_ms = duration_ms

        call = LLMCallUsage(
            node=node or "unknown", provider=provider, model=model, latency_ms=duration_ms,
            **usage_from_metadata(_usage_metadata(response)),
        )
        call.cost_usd = call_cost_usd(call)
        self.usage.calls.append(call)

    async def on_llm_error(self, error, *, run_id, parent_run_id=None, tags=None, **kwargs) -> None:
        # A failed call may still have been billed, but there's no usage to read off an
        # exception -- dropped rather than guessed at.
        self._llm_start.pop(run_id, None)
        self._llm_node.pop(run_id, None)
        self._llm_model.pop(run_id, None)

    async def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None, tags=None, metadata=None, inputs=None, **kwargs,
    ) -> None:
        self._tool_start[run_id] = time.monotonic()
        self._tool_name[run_id] = serialized.get("name", "unknown")

    async def on_tool_end(self, output, *, run_id, parent_run_id=None, tags=None, **kwargs) -> None:
        start = self._tool_start.pop(run_id, None)
        name = self._tool_name.pop(run_id, None)
        if start is None:
            return
        duration_ms = (time.monotonic() - start) * 1000
        self.timings.tool_ms.append((name or "unknown", duration_ms))

    async def on_tool_error(self, error, *, run_id, parent_run_id=None, tags=None, **kwargs) -> None:
        self._tool_start.pop(run_id, None)
        self._tool_name.pop(run_id, None)


def _default_metrics_log_path() -> Path:
    # User's app data, not the repo -- a local per-pilot log for now, never sent to the
    # server (docs/voice-stage-timing.md). Its line shape is what the eventual analytics
    # ingestion reads, so it's built to be shipped as-is later.
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "ALICE" / "turn_metrics.jsonl"


def turn_metrics_record(timings: TurnTimings, usage: TurnUsage, thread_id: str | None = None) -> dict:
    """One turn's JSONL line: when, which conversation, how long each stage took, and
    what every LLM call cost. thread_id lets a dashboard roll turns up into sessions."""
    return {
        "ts": datetime.now(UTC).isoformat(),
        "thread_id": thread_id,
        "timings": timings.model_dump(),
        "usage": usage.model_dump(),
    }


def log_turn_metrics(timings: TurnTimings, usage: TurnUsage, thread_id: str | None = None) -> None:
    """Prints the compact [ALICE]-style summaries and best-effort appends one JSON line
    to a local file (path overridable via ALICE_METRICS_LOG_PATH) -- a write failure here
    must never break a real voice turn, same principle as prewarm's per-target isolation."""
    print(f"[ALICE] timings: {timings.summary_line()}")
    print(f"[ALICE] usage: {usage.summary_line()}")

    path = Path(os.getenv("ALICE_METRICS_LOG_PATH", str(_default_metrics_log_path())))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(turn_metrics_record(timings, usage, thread_id)) + "\n")
    except OSError as exc:
        print(f"[ALICE] couldn't write metrics log ({exc}) -- continuing without it")
