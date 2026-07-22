from collections.abc import Callable
from operator import attrgetter
from typing import Any

from tools.uexcorp.matching import filter_by_location, match_by_name_or_code
from tools.uexcorp.reference_cache import UexReferenceCache
from tools.uexcorp.trade_data import UEXTradeData
from tools.uplink_tool import UEXBackedTool


class TradePriceTool(UEXBackedTool):

    async def _lookup(self, query: str, catalog_selector: Callable[[UexReferenceCache], list],
                      fetch_prices: Callable[[int], Any], not_found_label: str,
                      star_system: str | None = None, orbit: str | None = None,
                      terminal: str | None = None, moon: str | None = None,
                      near: str | None = None, max_distance: float | None = None,
                      ) -> dict[str, Any] | str:
        cache = await self.client.get_uex_cache()

        matched = match_by_name_or_code(query, catalog_selector(cache))
        if matched is None:
            return f"No {not_found_label} matching '{query}' was found."

        rows = [UEXTradeData.model_validate(row) for row in await fetch_prices(matched.id)]
        rows = await filter_by_location(
            rows, cache, self.client, star_system, orbit, terminal, moon, near, max_distance,
        )

        buy_rows = [r for r in rows if r.price_you_pay_to_acquire is not None]
        sell_rows = [r for r in rows if r.price_you_receive_when_selling is not None]

        cheapest_to_buy = min(buy_rows, key=attrgetter("price_you_pay_to_acquire"), default=None)
        best_to_sell = max(sell_rows, key=attrgetter("price_you_receive_when_selling"), default=None)

        return {
            "cheapest_to_buy": cheapest_to_buy.model_dump(exclude_none=True) if cheapest_to_buy else None,
            "best_to_sell": best_to_sell.model_dump(exclude_none=True) if best_to_sell else None,
            "all_results": [r.model_dump(exclude_none=True) for r in rows],
        }
