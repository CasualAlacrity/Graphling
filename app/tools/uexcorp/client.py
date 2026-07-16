import asyncio
import time
from datetime import datetime, timedelta, timezone

import requests
from langsmith import traceable
from pydantic import BaseModel, PrivateAttr

from db.session import SessionLocal
from tools.uexcorp.reference_cache import (UexReferenceCache, CachedCommodity, CachedStarSystem, CachedOrbit,
                                           CachedTerminal, CachedMoon, CachedItemCategory, CachedItem,
                                           CachedVehicle, CachedRefineryYield, CachedPoi, CachedCommodityStatus)
from tools.uexcorp.reference_cache_store import load_reference_cache, store_reference_cache

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30


def _get_with_retries(url: str, headers: dict, params: dict | None = None) -> requests.Response:
    response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    for attempt in range(1, RETRY_ATTEMPTS):
        if response.status_code < 500:
            return response
        time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    return response


class UEXCorpClient(BaseModel):
    API_BASE_URL: str = "https://api.uexcorp.uk/2.0/"
    api_key: str
    bearer_token: str
    _uex_cache: UexReferenceCache | None = PrivateAttr(default=None)
    _cache_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    def _is_fresh(self, uex_cache: UexReferenceCache) -> bool:
        return (datetime.now(timezone.utc) - uex_cache.fetched_at) < timedelta(hours=24)

    @traceable(name="uex_get_reference_cache")
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

            async with SessionLocal() as session:
                stored_cache = await load_reference_cache(session)
            if stored_cache is not None:
                print("Using cached UEX reference data from Postgres.", flush=True)
                self._uex_cache = stored_cache
                return stored_cache

            print("No usable cache found — fetching fresh data from the UEX API "
                  "(first run can take a minute)...", flush=True)
            uex_cache = await self._build_uex_cache()
            self._uex_cache = uex_cache
            print("UEX API fetch complete.", flush=True)

            async with SessionLocal() as session:
                await store_reference_cache(session, uex_cache)

            return uex_cache

    async def _build_uex_cache(self) -> UexReferenceCache:
        headers = self.get_header()

        commodities_resp, star_systems_resp, orbits_resp, terminals_resp, moons_resp, categories_resp, vehicles_resp, refinery_yields_resp, poi_resp, commodity_status_resp = await asyncio.gather(
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'commodities', headers),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'star_systems', headers),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'orbits', headers),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'terminals', headers),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'moons', headers),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'categories', headers, {"type": 'item'}),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'vehicles', headers),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'refineries_yields', headers),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'poi', headers),
            asyncio.to_thread(_get_with_retries, self.API_BASE_URL + 'commodities_status', headers),
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
                    _get_with_retries, self.API_BASE_URL + 'items', headers, {"id_category": category["id"]}
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
            fetched_at=datetime.now(timezone.utc),
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

    @traceable(name="uex_get_orbit_distances")
    async def get_orbit_distances(self, origin_orbit_id: int, origin_star_system_id: int) -> list[dict]:
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
