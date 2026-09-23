import asyncio
from datetime import UTC, datetime, timedelta

import requests
from langsmith import traceable
from pydantic import BaseModel, PrivateAttr

import uex_cache_client
from tools.http_utils import REQUEST_TIMEOUT_SECONDS, get_with_retries
from tools.uexcorp.reference_cache import (
    CachedCommodity,
    CachedCommodityStatus,
    CachedItem,
    CachedItemCategory,
    CachedMoon,
    CachedOrbit,
    CachedPoi,
    CachedRefineryYield,
    CachedStarSystem,
    CachedTerminal,
    CachedVehicle,
    UexReferenceCache,
)


class UEXCorpClient(BaseModel):
    API_BASE_URL: str = "https://api.uexcorp.uk/2.0/"
    api_key: str
    bearer_token: str
    _uex_cache: UexReferenceCache | None = PrivateAttr(default=None)
    _cache_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    # Orbit distances are static game-world data (no freshness window needed, unlike
    # _uex_cache). Callers that rank many routes from one origin (find_best_route) were
    # each independently re-fetching this per candidate route — same origin, same
    # response, fetched dozens of times in a row. No lock: a race just costs one wasted
    # duplicate fetch, not a correctness issue.
    _orbit_distances_cache: dict[tuple[int, int], list[dict]] = PrivateAttr(default_factory=dict)

    def _is_fresh(self, uex_cache: UexReferenceCache) -> bool:
        return (datetime.now(UTC) - uex_cache.fetched_at) < timedelta(hours=24)

    # NOT traceable. This is a memoized accessor, and estimate_travel_time calls it once
    # per candidate route — 261 times in a single warm region search, every one of them a
    # cache hit returning immediately. Tracing the accessor turned one route search into
    # 261 traced runs of a no-op and burned a month of LangSmith quota in two days. The
    # actual fetch is traced instead, on build_uex_cache below, where there's real work
    # and a real duration worth seeing.
    #
    # In-memory only on this side now — the Postgres-backed cache (docs/todo.md Phase 2's
    # cache follow-up) moved server-side (server/uex_cache_service.py), since the client
    # can't assume Postgres is reachable at all. This is just the process-lifetime L1
    # fast path in front of the HTTP call, same shape it always had in front of the DB
    # check.
    async def get_uex_cache(self) -> UexReferenceCache:
        uex_cache = self._uex_cache
        if uex_cache and self._is_fresh(uex_cache):
            return uex_cache

        # Cache is missing or stale. Multiple tool calls can reach this point at once (e.g. the LLM
        # calling two tools in the same turn) — without a lock, each would kick off its own full
        # rebuild concurrently and flood UEX with duplicate requests, tripping their rate limit.
        async with self._cache_lock:
            # Re-check now that we hold the lock: another caller may have already rebuilt it while
            # we were waiting, in which case we're done and skip the fetch entirely.
            uex_cache = self._uex_cache
            if uex_cache and self._is_fresh(uex_cache):
                return uex_cache

            print("Fetching UEX reference data (server-cached)...", flush=True)
            uex_cache = await uex_cache_client.get_reference_cache()
            self._uex_cache = uex_cache
            print("UEX reference data ready.", flush=True)
            return uex_cache

    @traceable(name="uex_build_reference_cache")
    async def build_uex_cache(self) -> UexReferenceCache:
        """Hits every UEX reference endpoint and assembles a fresh UexReferenceCache —
        no caching of its own (that's the caller's job; server/uex_cache_service.py is
        the only caller now, on a Postgres cache miss). Public because it's called from
        outside this class now, unlike when get_uex_cache was the only caller."""
        headers = self.get_header()

        (
            commodities_resp, star_systems_resp, orbits_resp, terminals_resp, moons_resp,
            categories_resp, vehicles_resp, refinery_yields_resp, poi_resp, commodity_status_resp,
        ) = await asyncio.gather(
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'commodities', headers),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'star_systems', headers),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'orbits', headers),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'terminals', headers),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'moons', headers),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'categories', headers, {"type": 'item'}),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'vehicles', headers),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'refineries_yields', headers),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'poi', headers),
            asyncio.to_thread(get_with_retries, self.API_BASE_URL + 'commodities_status', headers),
        )

        commodities_resp.raise_for_status()
        star_systems_resp.raise_for_status()
        orbits_resp.raise_for_status()
        terminals_resp.raise_for_status()
        moons_resp.raise_for_status()
        categories_resp.raise_for_status()
        vehicles_resp.raise_for_status()
        refinery_yields_resp.raise_for_status()
        poi_resp.raise_for_status()
        commodity_status_resp.raise_for_status()

        item_tasks = []
        for category in categories_resp.json()["data"]:
            if category:
                task = asyncio.to_thread(
                    get_with_retries, self.API_BASE_URL + 'items', headers, {"id_category": category["id"]}
                )
                item_tasks.append(task)
        item_responses = await asyncio.gather(*item_tasks)

        items_data = []
        for response in item_responses:
            response.raise_for_status()
            category_items = response.json()["data"]
            if category_items:
                items_data.extend(category_items)

        commodity_status_data = commodity_status_resp.json()["data"]
        commodity_statuses = [
            CachedCommodityStatus.model_validate({**row, "type": "buy"})
            for row in commodity_status_data["buy"]
        ] + [
            CachedCommodityStatus.model_validate({**row, "type": "sell"})
            for row in commodity_status_data["sell"]
        ]

        uex_cache = UexReferenceCache(
            fetched_at=datetime.now(UTC),
            commodities=[CachedCommodity.model_validate(row) for row in commodities_resp.json()["data"]],
            star_systems=[CachedStarSystem.model_validate(row) for row in star_systems_resp.json()["data"]],
            orbits=[CachedOrbit.model_validate(row) for row in orbits_resp.json()["data"]],
            terminals=[CachedTerminal.model_validate(row) for row in terminals_resp.json()["data"]],
            moons=[CachedMoon.model_validate(row) for row in moons_resp.json()["data"]],
            item_categories=[CachedItemCategory.model_validate(row) for row in categories_resp.json()["data"]],
            items=[CachedItem.model_validate(row) for row in items_data],
            vehicles=[CachedVehicle.model_validate(row) for row in vehicles_resp.json()["data"]],
            refinery_yields=[CachedRefineryYield.model_validate(row) for row in refinery_yields_resp.json()["data"]],
            poi=[CachedPoi.model_validate(row) for row in poi_resp.json()["data"]],
            commodity_statuses=commodity_statuses,
        )

        return uex_cache

    @traceable(name="uex_get_commodity_routes")
    async def get_commodity_routes(
        self,
        commodity_id: int | None = None,
        origin_terminal_id: int | None = None,
        destination_terminal_id: int | None = None,
        investment: int | None = None,
    ) -> list[dict]:
        params = {}
        if commodity_id is not None:
            params["id_commodity"] = commodity_id
        if origin_terminal_id is not None:
            params["id_terminal_origin"] = origin_terminal_id
        if destination_terminal_id is not None:
            params["id_terminal_destination"] = destination_terminal_id
        if investment is not None:
            params["investment"] = investment

        response = await asyncio.to_thread(
            requests.get,
            self.API_BASE_URL + 'commodities_routes',
            params=params,
            headers=self.get_header(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["data"]

    @traceable(name="uex_get_commodity_prices")
    async def get_commodity_prices(self, commodity_id: int) -> list[dict]:
        response = await asyncio.to_thread(
            requests.get,
            self.API_BASE_URL + 'commodities_prices',
            params={"id_commodity": commodity_id},
            headers=self.get_header(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["data"]

    @traceable(name="uex_get_terminal_prices")
    async def get_terminal_prices(self, terminal_id: int) -> list[dict]:
        response = await asyncio.to_thread(
            requests.get,
            self.API_BASE_URL + 'commodities_prices',
            params={"id_terminal": terminal_id},
            headers=self.get_header(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["data"]

    @traceable(name="uex_get_item_prices")
    async def get_item_prices(self, item_id: int) -> list[dict]:
        response = await asyncio.to_thread(
            requests.get,
            self.API_BASE_URL + 'items_prices',
            params={"id_item": item_id},
            headers=self.get_header(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["data"]

    @traceable(name="uex_get_vehicle_purchase_prices")
    async def get_vehicle_purchase_prices(self, vehicle_id: int) -> list[dict]:
        response = await asyncio.to_thread(
            requests.get,
            self.API_BASE_URL + 'vehicles_purchases_prices',
            params={"id_vehicle": vehicle_id},
            headers=self.get_header(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["data"]

    @traceable(name="uex_get_vehicle_rental_prices")
    async def get_vehicle_rental_prices(self, vehicle_id: int) -> list[dict]:
        response = await asyncio.to_thread(
            requests.get,
            self.API_BASE_URL + 'vehicles_rentals_prices',
            params={"id_vehicle": vehicle_id},
            headers=self.get_header(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["data"]

    # Same shape as get_uex_cache: memoized, and travel_time calls it inside the
    # per-candidate loop whenever wiki coordinates are missing for a terminal. Tracing the
    # accessor would emit a run per cache hit, which is exactly what burned a month of
    # quota. The miss path below carries the decorator instead.
    async def get_orbit_distances(self, origin_orbit_id: int, origin_star_system_id: int) -> list[dict]:
        cache_key = (origin_orbit_id, origin_star_system_id)
        cached = self._orbit_distances_cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._fetch_orbit_distances(origin_orbit_id, origin_star_system_id)
        self._orbit_distances_cache[cache_key] = data
        return data

    @traceable(name="uex_fetch_orbit_distances")
    async def _fetch_orbit_distances(self, origin_orbit_id: int, origin_star_system_id: int) -> list[dict]:
        response = await asyncio.to_thread(
            requests.get,
            self.API_BASE_URL + 'orbits_distances',
            params={
                "id_orbit_origin": origin_orbit_id,
                "id_star_system_origin": origin_star_system_id,
            },
            headers=self.get_header(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()["data"]

    def get_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}
