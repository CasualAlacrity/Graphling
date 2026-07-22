from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client
from langsmith.client import prompt_cache_singleton

_client = Client()
CACHE_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "prompts" / ".prompt_cache.json"


def load_prompt(prompt_identifier: str) -> ChatPromptTemplate:
    try:
        template = _client.pull_prompt(prompt_identifier)
        prompt_cache_singleton.dump(CACHE_SNAPSHOT_PATH)
        return template
    except Exception:
        prompt_cache_singleton.load(CACHE_SNAPSHOT_PATH)
        return _client.pull_prompt(prompt_identifier)
