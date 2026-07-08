import asyncio
from datetime import datetime, timedelta, timezone

import requests
from langsmith import traceable
from pydantic import BaseModel, PrivateAttr

from tools.uexcorp_reference_cache import (UexReferenceCache, CachedCommodity, CachedStarSystem, CachedOrbit,
                                           CachedTerminal)


class UEXCorpClient(BaseModel):
    API_BASE_URL: str = "https://api.uexcorp.uk/2.0/"
    api_key: str
    bearer_token: str
    _uex_cache: UexReferenceCache | None = PrivateAttr(default=None)

    @traceable(name="uex_get_reference_cache")
    async def get_uex_cache(self) -> UexReferenceCache:
        uex_cache = self._uex_cache
        if uex_cache:
            # Cache exists, check freshness
            fresh_cache = (datetime.now(timezone.utc) - uex_cache.fetched_at) < timedelta(hours=24)
            if fresh_cache:
                return uex_cache

        # Cache doesn't exist, or needs a refresh
        headers = self.get_header()

        commodities_resp, star_systems_resp, orbits_resp, terminals_resp = await asyncio.gather(
            asyncio.to_thread(requests.get, self.API_BASE_URL + 'commodities', headers=headers),
            asyncio.to_thread(requests.get, self.API_BASE_URL + 'star_systems', headers=headers),
            asyncio.to_thread(requests.get, self.API_BASE_URL + 'orbits', headers=headers),
            asyncio.to_thread(requests.get, self.API_BASE_URL + 'terminals', headers=headers),
        )

        commodities_resp.raise_for_status()
        star_systems_resp.raise_for_status()
        orbits_resp.raise_for_status()
        terminals_resp.raise_for_status()

        uex_cache = UexReferenceCache(
            fetched_at=datetime.now(timezone.utc),
            commodities=[CachedCommodity.model_validate(row) for row in commodities_resp.json()["data"]],
            star_systems=[CachedStarSystem.model_validate(row) for row in star_systems_resp.json()["data"]],
            orbits=[CachedOrbit.model_validate(row) for row in orbits_resp.json()["data"]],
            terminals=[CachedTerminal.model_validate(row) for row in terminals_resp.json()["data"]],
        )

        self._uex_cache = uex_cache

        return uex_cache

    @traceable(name="uex_get_commodity_prices")
    async def get_commodity_prices(self, commodity_id: int) -> list[dict]:
        response = await asyncio.to_thread(
            requests.get,
            self.API_BASE_URL + 'commodities_prices',
            params={"id_commodity": commodity_id},
            headers=self.get_header(),
        )
        response.raise_for_status()
        return response.json()["data"]

    def get_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}
