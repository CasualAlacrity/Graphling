"""The LLM gateway (docs/llm-gateway.md) -- every pilot's model calls go through here, so
no client ever holds a provider key or reaches heket directly.

Speaks Ollama's native /api/chat format, not OpenAI's, on purpose: Ollama's
OpenAI-compatible endpoint silently drops gemma4's thinking (verified 2026-10-09:
reasoning_effort=high still produced zero reasoning tokens), and the evals showed
reasoning helps the default model. So the client keeps using ChatOllama exactly as
evaluated, just pointed at this server, and the heket path is a pass-through.
Only the fallback translates (OpenAI here, Anthropic in server/anthropic_fallback.py).

Routing, per request:
1. The client names a ROLE (alice-chat / alice-classify), never a real model -- the
   server picks the model, the thinking flag, and the fallback, so they can change
   without a client release.
2. heket first, unless it's known to be down or already has llm_max_inflight
   requests running ("overflow").
3. Fall back (settings.fallback_provider: Anthropic or OpenAI) only when heket
   can't answer: connect error, timeout, or 5xx. Never because an answer looks bad,
   and never after any bytes have reached the client (non-streamed fallback is what
   makes this clean). Once the day's hosted spend passes fallback_daily_usd_cap, an
   overflow queues on the box instead, and an outage gets a 503.
4. Every call writes one llm_usage row, including how many requests were already running
   on heket when it arrived (real concurrency, for hardware sizing).

In-memory state (the down flag, the in-flight count) assumes one server process, the
same assumption server/routes/auth.py's _PENDING_LOGINS already makes.
"""
import asyncio
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import anthropic
import httpx
from sqlalchemy import func, select

from db.models import LlmUsage
from db.session import SessionLocal
from model_pricing import MODEL_PRICING, local_energy_cost_usd
from server import anthropic_fallback
from server.config import settings
from server.tool_call_ids import with_tool_call_ids

log = logging.getLogger(__name__)

HEKET = "heket"
OPENAI = "openai"
ANTHROPIC = anthropic_fallback.ANTHROPIC

# Used when fallback_chat_model / fallback_classify_model are left empty. Haiku 5.5 over
# 4.5, measured 2026-10-09 (3 cases x 5 samples through the gateway): 45/45 correct tool
# and args vs 33/45 (4.5 asked a clarifying question instead of looking prices up in
# 12 turns), at ~$0.0002 vs ~$0.0016 per call. Costs ~1.9 s more per turn than
# heket (it thinks by default) -- fine for a fallback.
DEFAULT_FALLBACK_MODELS = {ANTHROPIC: "claude-haiku-5-5", OPENAI: "gpt-4o-mini"}
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Ollama options a client may set. Everything else (num_ctx, num_gpu, keep_alive, ...)
# is the box's business: one pilot sending num_ctx=131072 or keep_alive=0 would evict
# or unload the model for every other pilot.
ALLOWED_OPTIONS = {"temperature", "top_p", "top_k", "seed", "stop", "num_predict", "repeat_penalty"}

CONNECT_TIMEOUT_S = 2.0
# Max silence between streamed chunks, not total time -- a thinking model streams
# continuously. Eval p90 for a whole turn was 5.1 s, max 15.4 s (2026-10-09).
READ_TIMEOUT_S = 30.0
OPENAI_TIMEOUT_S = 60.0
# How long a failed request benches heket before it's tried again. The probe
# (probe_forever) can clear it sooner.
DOWN_FOR_S = 30.0
PROBE_INTERVAL_S = 15.0
# How long the server's own warm-up may take to load a role's model. A cold gemma4 took
# ~35 s after an Ollama restart (2026-10-16: ~21 s load plus a slow first prompt), more
# than READ_TIMEOUT_S -- so a pilot request can't do the load itself: it times out, and
# the cancel kills the half-done load ("llama-server terminated signal: killed"), so
# every retry started from zero and the box never warmed up.
WARMUP_TIMEOUT_S = 300.0


def _http_client(**kwargs) -> httpx.AsyncClient:
    """Every outbound client is built here -- the one seam tests swap for an
    httpx.MockTransport instead of reaching a real box or OpenAI."""
    return httpx.AsyncClient(**kwargs)


class UnknownRole(ValueError):
    pass


class NoBackendAvailable(RuntimeError):
    pass


class HeketFailed(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Role:
    heket_model: str
    think: bool
    fallback_model: str


def roles() -> dict[str, Role]:
    # Thinking on for chat, off for classify -- the same split as the client's own
    # OLLAMA_REASONING / OLLAMA_CLASSIFICATION_REASONING defaults (app/llm.py).
    default_fallback = DEFAULT_FALLBACK_MODELS.get(settings.fallback_provider, "")
    return {
        "alice-chat": Role(
            settings.heket_chat_model, True, settings.fallback_chat_model or default_fallback,
        ),
        "alice-classify": Role(
            settings.heket_classify_model, False, settings.fallback_classify_model or default_fallback,
        ),
        # Short spoken lines in ALICE's voice (graph.py's decline and social reply): a chat
        # model, not the classifier, so the classifier can be swapped for a pure decision
        # model. The chat model with thinking off until a smaller one is picked.
        "alice-chat-small": Role(
            settings.heket_small_chat_model or settings.heket_chat_model,
            False,
            settings.fallback_small_chat_model or settings.fallback_chat_model or default_fallback,
        ),
    }


def fallback_configured() -> bool:
    if settings.fallback_provider == ANTHROPIC:
        return bool(settings.anthropic_api_key)
    if settings.fallback_provider == OPENAI:
        return bool(settings.openai_api_key)
    return False


class HeketState:
    def __init__(self) -> None:
        self.down_until = 0.0
        self.down_reason: str | None = None
        self.inflight = 0

    def mark_down(self, reason: str, until: float) -> None:
        self.down_until = until
        self.down_reason = reason

    def mark_up(self) -> None:
        self.down_until = 0.0
        self.down_reason = None


state = HeketState()


def skip_heket_reason(now: float) -> str | None:
    """Why this request shouldn't even try heket, or None to try it."""
    if now < state.down_until:
        return state.down_reason or "down"
    if settings.llm_max_inflight and state.inflight >= settings.llm_max_inflight:
        return "overflow"
    return None


@dataclass
class Relay:
    status_code: int
    media_type: str
    body: AsyncIterator[bytes]


@dataclass(frozen=True)
class CallContext:
    """What a fallback relay needs to answer and to write its llm_usage row."""
    role_name: str
    role: Role
    user_id: uuid.UUID
    started: float
    fallback_reason: str
    inflight_at_start: int


async def open_chat(payload: dict, user_id: uuid.UUID) -> Relay:
    role_name = payload.get("model")
    role = roles().get(role_name)
    if role is None:
        raise UnknownRole(f"Unknown model role {role_name!r}; expected one of {sorted(roles())}")

    started = time.monotonic()
    inflight_at_start = state.inflight
    fallback_reason = skip_heket_reason(started)
    if fallback_reason == "overflow" and await fallback_budget_exhausted():
        # Busy, not broken: with the day's budget spent, wait for a slot on the box
        # rather than pay for the call.
        fallback_reason = None
    if fallback_reason is None:
        try:
            return await _open_heket(payload, role_name, role, user_id, started, inflight_at_start)
        except HeketFailed as failure:
            fallback_reason = failure.reason
            state.mark_down(failure.reason, time.monotonic() + DOWN_FOR_S)
            log.warning("heket failed (%s); falling back", failure.reason)

    if not fallback_configured():
        raise NoBackendAvailable(f"heket unavailable ({fallback_reason}) and no fallback is configured")
    if await fallback_budget_exhausted():
        log.error("heket unavailable (%s) and today's fallback budget ($%.2f) is spent",
                  fallback_reason, settings.fallback_daily_usd_cap)
        raise NoBackendAvailable(
            f"heket unavailable ({fallback_reason}) and today's fallback budget is spent -- try again shortly"
        )
    context = CallContext(role_name, role, user_id, started, fallback_reason, inflight_at_start)
    if settings.fallback_provider == ANTHROPIC:
        return await _relay_anthropic(payload, context)
    return await _relay_openai(payload, context)


# ---- heket (pass-through) ----

def heket_payload(payload: dict, role: Role) -> dict:
    """The client's request with the role resolved and anything box-wide stripped."""
    forwarded = {key: value for key, value in payload.items() if key not in ("keep_alive", "options")}
    forwarded["model"] = role.heket_model
    forwarded["think"] = role.think
    options = payload.get("options") or {}
    forwarded["options"] = {key: value for key, value in options.items() if key in ALLOWED_OPTIONS}
    return forwarded


async def _open_heket(
    payload: dict, role_name: str, role: Role, user_id: uuid.UUID, started: float, inflight_at_start: int,
) -> Relay:
    client = _http_client(
        base_url=settings.heket_url,
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )
    request = client.build_request("POST", "/api/chat", json=heket_payload(payload, role))
    try:
        response = await client.send(request, stream=True)
    except httpx.TimeoutException as exc:
        await client.aclose()
        raise HeketFailed("timeout") from exc
    except httpx.TransportError as exc:
        await client.aclose()
        raise HeketFailed("down") from exc

    if response.status_code >= 500:
        await response.aclose()
        await client.aclose()
        raise HeketFailed("5xx")

    # Counted from here, not from the try above: a request only occupies the box once
    # it's actually been accepted.
    state.inflight += 1

    async def body() -> AsyncIterator[bytes]:
        final: dict = {}
        status = "ok" if response.status_code < 400 else "error"
        try:
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    yield (line + "\n").encode()
                    continue
                if chunk.get("done"):
                    # Lets the client's turn_metrics tell which backend answered
                    # (ChatOllama copies the final chunk's fields into response_metadata).
                    chunk["alice_backend"] = HEKET
                    final = chunk
                yield (json.dumps(chunk) + "\n").encode()
        except httpx.HTTPError:
            # Mid-stream failure -- too late to fall back, the client already has
            # bytes. Record it and let the client see a truncated response.
            status = "error"
            log.warning("heket stream broke mid-response")
        finally:
            state.inflight -= 1
            await response.aclose()
            await client.aclose()
            latency_ms = (time.monotonic() - started) * 1000
            await record_usage(
                user_id=user_id, role=role_name, backend=HEKET, model=role.heket_model,
                prompt_tokens=final.get("prompt_eval_count", 0), cache_read_tokens=0,
                completion_tokens=final.get("eval_count", 0),
                latency_ms=latency_ms, status=status, fallback_reason=None,
                # A failed call still kept the GPU busy, so it's priced too.
                cost_usd=local_energy_cost_usd(latency_ms), inflight_at_start=inflight_at_start,
            )

    media_type = response.headers.get("content-type", "application/x-ndjson")
    return Relay(status_code=response.status_code, media_type=media_type, body=body())


# ---- OpenAI fallback (translated) ----

def openai_request(payload: dict, model: str) -> dict:
    """Ollama /api/chat request -> OpenAI chat-completions request. ChatOllama already
    sends tools in OpenAI's shape; tool calls and results are paired by position
    (server/tool_call_ids.py -- the ollama client strips ids, so none arrive)."""
    messages = []
    for message in with_tool_call_ids(payload.get("messages", [])):
        role = message.get("role")
        content = message.get("content") or ""
        if role == "tool":
            messages.append({"role": "tool", "tool_call_id": message.get("tool_call_id", ""), "content": content})
        elif role == "assistant" and message.get("tool_calls"):
            tool_calls = []
            for index, tool_call in enumerate(message["tool_calls"]):
                arguments = tool_call["function"].get("arguments", {})
                tool_calls.append({
                    "id": tool_call.get("id") or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": tool_call["function"]["name"],
                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                    },
                })
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        else:
            # "thinking" and "images" are dropped -- the fallback model never sees the
            # primary model's reasoning, and ALICE sends no images.
            messages.append({"role": role, "content": content})

    request: dict = {"model": model, "messages": messages}
    if payload.get("tools"):
        request["tools"] = payload["tools"]

    response_format = payload.get("format")
    if isinstance(response_format, dict):
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "structured_output", "schema": response_format},
        }
    elif response_format == "json":
        request["response_format"] = {"type": "json_object"}

    options = payload.get("options") or {}
    for name in ("temperature", "top_p", "seed", "stop"):
        if options.get(name) is not None:
            request[name] = options[name]
    if options.get("num_predict") is not None:
        request["max_tokens"] = options["num_predict"]
    return request


def ollama_final_chunk(openai_response: dict, model: str) -> dict:
    """OpenAI chat-completions response -> one Ollama-shaped done:true chunk."""
    choice = openai_response["choices"][0]["message"]
    message: dict = {"role": "assistant", "content": choice.get("content") or ""}
    if choice.get("tool_calls"):
        message["tool_calls"] = []
        for tool_call in choice["tool_calls"]:
            raw_arguments = tool_call["function"].get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = raw_arguments
            message["tool_calls"].append({"function": {"name": tool_call["function"]["name"], "arguments": arguments}})

    usage = openai_response.get("usage") or {}
    return {
        "model": model,
        "created_at": datetime.now(UTC).isoformat(),
        "message": message,
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": usage.get("prompt_tokens", 0),
        "eval_count": usage.get("completion_tokens", 0),
        "alice_backend": OPENAI,
    }


def openai_cost_usd(model: str, prompt_tokens: int, cache_read_tokens: int, completion_tokens: int) -> float | None:
    rates = MODEL_PRICING.get((OPENAI, model))
    if rates is None:
        return None
    uncached = prompt_tokens - cache_read_tokens
    dollars = uncached * rates.input + cache_read_tokens * rates.cache_read + completion_tokens * rates.output
    return dollars / 1_000_000


async def _relay_openai(payload: dict, context: CallContext) -> Relay:
    model = context.role.fallback_model
    try:
        async with _http_client(timeout=OPENAI_TIMEOUT_S) as client:
            response = await client.post(
                OPENAI_CHAT_URL,
                json=openai_request(payload, model),
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
    except httpx.HTTPError as exc:
        await _record_fallback_error(context, OPENAI, model)
        raise NoBackendAvailable(f"fallback request failed: {exc!r}") from exc

    if response.status_code != 200:
        await _record_fallback_error(context, OPENAI, model)
        error_line = json.dumps({"error": f"fallback model returned {response.status_code}: {response.text[:500]}"})
        return Relay(status_code=502, media_type="application/json", body=_one_line(error_line))

    openai_response = response.json()
    usage = openai_response.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    cache_read_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    await record_usage(
        user_id=context.user_id, role=context.role_name, backend=OPENAI, model=model,
        prompt_tokens=prompt_tokens, cache_read_tokens=cache_read_tokens, completion_tokens=completion_tokens,
        latency_ms=(time.monotonic() - context.started) * 1000, status="ok",
        fallback_reason=context.fallback_reason,
        cost_usd=openai_cost_usd(model, prompt_tokens, cache_read_tokens, completion_tokens),
        inflight_at_start=context.inflight_at_start,
    )
    return Relay(
        status_code=200,
        media_type="application/x-ndjson",
        body=_one_line(json.dumps(ollama_final_chunk(openai_response, model))),
    )


async def _relay_anthropic(payload: dict, context: CallContext) -> Relay:
    model = context.role.fallback_model
    try:
        message = await anthropic_fallback.create(payload, model)
    except anthropic.APIStatusError as exc:
        await _record_fallback_error(context, ANTHROPIC, model)
        error_line = json.dumps({"error": f"fallback model returned {exc.status_code}: {exc.message[:500]}"})
        return Relay(status_code=502, media_type="application/json", body=_one_line(error_line))
    except anthropic.APIConnectionError as exc:
        await _record_fallback_error(context, ANTHROPIC, model)
        raise NoBackendAvailable(f"fallback request failed: {exc!r}") from exc

    usage = message.usage
    chunk = anthropic_fallback.ollama_final_chunk(message, model)
    await record_usage(
        user_id=context.user_id, role=context.role_name, backend=ANTHROPIC, model=model,
        prompt_tokens=chunk["prompt_eval_count"], cache_read_tokens=usage.cache_read_input_tokens or 0,
        completion_tokens=usage.output_tokens, latency_ms=(time.monotonic() - context.started) * 1000,
        status="ok", fallback_reason=context.fallback_reason, cost_usd=anthropic_fallback.cost_usd(model, usage),
        inflight_at_start=context.inflight_at_start,
    )
    return Relay(status_code=200, media_type="application/x-ndjson", body=_one_line(json.dumps(chunk)))


async def _one_line(line: str) -> AsyncIterator[bytes]:
    yield (line + "\n").encode()


async def _record_fallback_error(context: CallContext, backend: str, model: str) -> None:
    await record_usage(
        user_id=context.user_id, role=context.role_name, backend=backend, model=model, prompt_tokens=0,
        cache_read_tokens=0, completion_tokens=0, latency_ms=(time.monotonic() - context.started) * 1000,
        status="error", fallback_reason=context.fallback_reason, cost_usd=None,
        inflight_at_start=context.inflight_at_start,
    )


# ---- fallback budget ----

async def fallback_budget_exhausted() -> bool:
    cap = settings.fallback_daily_usd_cap
    if not cap:
        return False
    return await fallback_spend_today_usd() >= cap


async def fallback_spend_today_usd() -> float:
    """Hosted spend since UTC midnight, across every pilot. Read from llm_usage (not kept
    in memory) so a restart can't reset the budget. Fails open: if the database can't
    answer, the gateway has bigger problems than a budget, and blocking every fallback
    on top of them would turn a DB hiccup into an outage."""
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(
                    LlmUsage.backend != HEKET, LlmUsage.created_at >= midnight,
                )
            )
            return float(result.scalar_one())
    except Exception:
        log.exception("couldn't read today's fallback spend; not enforcing the cap for this call")
        return 0.0


# ---- usage log ----

async def record_usage(**fields) -> None:
    """Best-effort: a failed usage write is logged, never turned into a failed pilot turn."""
    try:
        async with SessionLocal() as session:
            session.add(LlmUsage(**fields))
            await session.commit()
    except Exception:
        log.exception("failed to record llm_usage row")


# ---- health probe ----

async def probe_once() -> None:
    """Marks heket up or down from its own /api/tags, including the digest pin, and
    keeps it benched as "warming" while a role's model isn't loaded (/api/ps)."""
    try:
        async with _http_client(base_url=settings.heket_url, timeout=5.0) as client:
            response = await client.get("/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
    except httpx.HTTPError:
        state.mark_down("down", math.inf)
        return

    if settings.heket_model_digest:
        # The pin applies to the chat model, the one the evals measured. A different
        # classify or small-chat model would need its own pin (not needed while all three
        # are gemma4).
        wanted = settings.heket_chat_model
        wanted_tag = wanted if ":" in wanted else f"{wanted}:latest"
        digest = ""
        for model in models:
            if model.get("name") == wanted_tag:
                digest = model.get("digest", "")
        if not digest.startswith(settings.heket_model_digest):
            log.error("heket %s digest %r doesn't match pinned %r -- routing to fallback",
                      wanted_tag, digest[:12], settings.heket_model_digest)
            state.mark_down("digest_mismatch", math.inf)
            return

    cold = await cold_models()
    if cold:
        # Up, but a pilot request would have to wait for the load. Keep pilots on the
        # fallback until the server's own warm-up has loaded every role's model; the
        # next probe after it finishes marks the box up.
        state.mark_down("warming", math.inf)
        start_warmup(cold)
        return

    state.mark_up()


def _tagged(model: str) -> str:
    return model if ":" in model else f"{model}:latest"


async def cold_models() -> list[str]:
    """The roles' heket models that Ollama doesn't have loaded right now (/api/ps).
    Empty if /api/ps can't be read -- the box answered /api/tags, so don't bench it
    over a check that only exists to save pilots a slow first turn."""
    try:
        async with _http_client(base_url=settings.heket_url, timeout=5.0) as client:
            response = await client.get("/api/ps")
            response.raise_for_status()
            running = response.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        log.warning("couldn't read heket's loaded models; assuming they're warm")
        return []

    loaded = set()
    for model in running:
        loaded.add(model.get("name"))
        loaded.add(model.get("model"))

    cold = []
    for role in roles().values():
        tag = _tagged(role.heket_model)
        if tag not in loaded and tag not in cold:
            cold.append(tag)
    return cold


_warmup_task: asyncio.Task | None = None


def start_warmup(models: list[str]) -> None:
    """Loads models on heket in a background task, unless a warm-up is already running.
    The task belongs to the server, not to any pilot request, so nothing a pilot does
    (timing out, hanging up) can cancel the load halfway."""
    global _warmup_task
    if _warmup_task is not None and not _warmup_task.done():
        return
    _warmup_task = asyncio.create_task(warm_up(models))


async def warm_up(models: list[str]) -> None:
    """One tiny chat per model: loads it, and runs a first prompt through it too, since
    the first prompt after a load was also slow. No keep_alive is sent, so the box's
    own OLLAMA_KEEP_ALIVE decides how long the model stays."""
    timeout = httpx.Timeout(WARMUP_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    async with _http_client(base_url=settings.heket_url, timeout=timeout) as client:
        for model in models:
            log.info("warming %s on heket", model)
            started = time.monotonic()
            try:
                response = await client.post("/api/chat", json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                    "think": False,
                    "options": {"num_predict": 1},
                })
                response.raise_for_status()
            except httpx.HTTPError as exc:
                # The probe keeps heket benched and tries again on its next round.
                log.warning("warming %s on heket failed: %r", model, exc)
                return
            log.info("%s warm on heket after %.1f s", model, time.monotonic() - started)


async def probe_forever() -> None:
    try:
        while True:
            await probe_once()
            await asyncio.sleep(PROBE_INTERVAL_S)
    finally:
        if _warmup_task is not None:
            _warmup_task.cancel()
