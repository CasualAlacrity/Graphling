import asyncio
import re

from pydantic import BaseModel, PrivateAttr

import wiki_cache_client
from tools.http_utils import get_with_retries
from tools.starcitizenwiki.models import LocationPosition, ShipSpeed

API_BASE_URL = "https://api.star-citizen.wiki/api/v2/"
# Not under API_BASE_URL — a different, unversioned path. Found by watching network
# requests on the wiki's own route-planner tool (https://api.star-citizen.wiki/tools/
# route-planner), not from published API docs — it's what powers that tool's own
# distance/time display, but isn't a documented, stability-guaranteed public endpoint.
LOCATIONS_URL = "https://api.star-citizen.wiki/api/locations/positions"
# The versioned, per-location detail list — carries game data's quantum_travel block
# (arrival_radius, i.e. QT drop-out distance) that the positions endpoint above doesn't.
# Same uuids as the positions endpoint, confirmed live (Levski's matches), so the two
# join cleanly. Paginated, and page[size] caps at 200 (~11 pages for ~2000 locations).
LOCATION_DETAILS_URL = API_BASE_URL + "locations"
LOCATION_DETAILS_PAGE_SIZE = 200


class StarCitizenWikiClient(BaseModel):
    """No API key needed — confirmed via response headers, this is a public, unauthenticated
    endpoint. get_ship_speed/get_locations go through the server's shared cache now
    (server/wiki_cache_service.py, docs/todo.md Phase 2's cache follow-up) — this class
    only keeps an in-memory L1 in front of that HTTP call, a fetch-once-per-process fast
    path, not the source of truth. fetch_*_from_wiki below are the real API calls; only
    the server (which owns its own instance of this class) ever calls those directly."""

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

            ship_speed = await wiki_cache_client.get_ship_speed(ship_name)
            self._cache[cache_key] = ship_speed
            return ship_speed

    async def get_locations(self) -> list[LocationPosition]:
        """The full locations/positions dataset — ~1774 named locations (planets,
        moons, stations, cities, outposts) with real coordinates, letting travel time
        be estimated for pairs UEX's orbit-level distance data can't reach (e.g. an
        orbital station to a surface city on the same planet). Fetched once per
        process, same reasoning as get_ship_speed."""
        if self._locations_cache is not None:
            return self._locations_cache

        async with self._locations_cache_lock:
            if self._locations_cache is not None:
                return self._locations_cache

            locations = await wiki_cache_client.get_locations()
            self._locations_cache = locations
            return locations

    async def fetch_ship_speed_from_wiki(self, ship_name: str) -> ShipSpeed | None:
        """The real wiki API call, with no caching of its own — called only from
        server/wiki_cache_service.py, on a cache miss there."""
        response = await asyncio.to_thread(
            get_with_retries, API_BASE_URL + "vehicles", {}, {"filter[name]": ship_name},
        )
        response.raise_for_status()
        results = response.json()["data"]

        # The API's own name filter already does the fuzzy/partial matching (confirmed live
        # — "railen" correctly matched "Gatac Railen") — no client-side fuzzy match needed
        # on top of it. Just takes the first result if more than one comes back.
        if not results:
            return None
        ship_speed = ShipSpeed.model_validate(results[0])

        drive_stats = await self._fetch_quantum_drive_stats(results[0]["slug"])
        if drive_stats is None:
            return ship_speed
        return ship_speed.model_copy(update=drive_stats)

    async def _fetch_quantum_drive_stats(self, vehicle_slug: str) -> dict | None:
        """The installed quantum drive's acceleration stages — not on the vehicle
        response itself, only on the drive's own item record. Two more calls: the
        vehicle detail (the filtered list above leaves out components) to learn which
        drive is fitted, then that drive's item. None on any gap, rather than failing
        the whole ship lookup — travel_time falls back to the flat-speed model without
        these, the same thing every ship got before they existed."""
        response = await asyncio.to_thread(get_with_retries, API_BASE_URL + f"vehicles/{vehicle_slug}", {})
        if response.status_code != 200:
            return None
        components = response.json()["data"].get("components") or []
        drive_name = next((c["name"] for c in components if c.get("type") == "quantum_drives"), None)
        if drive_name is None:
            return None

        response = await asyncio.to_thread(
            get_with_retries, API_BASE_URL + "items", {}, {"filter[name]": drive_name},
        )
        if response.status_code != 200:
            return None
        # The item name filter is a partial match like the vehicle one — prefer the
        # exact name among results that are actually quantum drives.
        drives = [item for item in response.json()["data"] if item.get("quantum_drive")]
        drive = next((item for item in drives if item["name"] == drive_name), drives[0] if drives else None)
        if drive is None:
            return None

        standard_jump = drive["quantum_drive"].get("standard_jump") or {}
        return {
            "quantum_engage_speed": standard_jump.get("engage_speed"),
            "quantum_stage_one_accel": standard_jump.get("stage_one_accel_rate"),
            "quantum_stage_two_accel": standard_jump.get("stage_two_accel_rate"),
        }

    async def fetch_locations_from_wiki(self) -> list[LocationPosition]:
        """The real wiki API call, with no caching of its own — called only from
        server/wiki_cache_service.py, on a cache miss there."""
        response = await asyncio.to_thread(get_with_retries, LOCATIONS_URL, {})
        response.raise_for_status()
        rows = response.json()["data"]

        arrival_radii = await self._fetch_arrival_radii()
        locations = []
        for row in rows:
            location = LocationPosition.model_validate(row)
            location.arrival_radius = arrival_radii.get(row.get("uuid"))
            locations.append(location)
        return locations

    async def _fetch_arrival_radii(self) -> dict[str, float]:
        """uuid -> QT arrival radius (meters), across every page of the v2 locations
        list. Locations without a quantum_travel block (clinics, shops inside a
        station) are just absent from the result."""
        arrival_radii = {}
        page = 1
        while True:
            response = await asyncio.to_thread(
                get_with_retries, LOCATION_DETAILS_URL, {},
                {"page[size]": LOCATION_DETAILS_PAGE_SIZE, "page[number]": page},
            )
            response.raise_for_status()
            body = response.json()
            for row in body["data"]:
                quantum_travel = row.get("quantum_travel") or {}
                if quantum_travel.get("arrival_radius") is not None:
                    arrival_radii[row["uuid"]] = float(quantum_travel["arrival_radius"])
            if page >= body["meta"]["last_page"]:
                return arrival_radii
            page += 1

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

    async def find_jump_point(self, from_system: str, to_system: str) -> LocationPosition | None:
        """The connector to to_system, located in from_system (a jump point/gateway has
        a separate location entry — different coordinates — on each side).

        Three real, confirmed data quirks this works around:
        - Not always type="JumpPoint" — real jump points (e.g. "Stanton-Pyro Jump
          Point") are mostly tagged type="Anomaly" instead; only 2 locations actually
          use the JumpPoint type and neither is a real inter-system connector.
        - Not always named "Jump Point" either — Stanton's connector to Nyx has no
          Anomaly-type "Jump Point" entry at all, only "Nyx Gateway" (type="Manmade"),
          a waypoint station near the actual point rather than the point itself. Both
          types are searched; an actual "Jump Point" is preferred when one exists,
          since its coordinates are presumably more precise than a nearby station's.
        - Substring matching on the destination system name is unsafe — "Nyx" as a
          plain substring also matches "Onyx Facility ..." (120+ unrelated entries).
          Matched as a whole word instead.

        "Wreck Site" decoys (e.g. "Stanton-Pyro Jump Point Wreck Site") are excluded.
        """
        locations = await self.get_locations()
        from_lower = from_system.lower()
        to_pattern = re.compile(rf"\b{re.escape(to_system.lower())}\b")

        candidates = []
        for location in locations:
            if location.system.lower() != from_lower or location.type not in ("Anomaly", "Manmade"):
                continue
            name_lower = location.name.lower()
            if "wreck" in name_lower or not to_pattern.search(name_lower):
                continue
            candidates.append(location)

        for location in candidates:
            if "jump point" in location.name.lower():
                return location
        return candidates[0] if candidates else None

    async def find_gateway(self, from_system: str, to_system: str) -> LocationPosition | None:
        """The gateway station in from_system serving the jump to to_system (e.g.
        Stanton's "Pyro Gateway"). Pilots QT to this station, not the jump point itself
        — the jump point has no useful QT marker of its own — and fly the rest on
        hydrogen. Whole-word match on to_system, same reason as find_jump_point."""
        locations = await self.get_locations()
        from_lower = from_system.lower()
        to_pattern = re.compile(rf"\b{re.escape(to_system.lower())}\b")
        for location in locations:
            if location.system.lower() != from_lower or location.type != "Manmade":
                continue
            name_lower = location.name.lower()
            if "gateway" in name_lower and to_pattern.search(name_lower):
                return location
        return None

    async def nearest_jump_point(self, location: LocationPosition) -> LocationPosition | None:
        """The jump point physically closest to location, in its own system. Used to
        pair a gateway station with its jump point by geometry rather than by name —
        the names aren't reliable (Stanton's Nyx Gateway sits 52 km from a point the
        positions data calls "Stanton - Magnus Jump Point"), but every gateway is
        within ~40-50 km of its point while the next nearest is tens of Gm away."""
        locations = await self.get_locations()
        system_lower = location.system.lower()
        best, best_distance = None, None
        for candidate in locations:
            if candidate.system.lower() != system_lower:
                continue
            name_lower = candidate.name.lower()
            if "jump point" not in name_lower or "wreck" in name_lower:
                continue
            distance = (
                (candidate.x - location.x) ** 2 + (candidate.y - location.y) ** 2 + (candidate.z - location.z) ** 2
            )
            if best_distance is None or distance < best_distance:
                best, best_distance = candidate, distance
        return best
