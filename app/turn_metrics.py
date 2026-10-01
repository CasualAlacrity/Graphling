"""Shared turn-level metrics — tokens, cost, latency — for one `graph.ainvoke()` call.

Used by both the eval harness (evals/agent_eval/) and production (voice/__init__.py)
so the two never compute this differently. Pure and side-effect-free: takes a
turn's already-returned messages and a wall-clock duration, returns a plain
TurnMetrics record. Callers decide what to do with it (write to a report file, log
to the ledger's metrics table, attach to a LangSmith run) — this module doesn't.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, Field


class TurnMetrics(BaseModel):
    model_provider: str
    model_name: str
    tokens_in: int
    tokens_out: int
    tokens_total: int
    latency_ms: float
    cost_usd: float | None  # None only for an unlisted *hosted* model -- every local
                            # (Ollama) model gets a cost via DEFAULT_LOCAL_POWER_WATTS


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
#
# Every local model measured so far gets the same estimate -- it's the same box/GPU
# regardless of which model is loaded, and a bigger model mostly shows up as more
# latency, not more watts. DEFAULT_LOCAL_POWER_WATTS is that shared estimate; entries
# below only exist for a model that's genuinely known to differ (none do yet). Any
# model name not in the dict -- including a composite label like "respond=gemma4:12b+
# classify=gemma4" for a per-role split config (graph.py's OLLAMA_RESPOND_MODEL/
# OLLAMA_CLASSIFY_MODEL) -- falls back to the default rather than reporting no cost.
DEFAULT_LOCAL_POWER_WATTS = 420.0
LOCAL_MODEL_POWER_WATTS: dict[str, float] = {}

# $/kWh -- Jeff's real rate, not a guess. Override via this env var if it changes
# or this ever runs on someone else's meter.
ELECTRICITY_RATE_USD_PER_KWH = float(os.getenv("ELECTRICITY_RATE_USD_PER_KWH", "0.34"))


def _local_cost_usd(model_name: str, latency_ms: float) -> float:
    watts = LOCAL_MODEL_POWER_WATTS.get(model_name, DEFAULT_LOCAL_POWER_WATTS)

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


class TurnTimings(BaseModel):
    """Per-stage durations for one turn (docs/voice-stage-timing.md) -- pure data, same
    philosophy as TurnMetrics above. A caller builds one via TurnTimingsCallback (the
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


class TurnTimingsCallback(AsyncCallbackHandler):
    """Fresh one per turn, passed via graph.ainvoke's config={"callbacks": [...]}.
    LangGraph tags every node's LLM/tool runs with metadata["langgraph_node"] -- that's
    how an LLM call gets bucketed into classify_ms/respond_ms/decline_ms without graph.py
    needing to know this exists. Node code is never touched (docs/voice-stage-timing.md's
    hard constraint): this only observes start/end events LangChain already emits.

    Keyed by run_id rather than assuming one call at a time, since respond<->tools can
    round-trip more than once in a single turn and each round trip gets its own run_id."""

    def __init__(self) -> None:
        self.timings = TurnTimings()
        self._llm_start: dict[uuid.UUID, float] = {}
        self._llm_node: dict[uuid.UUID, str] = {}
        self._tool_start: dict[uuid.UUID, float] = {}
        self._tool_name: dict[uuid.UUID, str] = {}

    async def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs,
    ) -> None:
        self._llm_start[run_id] = time.monotonic()
        self._llm_node[run_id] = (metadata or {}).get("langgraph_node", "unknown")

    async def on_llm_end(self, response, *, run_id, parent_run_id=None, tags=None, **kwargs) -> None:
        start = self._llm_start.pop(run_id, None)
        node = self._llm_node.pop(run_id, None)
        if start is None:
            return
        duration_ms = (time.monotonic() - start) * 1000
        if node == "classify_topic":
            self.timings.classify_ms = duration_ms
        elif node == "respond":
            self.timings.respond_ms.append(duration_ms)
        elif node == "decline":
            self.timings.decline_ms = duration_ms

    async def on_llm_error(self, error, *, run_id, parent_run_id=None, tags=None, **kwargs) -> None:
        self._llm_start.pop(run_id, None)
        self._llm_node.pop(run_id, None)

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


def _default_timing_log_path() -> Path:
    # User's app data, not the repo -- this is a local debug/tuning log (per-pilot
    # machine, per-pilot timings), never sent to the server (docs/voice-stage-timing.md).
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "ALICE" / "turn_timings.jsonl"


def log_turn_timings(timings: TurnTimings) -> None:
    """Prints the compact [ALICE]-style summary and best-effort appends one JSON line
    to a local file (path overridable via ALICE_TIMING_LOG_PATH) -- a write failure here
    must never break a real voice turn, same principle as prewarm's per-target isolation."""
    print(f"[ALICE] timings: {timings.summary_line()}")

    path = Path(os.getenv("ALICE_TIMING_LOG_PATH", str(_default_timing_log_path())))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(timings.model_dump_json() + "\n")
    except OSError as exc:
        print(f"[ALICE] couldn't write timing log ({exc}) -- continuing without it")
