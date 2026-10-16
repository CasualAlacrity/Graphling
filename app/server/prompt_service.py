"""ALICE's prompts, held server-side and swapped into LLM gateway requests — so the
persona/classifier text never ships to pilots (not in the installer, not over the wire).

The client sends a placeholder system message, {{alice-prompt:<name>}}
(prompt_loader.prompt_messages); expand() replaces it with that prompt's messages before
the request is routed anywhere, so heket and both hosted fallbacks see the real
text. Every ALICE prompt is static — no template variables, by design, so prewarming can
match it (graph.decline_topic's note) — which is what makes a straight swap possible.

Sources, in order:
1. The LangSmith Hub, when LANGSMITH_API_KEY is set — the same prompts the desktop
   client pulls in development. settings.prompt_hub_tag pins a tag ("prod"), so editing
   a prompt doesn't change what pilots get until that tag is moved: that's the release.
2. prompts/.prompt_cache.json, the committed snapshot, copied into the image.

Read with the langsmith SDK's raw pull_prompt_commit and parsed here rather than via
LangChain's ChatPromptTemplate: the server image deliberately has no LangChain
(server/requirements.txt), and the manifest is a small, stable JSON shape.
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

from server.config import settings

log = logging.getLogger(__name__)

PROMPT_NAMES = ("alice-persona", "topic-classification", "topic-reject-voice")
PLACEHOLDER = re.compile(r"^\{\{alice-prompt:([a-z0-9-]+)\}\}$")
SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "prompts" / ".prompt_cache.json"

# LangChain message-template class -> Ollama /api/chat role.
_ROLES = {
    "SystemMessagePromptTemplate": "system",
    "HumanMessagePromptTemplate": "user",
    "AIMessagePromptTemplate": "assistant",
}


class UnknownPrompt(ValueError):
    pass


class _Store:
    """name -> Ollama-format messages, plus where each came from (for logs)."""

    def __init__(self):
        self.messages: dict[str, list[dict]] = {}
        self.sources: dict[str, str] = {}


store = _Store()


def messages_from_manifest(manifest: dict) -> list[dict]:
    """A Hub ChatPromptTemplate manifest -> [{"role", "content"}, ...]. Rejects anything
    with template variables: there's nothing on the server to fill them with, and a
    half-formatted prompt is worse than a failed refresh (the old one stays live)."""
    result = []
    for entry in manifest["kwargs"]["messages"]:
        role = _ROLES.get(entry["id"][-1])
        if role is None:
            raise ValueError(f"unsupported message type {entry['id'][-1]}")
        prompt = entry["kwargs"]["prompt"]["kwargs"]
        if prompt.get("input_variables"):
            raise ValueError(f"prompt has template variables {prompt['input_variables']}")
        template = prompt["template"]
        # An f-string template with no variables still escapes literal braces as {{ }};
        # format() with nothing to fill unescapes them exactly as ChatPromptTemplate does.
        if prompt.get("template_format", "f-string") == "f-string":
            template = template.format()
        result.append({"role": role, "content": template})
    return result


def load_snapshot() -> dict[str, list[dict]]:
    entries = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["entries"]
    loaded = {}
    for name in PROMPT_NAMES:
        loaded[name] = messages_from_manifest(entries[name]["manifest"])
    return loaded


def _pull_from_hub(name: str) -> list[dict]:
    from langsmith import Client

    identifier = f"{name}:{settings.prompt_hub_tag}" if settings.prompt_hub_tag else name
    commit = Client().pull_prompt_commit(identifier, skip_cache=True)
    return messages_from_manifest(commit.manifest)


async def refresh() -> None:
    """Load every prompt, per prompt keeping the best source that works: Hub, then
    whatever's already loaded, then the snapshot. A Hub outage never blanks a prompt."""
    snapshot = None
    hub_enabled = bool(os.getenv("LANGSMITH_API_KEY"))
    for name in PROMPT_NAMES:
        if hub_enabled:
            try:
                messages = await asyncio.to_thread(_pull_from_hub, name)
                if messages != store.messages.get(name):
                    log.info("prompt %s loaded from the Hub", name)
                store.messages[name], store.sources[name] = messages, "hub"
                continue
            except Exception as exc:
                log.warning("couldn't pull prompt %s from the Hub (%s)", name, exc)
        if name in store.messages:
            continue
        if snapshot is None:
            snapshot = load_snapshot()
        store.messages[name], store.sources[name] = snapshot[name], "snapshot"
        log.info("prompt %s loaded from the snapshot", name)


async def refresh_forever() -> None:
    if settings.prompt_refresh_s <= 0:
        return
    while True:
        await asyncio.sleep(settings.prompt_refresh_s)
        try:
            await refresh()
        except Exception:
            log.exception("prompt refresh failed; keeping the prompts already loaded")


def expand(payload: dict) -> dict:
    """The request with every placeholder system message replaced by its prompt's
    messages. Messages that aren't placeholders pass through untouched."""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload
    expanded = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        match = PLACEHOLDER.match(content) if isinstance(content, str) else None
        if match is None or message.get("role") != "system":
            expanded.append(message)
            continue
        name = match.group(1)
        if name not in store.messages:
            raise UnknownPrompt(f"Unknown prompt {name!r}; expected one of {sorted(store.messages)}")
        expanded.extend(dict(prompt_message) for prompt_message in store.messages[name])
    return {**payload, "messages": expanded}
