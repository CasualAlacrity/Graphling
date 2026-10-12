"""Shared HTTP plumbing for every client-side module that talks to app/server/ —
ledger_client.py, uex_cache_client.py, wiki_cache_client.py all use this instead of each
building their own authenticated httpx.AsyncClient.
"""
import os

import httpx

from auth.session_auth import PilotSessionAuth


def api_url() -> str:
    return os.getenv("ALICE_API_URL", "http://localhost:8000").rstrip("/")


async def authenticated_client() -> httpx.AsyncClient:
    """The token rides on PilotSessionAuth: attached per request, and a 401 triggers one
    re-login and a retry. No per-call validation round trip — get_pilot_session already
    checked the session at startup."""
    return httpx.AsyncClient(base_url=api_url(), auth=PilotSessionAuth(), timeout=15)


def raise_for_status(response: httpx.Response) -> None:
    # The server's routes return 400 with {"detail": "<message>"} for a business-logic
    # ValueError — surface that exact text so a caller's existing
    # `except ValueError as ve: return str(ve)` handling keeps working unchanged.
    if response.status_code == 400:
        raise ValueError(response.json().get("detail", response.text))
    response.raise_for_status()
