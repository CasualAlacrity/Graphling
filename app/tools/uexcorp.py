import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
from rapidfuzz import process

from uexcorp_reference_cache import UexReferenceCache, CachedCommodity, CachedStarSystem, CachedOrbit, CachedTerminal


class UEXCorpClient(BaseModel):
    API_BASE_URL: str = "https://api.uexcorp.uk/2.0/"
    api_key: str
    bearer_token: str
    _uex_cache: UexReferenceCache | None = PrivateAttr(default=None)

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



class CommodityPriceArgs(BaseModel):
    commodity: str = Field(
        description="The name of the commodity to look up, e.g. 'Iron', 'Laranite', 'Agricium'. "
                    "May be misspelled or phonetically transcribed from speech — pass through what "
                    "the user said, don't try to correct it yourself."
    )
    star_system: str | None = Field(
        default=None,
        description="The star system to narrow the search to, e.g. 'Stanton' or 'Pyro'. "
                    "Leave unset if the user didn't mention one."
    )
    orbit: str | None = Field(
        default=None,
        description="The planet or moon to narrow the search to, e.g. 'microTech', 'Yela', 'Hurston'. "
                    "This is what players usually mean when they say 'planet' or 'moon'."
    )
    terminal: str | None = Field(
        default=None,
        description="An exact trading terminal/location name, e.g. 'Area18', 'Port Tressler', "
                    "'Ambitious Dream Refueling'. Only set this if the user named a specific location."
    )


class CommodityPriceTool(BaseTool):
    name: str = "commodity_price_lookup"
    description: str = (
        "Look up current buy and sell prices for a Star Citizen trade commodities and their "
        "associated terminals. Returns one row per terminal that trades it, including star system, "
        "planet/moon, and terminal name, alongside price_buy (cost to purchase) and price_sell "
        "(payout when selling to that terminal). A broad query with no system/orbit/terminal may "
        "return many rows — reason over them yourself to answer the user (e.g. best price, price "
        "in a given system) instead of calling this tool again for the same commodity."
    )
    args_schema: type[BaseModel] = CommodityPriceArgs
    client: UEXCorpClient

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("CommodityPriceTool only supports async execution — use _arun.")

    async def _arun(
            self,
            commodity: str,
            star_system: str | None = None,
            orbit: str | None = None,
            terminal: str | None = None,
    ) -> list[dict] | str:
        cache = await self.client.get_uex_cache()

        commodity_match = process.extractOne(
            commodity, [c.name for c in cache.commodities], score_cutoff=60
        )
        if commodity_match is None:
            return f"No commodity matching '{commodity}' was found."

        matched_commodity = cache.commodities[commodity_match[2]]
        rows = await self.client.get_commodity_prices(matched_commodity.id)

        if star_system:
            match = process.extractOne(
                star_system, [s.name for s in cache.star_systems], score_cutoff=60
            )
            if match:
                rows = [r for r in rows if r["star_system_name"] == match[0]]

        if orbit:
            match = process.extractOne(orbit, [o.name for o in cache.orbits], score_cutoff=60)
            if match:
                rows = [r for r in rows if r["orbit_name"] == match[0]]

        if terminal:
            match = process.extractOne(terminal, [t.name for t in cache.terminals], score_cutoff=60)
            if match:
                rows = [r for r in rows if r["terminal_name"] == match[0]]

        return rows
