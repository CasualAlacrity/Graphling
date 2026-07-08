import logging
from operator import attrgetter
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, field_validator

from tools.uexcorp_client import UEXCorpClient
from tools.uexcorp_matching import filter_by_match, match_by_name_or_code

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
        description="The planet to narrow the search to, e.g. 'microTech', 'Hurston'. "
                    "This is what players usually mean when they say 'planet'."
    )
    terminal: str | None = Field(
        default=None,
        description="An exact trading terminal/location name, e.g. 'Area18', 'Port Tressler', "
                    "'Ambitious Dream Refueling'. Only set this if the user named a specific location."
    )
    moon: str | None = Field(
        default=None,
        description="The moon to narrow the search to, e.g. 'Yela', 'Daymar'. "
                    "This is what players usually mean when they say 'moon'. "
                    "The player may mistakenly refer to these celestial bodies as 'planets'."
    )


class CommodityPriceTool(BaseTool):
    name: str = "commodity_price_lookup"
    description: str = (
        "Look up current buy and sell prices for a Star Citizen trade commodity across its "
        "associated terminals. Returns cheapest_to_buy (the single best terminal to purchase from) "
        "and best_to_sell (the single best terminal to sell to) — these are already computed for "
        "you, use them directly for 'best'/'cheapest'/'highest' questions rather than scanning "
        "all_results yourself, since manually comparing many rows is error-prone. all_results lists "
        "every matching terminal, for when the user wants alternatives or a specific location "
        "instead of the single best option. A broad query with no system/orbit/terminal may return "
        "many rows in all_results — reuse this same result for a follow-up narrowing question "
        "(e.g. 'what about in a specific system') instead of calling this tool again."
    )
    args_schema: type[BaseModel] = CommodityPriceArgs
    client: UEXCorpClient

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("CommodityPriceTool only supports async execution — use _arun.")

    async def _arun(self, commodity: str, star_system: str | None = None, orbit: str | None = None,
                    terminal: str | None = None, moon: str | None = None
                    ) -> dict[str, Any] | str:
        try:
            cache = await self.client.get_uex_cache()

            matched_commodity = match_by_name_or_code(commodity, cache.commodities)
            if matched_commodity is None:
                return f"No commodity matching '{commodity}' was found."

            rows = [CommodityTradeData.model_validate(row) for row in
                    await self.client.get_commodity_prices(matched_commodity.id)]

            rows = filter_by_match(rows, star_system, cache.star_systems, "star_system_name")
            rows = filter_by_match(rows, orbit, cache.orbits, "orbit_name")
            rows = filter_by_match(rows, terminal, cache.terminals, "terminal_name")
            rows = filter_by_match(rows, moon, cache.moons, "moon_name")

            buy_rows = [r for r in rows if r.price_you_pay_to_acquire is not None]
            sell_rows = [r for r in rows if r.price_you_receive_when_selling is not None]

            cheapest_to_buy = min(buy_rows, key=attrgetter("price_you_pay_to_acquire"), default=None)
            best_to_sell = max(sell_rows, key=attrgetter("price_you_receive_when_selling"), default=None)

            return {
                "cheapest_to_buy": cheapest_to_buy.model_dump(exclude_none=True) if cheapest_to_buy else None,
                "best_to_sell": best_to_sell.model_dump(exclude_none=True) if best_to_sell else None,
                "all_results": [r.model_dump(exclude_none=True) for r in rows],
            }
        except Exception:
            logger.exception("commodity_price_lookup failed")
            return "The UEX pricing API is temporarily unavailable. Tell the user their request couldn't be completed and suggest trying again shortly."
