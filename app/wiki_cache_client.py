"""HTTP client for the server's wiki cache (app/server/wiki_cache_service.py) — replaces
StarCitizenWikiClient's old in-memory-only, per-process caching with a shared,
Postgres-backed cache pilots reuse across processes, same shape as the UEX cache
(docs/todo.md Phase 2's cache follow-up).
"""
from api_client import authenticated_client, raise_for_status
from tools.starcitizenwiki.models import LocationPosition, ShipSpeed


async def get_ship_speed(ship_name: str) -> ShipSpeed | None:
    # Query param, not a path segment — ship names contain spaces ("Gatac Railen") and
    # httpx won't URL-encode an f-string dropped straight into a path.
    async with await authenticated_client() as client:
        response = await client.get("/wiki-cache/ship-speed", params={"ship_name": ship_name})
        raise_for_status(response)
        data = response.json()
        if data is None:
            return None
        # model_construct, not model_validate: ShipSpeed's fields use validation_alias=
        # AliasPath("speed", "scm") etc. to parse the wiki's own nested API response
        # shape, but FastAPI serializes a response_model with plain model_dump(), which
        # writes the flat field names — model_validate() on that flat JSON always fails
        # looking for the nested shape. See server/wiki_cache_service.py's identical note.
        return ShipSpeed.model_construct(**data)


async def get_locations() -> list[LocationPosition]:
    async with await authenticated_client() as client:
        response = await client.get("/wiki-cache/locations")
        raise_for_status(response)
        return [LocationPosition.model_validate(row) for row in response.json()]
