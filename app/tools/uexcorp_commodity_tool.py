import logging
from typing import Any

from pydantic import BaseModel, Field

from tools.uex_trade_tool import TradePriceTool
from tools.uexcorp_args import LocationArgs

logger = logging.getLogger(__name__)


class CommodityPriceArgs(LocationArgs):
    commodity: str = Field(
        description="The name of the commodity to look up, e.g. 'Iron', 'Laranite', 'Agricium'. "
                    "May be misspelled or phonetically transcribed from speech — pass through what "
                    "the user said, don't try to correct it yourself."
    )


class CommodityPriceTool(TradePriceTool):
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

    async def _arun(self, commodity: str, star_system: str | None = None, orbit: str | None = None,
                    terminal: str | None = None, moon: str | None = None, near: str | None = None,
                    max_distance: float | None = None) -> dict[str, Any] | str:
        return await self._lookup(
            commodity, lambda cache: cache.commodities, self.client.get_commodity_prices, "commodity",
            star_system=star_system, orbit=orbit, terminal=terminal, moon=moon,
            near=near, max_distance=max_distance,
        )
