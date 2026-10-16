"""/llm/decide/topic: request, social or off-topic, asked of the decision model on heket
(settings.heket_topic_model, Tev1). The question wording and confidence floors live in
app/topic_decision.py, shared with the client's local-development path.

No hosted fallback: nothing hosted answers this endpoint's shape. When heket is down or
the call fails, the route answers 503 and the client falls back to its alice-classify
chat-model classifier, which goes through the normal gateway (and its own fallback).
A Tev1 failure doesn't mark heket down: it can be Tev1-specific (not pulled), while chat
on heket still works.
"""
import time
import uuid

import httpx

import topic_decision
from model_pricing import local_energy_cost_usd
from server import llm_gateway_service as gateway
from server.config import settings

ROLE = "alice-topic"
# A decision takes ~30 ms once loaded and ~9 s to load cold; past this the client's
# fallback answers faster than waiting would.
TIMEOUT_S = 3.0


class TopicDecisionUnavailable(RuntimeError):
    pass


async def decide(utterance: str, user_id: uuid.UUID) -> dict:
    """{"kind": request|social|off_topic, "choice": Tev1's raw pick, "confidence": ...}."""
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
            response = await client.post("/v1/systemone", json=topic_decision.request_body(model, utterance))
        if response.status_code >= 400:
            raise TopicDecisionUnavailable(f"heket answered {response.status_code}")
        body = response.json()
        usage = body.get("usage") or {}
        choice, confidence = topic_decision.read_answer(body)
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

    return {
        "kind": topic_decision.apply_floors(choice, confidence),
        "choice": choice,
        "confidence": confidence,
    }
