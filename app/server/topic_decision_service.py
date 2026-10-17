"""/llm/decide/topic: request, social or off-topic, asked of the decision model on heket
(settings.heket_topic_model, Tev1). The question wording and confidence floors live in
app/topic_decision.py, shared with the client's local-development path.

No hosted fallback: nothing hosted answers this endpoint's shape. When heket is down or
the call fails, the route answers 503 and the client falls back to its alice-classify
chat-model classifier, which goes through the normal gateway (and its own fallback).
A Tev1 failure doesn't mark heket down: it can be Tev1-specific (not pulled), while chat
on heket still works.

Warming is separate from the chat warm-up (llm_gateway_service.probe_once) for the same
reason: that one benches heket until its models are loaded, and a Tev1 that can't load
must never take chat down with it. Tev1 loads in ~9 s, past TIMEOUT_S, so without its own
warm-up a decision request would time out and cancel the load every time.
"""
import asyncio
import logging
import time
import uuid

import httpx

import topic_decision
from model_pricing import local_energy_cost_usd
from server import llm_gateway_service as gateway
from server.config import settings

log = logging.getLogger(__name__)

ROLE = "alice-topic"
# A decision takes ~30 ms once loaded and ~9 s to load cold; past this the client's
# fallback answers faster than waiting would.
TIMEOUT_S = 3.0
WARMUP_TIMEOUT_S = 120.0
WARM_CHECK_INTERVAL_S = 15.0


class TopicDecisionUnavailable(RuntimeError):
    pass


async def decide(utterance: str, user_id: uuid.UUID, with_groups: bool = False) -> dict:
    """{"kind": request|social|off_topic, "choice": Tev1's raw pick, "confidence": ...}, plus
    "tool_groups" (a list, or None for every tool) when with_groups: the tool-routing
    experiment (topic_decision.GROUP_QUESTION), asked in the same call."""
    model = settings.heket_topic_model
    if not model:
        raise TopicDecisionUnavailable("no topic model configured")
    started = time.monotonic()
    reason = gateway.skip_heket_reason(started)
    if reason is not None:
        raise TopicDecisionUnavailable(f"heket unavailable ({reason})")

    status = "error"
    usage = {}
    try:
        async with gateway._http_client(base_url=settings.heket_url, timeout=TIMEOUT_S) as client:
            if with_groups:
                body = topic_decision.request_body_with_groups(model, utterance)
            else:
                body = topic_decision.request_body(model, utterance)
            response = await client.post("/v1/systemone", json=body)
        if response.status_code >= 400:
            raise TopicDecisionUnavailable(f"heket answered {response.status_code}")
        body = response.json()
        usage = body.get("usage") or {}
        choice, confidence = topic_decision.read_answer(body)
        group_probabilities = topic_decision.read_group_probabilities(body) if with_groups else {}
        status = "ok"
    except httpx.HTTPError as exc:
        raise TopicDecisionUnavailable(f"heket unreachable ({type(exc).__name__})") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise TopicDecisionUnavailable("unexpected answer from the topic model") from exc
    finally:
        latency_ms = (time.monotonic() - started) * 1000
        await gateway.record_usage(
            user_id=user_id, role=ROLE, backend=gateway.HEKET, model=model,
            prompt_tokens=usage.get("input_tokens", 0), cache_read_tokens=0, completion_tokens=0,
            latency_ms=latency_ms, status=status, fallback_reason=None,
            cost_usd=local_energy_cost_usd(latency_ms), inflight_at_start=gateway.state.inflight,
        )

    result = {
        "kind": topic_decision.apply_floors(choice, confidence),
        "choice": choice,
        "confidence": confidence,
    }
    if with_groups:
        result["tool_groups"] = topic_decision.chosen_groups(group_probabilities)
    return result


async def warm_if_cold() -> None:
    """Loads the topic model on heket if it isn't loaded and heket is otherwise up. Owned
    by the server, so no pilot request's timeout can cancel the load; never benches heket."""
    model = settings.heket_topic_model
    if not model or gateway.skip_heket_reason(time.monotonic()) is not None:
        return
    tag = model if ":" in model else f"{model}:latest"
    try:
        async with gateway._http_client(base_url=settings.heket_url, timeout=5.0) as client:
            response = await client.get("/api/ps")
            response.raise_for_status()
            loaded = set()
            for running in response.json().get("models", []):
                loaded.add(running.get("name"))
                loaded.add(running.get("model"))
        if tag in loaded:
            return
        log.info("warming %s on heket", tag)
        started = time.monotonic()
        async with gateway._http_client(base_url=settings.heket_url, timeout=WARMUP_TIMEOUT_S) as client:
            response = await client.post("/v1/systemone", json=topic_decision.request_body(model, "Good morning."))
            response.raise_for_status()
        log.info("%s warm on heket after %.1f s", tag, time.monotonic() - started)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("warming %s on heket failed: %r", tag, exc)


async def warm_forever() -> None:
    while True:
        await warm_if_cold()
        await asyncio.sleep(WARM_CHECK_INTERVAL_S)
