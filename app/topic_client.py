"""The client side of the topic decision (app/topic_decision.py): request, social or
off-topic for one utterance, or None when it can't be had, in which case graph.py's
classify_topic falls back to its chat-model classifier.

LLM_PROVIDER=alice (every pilot): ask the server, which asks Tev1 on heket.
LLM_PROVIDER=ollama (development): ask the local Ollama's Tev1 directly, with the same
question and floors. OLLAMA_TOPIC_MODEL picks the model; set it empty to skip Tev1.
Any other provider: None, there's no decision model in that setup.
"""
import logging
import os

import httpx

import topic_decision
from api_client import authenticated_client

log = logging.getLogger(__name__)

# The decision itself takes ~30 ms; anything slower means trouble, and the fallback
# classifier answers in well under a second.
TIMEOUT_S = 3.0


async def topic_kind(utterance: str) -> str | None:
    provider = os.getenv("LLM_PROVIDER", "ollama")
    try:
        if provider == "alice":
            return await _from_server(utterance)
        if provider == "ollama":
            return await _from_local_ollama(utterance)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        log.warning("topic decision unavailable (%s); using the classifier model", exc)
    return None


async def _from_server(utterance: str) -> str:
    async with await authenticated_client() as client:
        response = await client.post("/llm/decide/topic", json={"utterance": utterance}, timeout=TIMEOUT_S)
        response.raise_for_status()
        return response.json()["kind"]


async def _from_local_ollama(utterance: str) -> str | None:
    model = os.getenv("OLLAMA_TOPIC_MODEL", "tev1:4b")
    if not model:
        return None
    base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        response = await client.post(f"{base_url}/v1/systemone", json=topic_decision.request_body(model, utterance))
        response.raise_for_status()
        choice, confidence = topic_decision.read_answer(response.json())
        return topic_decision.apply_floors(choice, confidence)
