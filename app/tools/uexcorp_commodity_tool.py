import logging
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, field_validator
from rapidfuzz import process

from tools.uexcorp_client import UEXCorpClient

logger = logging.getLogger(__name__)


class CommodityTradeData(BaseModel):
    terminal_name: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None
    price_you_pay_to_acquire: float | None = Field(validation_alias="price_buy")
    price_you_receive_when_selling: float | None = Field(validation_alias="price_sell")

    @field_validator("price_you_pay_to_acquire", "price_you_receive_when_selling")
    @classmethod
    def zero_means_not_offered(cls, value: float) -> float | None:
        return None if value == 0 else value


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
        "associated terminals. Returns one row per terminal that trades it, including star system, "
        "planet/moon, and terminal name, alongside price_you_pay_to_acquire (per SCU) and "
        "price_you_receive_when_selling (per SCU). A broad query with no system/orbit/terminal may "
        "return many rows — reason over them yourself to answer the user (e.g. best price, price "
        "in a given system) instead of calling this tool again for the same commodity."
    )
    args_schema: type[BaseModel] = CommodityPriceArgs
    client: UEXCorpClient

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("CommodityPriceTool only supports async execution — use _arun.")

    async def _arun(self, commodity: str, star_system: str | None = None, orbit: str | None = None,
                    terminal: str | None = None,
                    ) -> list[dict] | str:
        try:
            cache = await self.client.get_uex_cache()

            commodity_match = process.extractOne(
                commodity, [c.name for c in cache.commodities], score_cutoff=60
            )
            if commodity_match is None:
                return f"No commodity matching '{commodity}' was found."

            matched_commodity = cache.commodities[commodity_match[2]]
            rows = [CommodityTradeData.model_validate(row) for row in
                    await self.client.get_commodity_prices(matched_commodity.id)]

            if star_system:
                match = process.extractOne(
                    star_system, [s.name for s in cache.star_systems], score_cutoff=60
                )
                if match:
                    rows = [r for r in rows if r.star_system_name == match[0]]

            if orbit:
                match = process.extractOne(orbit, [o.name for o in cache.orbits], score_cutoff=60)
                if match:
                    rows = [r for r in rows if r.orbit_name == match[0]]

            if terminal:
                match = process.extractOne(terminal, [t.name for t in cache.terminals], score_cutoff=60)
                if match:
                    rows = [r for r in rows if r.terminal_name == match[0]]

            return [r.model_dump(exclude_none=True) for r in rows]
        except Exception:
            logger.exception("commodity_price_lookup failed")
            return "The UEX pricing API is temporarily unavailable. Tell the user their request couldn't be completed and suggest trying again shortly."
