import logging
from operator import attrgetter
from typing import Any, Callable

from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import DEFAULT_NEAR_DISTANCE, filter_by_match, match_by_name_or_code, filter_by_distance
from tools.uexcorp.reference_cache import UexReferenceCache
from tools.uexcorp.trade_data import UEXTradeData
from tools.uplink_tool import UplinkTool

logger = logging.getLogger(__name__)


class TradePriceTool(UplinkTool):

    client: UEXCorpClient

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{type(self).__name__} only supports async execution — use _arun.")

    async def _lookup(self, query: str, catalog_selector: Callable[[UexReferenceCache], list],
                      fetch_prices: Callable[[int], Any], not_found_label: str,
                      star_system: str | None = None, orbit: str | None = None,
                      terminal: str | None = None, moon: str | None = None,
                      near: str | None = None, max_distance: float | None = None,
                      ) -> dict[str, Any] | str:
        try:
            cache = await self.client.get_uex_cache()

            matched = match_by_name_or_code(query, catalog_selector(cache))
            if matched is None:
                return f"No {not_found_label} matching '{query}' was found."

            rows = [UEXTradeData.model_validate(row) for row in await fetch_prices(matched.id)]

            rows = filter_by_match(rows, star_system, cache.star_systems, "star_system_name")
            rows = filter_by_match(rows, orbit, cache.orbits, "orbit_name")
            rows = filter_by_match(rows, terminal, cache.terminals, "terminal_name")
            rows = filter_by_match(rows, moon, cache.moons, "moon_name")

            if near:
                effective_max_distance = max_distance if max_distance is not None else DEFAULT_NEAR_DISTANCE
                rows = await filter_by_distance(rows, near, effective_max_distance, cache, self.client)

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
            logger.exception(f"{self.name} failed")
            return "The UEX pricing API is temporarily unavailable. Tell the user their request couldn't be completed and suggest trying again shortly."
