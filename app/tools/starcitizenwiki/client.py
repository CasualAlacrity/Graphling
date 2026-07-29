import asyncio

from pydantic import BaseModel, PrivateAttr

from tools.http_utils import get_with_retries
from tools.starcitizenwiki.models import LocationPosition, ShipSpeed

API_BASE_URL = "https://api.star-citizen.wiki/api/v2/"
# Not under API_BASE_URL — a different, unversioned path. Found by watching network
# requests on the wiki's own route-planner tool (https://api.star-citizen.wiki/tools/
# route-planner), not from published API docs — it's what powers that tool's own
# distance/time display, but isn't a documented, stability-guaranteed public endpoint.
LOCATIONS_URL = "https://api.star-citizen.wiki/api/locations/positions"


class StarCitizenWikiClient(BaseModel):
    """No API key needed — confirmed via response headers, this is a public, unauthenticated
    endpoint. Cached in-memory only, no DB persistence: ship stats only change on game
    patches, not within a session, so there's no freshness window to track, just a
    fetch-once-per-ship-name cache for the process's lifetime."""

    _cache: dict[str, ShipSpeed | None] = PrivateAttr(default_factory=dict)
    _cache_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    _locations_cache: list[LocationPosition] | None = PrivateAttr(default=None)
    _locations_cache_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

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

    async def get_locations(self) -> list[LocationPosition]:
        """Fetches and caches the full locations/positions dataset — ~1774 named
        locations (planets, moons, stations, cities, outposts) with real coordinates,
        letting travel time be estimated for pairs UEX's orbit-level distance data can't
        reach (e.g. an orbital station to a surface city on the same planet). Fetched
        once, cached for the process's lifetime — static game data, same reasoning as
        get_ship_speed."""
        if self._locations_cache is not None:
            return self._locations_cache

        async with self._locations_cache_lock:
            if self._locations_cache is not None:
                return self._locations_cache

            response = await asyncio.to_thread(get_with_retries, LOCATIONS_URL, {})
            response.raise_for_status()
            locations = [LocationPosition.model_validate(row) for row in response.json()["data"]]
            self._locations_cache = locations
            return locations

    async def find_location(self, name: str, system: str) -> LocationPosition | None:
        """Exact, case-insensitive match on name + system. This cross-references two
        independently-maintained datasets (UEX and the wiki) that happen to use the same
        canonical place names — confirmed live, UEX's city_name/space_station_name
        values match this dataset's name field exactly — not noisy pilot speech, so an
        exact match is the right level of trust here rather than fuzzy-matching again."""
        locations = await self.get_locations()
        name_lower, system_lower = name.lower(), system.lower()
        for location in locations:
            if location.name.lower() == name_lower and location.system.lower() == system_lower:
                return location
        return None
