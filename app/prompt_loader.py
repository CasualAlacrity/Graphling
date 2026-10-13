import os

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client
from langsmith.client import prompt_cache_singleton

import bundle

_client = Client()
CACHE_SNAPSHOT_PATH = bundle.root() / "prompts" / ".prompt_cache.json"


def load_prompt(prompt_identifier: str) -> ChatPromptTemplate:
    try:
        template = _client.pull_prompt(prompt_identifier)
        prompt_cache_singleton.dump(CACHE_SNAPSHOT_PATH)
        return template
    except Exception:
        prompt_cache_singleton.load(CACHE_SNAPSHOT_PATH)
        return _client.pull_prompt(prompt_identifier)


def placeholder(prompt_identifier: str) -> str:
    """What the client sends in place of a prompt through ALICE's gateway — the server
    swaps the real text in (server/prompt_service.py's PLACEHOLDER)."""
    return f"{{{{alice-prompt:{prompt_identifier}}}}}"


def prompt_messages(prompt_identifier: str) -> list[BaseMessage]:
    """A prompt's messages, ready to put in front of a conversation.

    Through ALICE's own gateway (LLM_PROVIDER=alice — every pilot) that's a single
    placeholder: the server holds the prompts and expands it, so the text never ships
    with the client or crosses the wire. Every other provider (development against
    Ollama/OpenAI/Anthropic directly) has no server in the loop, so the text is loaded
    here as before — the LangSmith Hub, falling back to the committed snapshot."""
    if os.getenv("LLM_PROVIDER", "ollama") == "alice":
        return [SystemMessage(content=placeholder(prompt_identifier))]
    return load_prompt(prompt_identifier).invoke({}).to_messages()
