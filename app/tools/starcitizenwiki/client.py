import asyncio

from pydantic import BaseModel, PrivateAttr

from tools.http_utils import get_with_retries
from tools.starcitizenwiki.models import ShipSpeed

API_BASE_URL = "https://api.star-citizen.wiki/api/v2/"


class StarCitizenWikiClient(BaseModel):
    """No API key needed — confirmed via response headers, this is a public, unauthenticated
    endpoint. Cached in-memory only, no DB persistence: ship stats only change on game
    patches, not within a session, so there's no freshness window to track, just a
    fetch-once-per-ship-name cache for the process's lifetime."""

    _cache: dict[str, ShipSpeed | None] = PrivateAttr(default_factory=dict)
    _cache_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    async def get_ship_speed(self, ship_name: str) -> ShipSpeed | None:
        cache_key = ship_name.lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        async with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

            response = await asyncio.to_thread(
                get_with_retries, API_BASE_URL + "vehicles", {}, {"filter[name]": ship_name},
            )
            response.raise_for_status()
            results = response.json()["data"]

            # The API's own name filter already does the fuzzy/partial matching (confirmed live
            # — "railen" correctly matched "Gatac Railen") — no client-side fuzzy match needed
            # on top of it. Just takes the first result if more than one comes back.
            ship_speed = ShipSpeed.model_validate(results[0]) if results else None
            self._cache[cache_key] = ship_speed
            return ship_speed
