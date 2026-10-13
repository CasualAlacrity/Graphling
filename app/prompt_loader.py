from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client
from langsmith.client import prompt_cache_singleton

import bundle

_client = Client()
CACHE_SNAPSHOT_PATH = bundle.root() / "prompts" / ".prompt_cache.json"


def load_prompt(prompt_identifier: str) -> ChatPromptTemplate:
    try:
        template = _client.pull_prompt(prompt_identifier)
        # Refreshing the snapshot is a developer-side thing — it's the committed copy
        # pilots fall back on. A packaged install's copy sits in the read-only install
        # folder and is only ever read.
        if not bundle.is_frozen():
            prompt_cache_singleton.dump(CACHE_SNAPSHOT_PATH)
        return template
    except Exception:
        prompt_cache_singleton.load(CACHE_SNAPSHOT_PATH)
        return _client.pull_prompt(prompt_identifier)
