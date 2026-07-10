import os
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection

# Anchored to app/, not the process's CWD — main.py, voice.py, and one-off
# scripts get run from different working directories, and a CWD-relative path
# would silently point at a different store (and lose data) depending on how
# the app was launched.
_DEFAULT_CHROMA_PATH = str(Path(__file__).resolve().parent.parent / ".chroma")
_CHROMA_PATH = os.getenv("CHROMA_PATH", _DEFAULT_CHROMA_PATH)
_COLLECTION_NAME = "pilot_memory"

_client: chromadb.ClientAPI | None = None


def get_pilot_memory_collection() -> Collection:
    """
    Local persistent Chroma collection for pilot memory embeddings.

    embedding_function=None is deliberate — embeddings always come from our own
    provider-agnostic get_embeddings() helper (llm.py), never Chroma's built-in
    default model. Callers must always pass embeddings=/query_embeddings=
    explicitly, never documents=/query_texts= alone, or Chroma silently falls
    back to downloading and running its own local embedding model.
    """
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=_CHROMA_PATH)
    return _client.get_or_create_collection(_COLLECTION_NAME, embedding_function=None)
