import logging
from typing import Any

from pydantic import BaseModel, Field

from tools.uex_trade_tool import TradePriceTool
from tools.uexcorp_args import LocationArgs

logger = logging.getLogger(__name__)


class ItemArgs(LocationArgs):
    item: str = Field(
        description="The name of the ship or personal item to look up, e.g. 'Trawler Scraper "
                    "Module', 'Multi-Tool'. May be misspelled or phonetically transcribed from "
                    "speech — pass through what the user said, don't try to correct it yourself."
    )


class ItemPriceTool(TradePriceTool):
    name: str = "item_price_lookup"
    description: str = (
        "Look up current buy and sell prices for a Star Citizen ship or personal item (weapons, "
        "armor, components, tools — not trade commodities) across its associated terminals. Returns "
        "cheapest_to_buy (the single best terminal to purchase from) and best_to_sell (the single "
        "best terminal to sell to) — these are already computed for you, use them directly for "
        "'best'/'cheapest'/'highest' questions rather than scanning all_results yourself, since "
        "manually comparing many rows is error-prone. all_results lists every matching terminal, "
        "for when the user wants alternatives or a specific location instead of the single best "
        "option."
    )
    args_schema: type[BaseModel] = ItemArgs

    async def _arun(self, item: str, star_system: str | None = None, orbit: str | None = None,
                    terminal: str | None = None, moon: str | None = None, near: str | None = None,
                    max_distance: float | None = None) -> dict[str, Any] | str:
        return await self._lookup(
            item, lambda cache: cache.items, self.client.get_item_prices, "item",
            star_system=star_system, orbit=orbit, terminal=terminal, moon=moon,
            near=near, max_distance=max_distance,
        )
