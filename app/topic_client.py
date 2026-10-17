"""The client side of the topic decision (app/topic_decision.py): request, social or
off-topic for one utterance, or None when it can't be had, in which case graph.py's
classify_topic falls back to its chat-model classifier.

LLM_PROVIDER=alice (every pilot): ask the server, which asks Tev1 on heket.
LLM_PROVIDER=ollama (development): ask the local Ollama's Tev1 directly, with the same
question and floors. OLLAMA_TOPIC_MODEL picks the model; set it empty to skip Tev1.
Any other provider: None, there's no decision model in that setup.

ALICE_TOOL_ROUTING=1 turns on the tool-routing experiment: the same call also picks which
tool groups respond needs (topic_decision.GROUP_QUESTION).
"""
import logging
import os
from dataclasses import dataclass

import httpx

import topic_decision
from api_client import authenticated_client

log = logging.getLogger(__name__)

# The decision itself takes ~30-40 ms; anything slower means trouble, and the fallback
# classifier answers in well under a second.
TIMEOUT_S = 3.0


@dataclass(frozen=True)
class TopicDecision:
    kind: str
    # Tool groups for respond, or None for every tool (routing off, or Tev1 unsure).
    tool_groups: list[str] | None = None


def tool_routing_enabled() -> bool:
    return os.getenv("ALICE_TOOL_ROUTING", "").strip().lower() in ("1", "true", "on")


async def decide(utterance: str) -> TopicDecision | None:
    provider = os.getenv("LLM_PROVIDER", "ollama")
    try:
        if provider == "alice":
            return await _from_server(utterance)
        if provider == "ollama":
            return await _from_local_ollama(utterance)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        log.warning("topic decision unavailable (%s); using the classifier model", exc)
    return None


async def _from_server(utterance: str) -> TopicDecision:
    body = {"utterance": utterance, "groups": tool_routing_enabled()}
    async with await authenticated_client() as client:
        response = await client.post("/llm/decide/topic", json=body, timeout=TIMEOUT_S)
        response.raise_for_status()
        answer = response.json()
        return TopicDecision(answer["kind"], answer.get("tool_groups"))


async def _from_local_ollama(utterance: str) -> TopicDecision | None:
    model = os.getenv("OLLAMA_TOPIC_MODEL", "tev1:4b")
    if not model:
        return None
    base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    routing = tool_routing_enabled()
    if routing:
        body = topic_decision.request_body_with_groups(model, utterance)
    else:
        body = topic_decision.request_body(model, utterance)
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        response = await client.post(f"{base_url}/v1/systemone", json=body)
        response.raise_for_status()
        answer = response.json()
    choice, confidence = topic_decision.read_answer(answer)
    tool_groups = None
    if routing:
        tool_groups = topic_decision.chosen_groups(topic_decision.read_group_probabilities(answer))
    return TopicDecision(topic_decision.apply_floors(choice, confidence), tool_groups)
