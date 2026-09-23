"""HTTP client for the server's UEX cache (app/server/uex_cache_service.py) — replaces
what used to be direct Postgres access from tools/uexcorp/client.py, tools/route_ranking.py,
and overlay/uex_lookup.py (docs/todo.md Phase 2's cache follow-up: the client can't
assume Postgres is reachable at all, only the server can).

Same function names the direct-DB code had, so every caller only needed its import
changed. Returns plain dicts/UexReferenceCache — exactly what the old SessionLocal-based
calls returned, nothing about the callers' own parsing needed to change.
"""
from api_client import authenticated_client, raise_for_status
from tools.uexcorp.reference_cache import UexReferenceCache


async def get_reference_cache() -> UexReferenceCache:
    async with await authenticated_client() as client:
        response = await client.get("/uex-cache/reference")
        raise_for_status(response)
        return UexReferenceCache.model_validate(response.json())


async def get_commodity_price_rows(commodity_id: int) -> list[dict]:
    async with await authenticated_client() as client:
        response = await client.get(f"/uex-cache/commodity-prices/{commodity_id}")
        raise_for_status(response)
        return response.json()


async def get_terminal_price_rows(terminal_id: int) -> list[dict]:
    async with await authenticated_client() as client:
        response = await client.get(f"/uex-cache/terminal-prices/{terminal_id}")
        raise_for_status(response)
        return response.json()


async def get_commodity_route_rows(commodity_id: int) -> list[dict]:
    async with await authenticated_client() as client:
        response = await client.get(f"/uex-cache/commodity-routes/{commodity_id}")
        raise_for_status(response)
        return response.json()


async def cached_routes_from_terminal(terminal_id: int) -> list[dict] | None:
    """Routes out of this terminal if they're cached and fresh, else None. Never
    triggers a live UEX fetch server-side — see fetch_routes_from_terminal for that."""
    async with await authenticated_client() as client:
        response = await client.get(f"/uex-cache/routes-from-terminal/{terminal_id}/cached")
        raise_for_status(response)
        return response.json()


async def fetch_routes_from_terminal(terminal_id: int) -> list[dict]:
    """Always triggers a live UEX fetch server-side and stores the result."""
    async with await authenticated_client() as client:
        response = await client.post(f"/uex-cache/routes-from-terminal/{terminal_id}/fetch")
        raise_for_status(response)
        return response.json()
