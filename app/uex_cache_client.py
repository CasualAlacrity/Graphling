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


async def cached_routes_from_terminals(terminal_ids: list[int]) -> dict[int, list[dict]]:
    """Terminal id -> routes out of it, for every one of these that's cached and fresh;
    a missing id is a miss. One request for the whole list, and never a live UEX fetch
    server-side — see fetch_routes_from_terminal for that."""
    async with await authenticated_client() as client:
        response = await client.post("/uex-cache/routes-from-terminals/cached", json=terminal_ids)
        raise_for_status(response)
        rows_by_origin = {}
        for terminal_id, rows in response.json().items():
            rows_by_origin[int(terminal_id)] = rows
        return rows_by_origin


async def fetch_routes_from_terminal(terminal_id: int) -> list[dict]:
    """Always triggers a live UEX fetch server-side and stores the result."""
    async with await authenticated_client() as client:
        response = await client.post(f"/uex-cache/routes-from-terminal/{terminal_id}/fetch")
        raise_for_status(response)
        return response.json()


async def get_item_price_rows(item_id: int) -> list[dict]:
    return await _get_rows(f"/uex-cache/item-prices/{item_id}")


async def get_vehicle_purchase_price_rows(vehicle_id: int) -> list[dict]:
    return await _get_rows(f"/uex-cache/vehicle-purchase-prices/{vehicle_id}")


async def get_vehicle_rental_price_rows(vehicle_id: int) -> list[dict]:
    return await _get_rows(f"/uex-cache/vehicle-rental-prices/{vehicle_id}")


async def get_orbit_distance_rows(orbit_id: int, star_system_id: int) -> list[dict]:
    return await _get_rows(f"/uex-cache/orbit-distances/{orbit_id}", params={"star_system_id": star_system_id})


async def _get_rows(path: str, params: dict | None = None) -> list[dict]:
    async with await authenticated_client() as client:
        response = await client.get(path, params=params)
        raise_for_status(response)
        return response.json()
