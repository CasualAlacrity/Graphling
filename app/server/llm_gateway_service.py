"""The LLM gateway (docs/llm-gateway.md) -- every pilot's model calls go through here, so
no client ever holds a provider key or reaches frankencompute directly.

Speaks Ollama's native /api/chat format, not OpenAI's, on purpose: Ollama's
OpenAI-compatible endpoint silently drops gemma4's thinking (verified 2026-10-09:
reasoning_effort=high still produced zero reasoning tokens), and the evals showed
reasoning helps the default model. So the client keeps using ChatOllama exactly as
evaluated, just pointed at this server, and the frankencompute path is a pass-through.
Only the OpenAI fallback translates.

Routing, per request:
1. The client names a ROLE (alice-chat / alice-classify), never a real model -- the
   server picks the model, the thinking flag, and the fallback, so they can change
   without a client release.
2. frankencompute first, unless it's known to be down or already has llm_max_inflight
   requests running ("overflow").
3. Fall back to OpenAI only when frankencompute can't answer: connect error, timeout,
   or 5xx. Never because an answer looks bad, and never after any bytes have reached
   the client (non-streamed fallback is what makes this clean).
4. Every call writes one llm_usage row.

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

import httpx

from db.models import LlmUsage
from db.session import SessionLocal
from model_pricing import MODEL_PRICING
from server.config import settings

log = logging.getLogger(__name__)

FRANKENCOMPUTE = "frankencompute"
OPENAI = "openai"
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
# How long a failed request benches frankencompute before it's tried again. The probe
# (probe_forever) can clear it sooner.
DOWN_FOR_S = 30.0
PROBE_INTERVAL_S = 15.0


def _http_client(**kwargs) -> httpx.AsyncClient:
    """Every outbound client is built here -- the one seam tests swap for an
    httpx.MockTransport instead of reaching a real box or OpenAI."""
    return httpx.AsyncClient(**kwargs)


class UnknownRole(ValueError):
    pass


class NoBackendAvailable(RuntimeError):
    pass


class FrankencomputeFailed(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Role:
    frankencompute_model: str
    think: bool
    fallback_model: str


def roles() -> dict[str, Role]:
    # Thinking on for chat, off for classify -- the same split as the client's own
    # OLLAMA_REASONING / OLLAMA_CLASSIFICATION_REASONING defaults (app/llm.py).
    return {
        "alice-chat": Role(settings.frankencompute_chat_model, True, settings.fallback_chat_model),
        "alice-classify": Role(settings.frankencompute_classify_model, False, settings.fallback_classify_model),
    }


class FrankencomputeState:
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


state = FrankencomputeState()


def skip_frankencompute_reason(now: float) -> str | None:
    """Why this request shouldn't even try frankencompute, or None to try it."""
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


async def open_chat(payload: dict, user_id: uuid.UUID) -> Relay:
    role_name = payload.get("model")
    role = roles().get(role_name)
    if role is None:
        raise UnknownRole(f"Unknown model role {role_name!r}; expected one of {sorted(roles())}")

    started = time.monotonic()
    fallback_reason = skip_frankencompute_reason(started)
    if fallback_reason is None:
        try:
            return await _open_frankencompute(payload, role_name, role, user_id, started)
        except FrankencomputeFailed as failure:
            fallback_reason = failure.reason
            state.mark_down(failure.reason, time.monotonic() + DOWN_FOR_S)
            log.warning("frankencompute failed (%s); falling back", failure.reason)

    if not settings.openai_api_key:
        raise NoBackendAvailable(f"frankencompute unavailable ({fallback_reason}) and no fallback is configured")
    return await _relay_openai(payload, role_name, role, user_id, started, fallback_reason)


# ---- frankencompute (pass-through) ----

def frankencompute_payload(payload: dict, role: Role) -> dict:
    """The client's request with the role resolved and anything box-wide stripped."""
    forwarded = {key: value for key, value in payload.items() if key not in ("keep_alive", "options")}
    forwarded["model"] = role.frankencompute_model
    forwarded["think"] = role.think
    options = payload.get("options") or {}
    forwarded["options"] = {key: value for key, value in options.items() if key in ALLOWED_OPTIONS}
    return forwarded


async def _open_frankencompute(
    payload: dict, role_name: str, role: Role, user_id: uuid.UUID, started: float,
) -> Relay:
    client = _http_client(
        base_url=settings.frankencompute_url,
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
    )
    request = client.build_request("POST", "/api/chat", json=frankencompute_payload(payload, role))
    try:
        response = await client.send(request, stream=True)
    except httpx.TimeoutException as exc:
        await client.aclose()
        raise FrankencomputeFailed("timeout") from exc
    except httpx.TransportError as exc:
        await client.aclose()
        raise FrankencomputeFailed("down") from exc

    if response.status_code >= 500:
        await response.aclose()
        await client.aclose()
        raise FrankencomputeFailed("5xx")

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
                    chunk["alice_backend"] = FRANKENCOMPUTE
                    final = chunk
                yield (json.dumps(chunk) + "\n").encode()
        except httpx.HTTPError:
            # Mid-stream failure -- too late to fall back, the client already has
            # bytes. Record it and let the client see a truncated response.
            status = "error"
            log.warning("frankencompute stream broke mid-response")
        finally:
            state.inflight -= 1
            await response.aclose()
            await client.aclose()
            await record_usage(
                user_id=user_id, role=role_name, backend=FRANKENCOMPUTE, model=role.frankencompute_model,
                prompt_tokens=final.get("prompt_eval_count", 0), cache_read_tokens=0,
                completion_tokens=final.get("eval_count", 0),
                latency_ms=(time.monotonic() - started) * 1000, status=status,
                fallback_reason=None, cost_usd=None,
            )

    media_type = response.headers.get("content-type", "application/x-ndjson")
    return Relay(status_code=response.status_code, media_type=media_type, body=body())


# ---- OpenAI fallback (translated) ----

def openai_request(payload: dict, model: str) -> dict:
    """Ollama /api/chat request -> OpenAI chat-completions request. ChatOllama already
    sends tools in OpenAI's shape and gives every tool call an id (echoed back as the
    tool message's tool_call_id), so the pairing OpenAI insists on survives as-is."""
    messages = []
    for message in payload.get("messages", []):
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


async def _relay_openai(
    payload: dict, role_name: str, role: Role, user_id: uuid.UUID, started: float, fallback_reason: str,
) -> Relay:
    model = role.fallback_model
    try:
        async with _http_client(timeout=OPENAI_TIMEOUT_S) as client:
            response = await client.post(
                OPENAI_CHAT_URL,
                json=openai_request(payload, model),
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
    except httpx.HTTPError as exc:
        await _record_fallback_error(user_id, role_name, model, started, fallback_reason)
        raise NoBackendAvailable(f"fallback request failed: {exc!r}") from exc

    if response.status_code != 200:
        await _record_fallback_error(user_id, role_name, model, started, fallback_reason)
        error_line = json.dumps({"error": f"fallback model returned {response.status_code}: {response.text[:500]}"})
        return Relay(status_code=502, media_type="application/json", body=_one_line(error_line))

    openai_response = response.json()
    usage = openai_response.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    cache_read_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    await record_usage(
        user_id=user_id, role=role_name, backend=OPENAI, model=model,
        prompt_tokens=prompt_tokens, cache_read_tokens=cache_read_tokens, completion_tokens=completion_tokens,
        latency_ms=(time.monotonic() - started) * 1000, status="ok", fallback_reason=fallback_reason,
        cost_usd=openai_cost_usd(model, prompt_tokens, cache_read_tokens, completion_tokens),
    )
    return Relay(
        status_code=200,
        media_type="application/x-ndjson",
        body=_one_line(json.dumps(ollama_final_chunk(openai_response, model))),
    )


async def _one_line(line: str) -> AsyncIterator[bytes]:
    yield (line + "\n").encode()


async def _record_fallback_error(
    user_id: uuid.UUID, role_name: str, model: str, started: float, fallback_reason: str,
) -> None:
    await record_usage(
        user_id=user_id, role=role_name, backend=OPENAI, model=model, prompt_tokens=0, cache_read_tokens=0,
        completion_tokens=0, latency_ms=(time.monotonic() - started) * 1000, status="error",
        fallback_reason=fallback_reason, cost_usd=None,
    )


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
    """Marks frankencompute up or down from its own /api/tags, including the digest pin."""
    try:
        async with _http_client(base_url=settings.frankencompute_url, timeout=5.0) as client:
            response = await client.get("/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
    except httpx.HTTPError:
        state.mark_down("down", math.inf)
        return

    if settings.frankencompute_model_digest:
        # The pin applies to the chat model, the one the evals measured. A different
        # classify model would need its own pin (not needed while both are gemma4).
        wanted = settings.frankencompute_chat_model
        wanted_tag = wanted if ":" in wanted else f"{wanted}:latest"
        digest = ""
        for model in models:
            if model.get("name") == wanted_tag:
                digest = model.get("digest", "")
        if not digest.startswith(settings.frankencompute_model_digest):
            log.error("frankencompute %s digest %r doesn't match pinned %r -- routing to fallback",
                      wanted_tag, digest[:12], settings.frankencompute_model_digest)
            state.mark_down("digest_mismatch", math.inf)
            return

    state.mark_up()


async def probe_forever() -> None:
    while True:
        await probe_once()
        await asyncio.sleep(PROBE_INTERVAL_S)
