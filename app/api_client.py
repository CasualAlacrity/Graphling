"""Shared HTTP plumbing for every client-side module that talks to app/server/ —
ledger_client.py, uex_cache_client.py, wiki_cache_client.py all use this instead of each
building their own authenticated httpx.AsyncClient.
"""
import os

import httpx

from auth.discord_identity import get_pilot_session


def api_url() -> str:
    return os.getenv("ALICE_API_URL", "http://localhost:8000").rstrip("/")


async def authenticated_client() -> httpx.AsyncClient:
    session = await get_pilot_session()
    return httpx.AsyncClient(base_url=api_url(), headers={"Authorization": f"Bearer {session.token}"}, timeout=15)


def raise_for_status(response: httpx.Response) -> None:
    # The server's routes return 400 with {"detail": "<message>"} for a business-logic
    # ValueError — surface that exact text so a caller's existing
    # `except ValueError as ve: return str(ve)` handling keeps working unchanged.
    if response.status_code == 400:
        raise ValueError(response.json().get("detail", response.text))
    response.raise_for_status()
