from collections.abc import Callable
from operator import attrgetter
from typing import Any

from rapidfuzz import fuzz

from tools.uexcorp.location_phrase import RAW_LOCATION_FIELDS, location_phrase, terminals_by_id
from tools.uexcorp.matching import filter_by_location, resolve_or_hedge
from tools.uexcorp.reference_cache import UexReferenceCache
from tools.uexcorp.trade_data import UEXTradeData
from tools.uplink_tool import UEXBackedTool


# UEX's commodities_status table, buy side: code 1 is "Out of Stock (Empty)", 7 is full. A
# terminal in that state has the lowest-looking price and nothing to sell, so it can't be
# "the cheapest place to buy".
OUT_OF_STOCK_STATUS = 1


class TradePriceTool(UEXBackedTool):

    async def _lookup(self, query: str, catalog_selector: Callable[[UexReferenceCache], list],
                      fetch_prices: Callable[[int], Any], not_found_label: str,
                      star_system: str | None = None, orbit: str | None = None,
                      terminal: str | None = None, moon: str | None = None,
                      near: str | None = None, max_distance: float | None = None,
                      scorer: Any = fuzz.WRatio, skip_out_of_stock: bool = False,
                      ) -> dict[str, Any] | str:
        cache = await self.client.get_uex_cache()

        matched, error = resolve_or_hedge(query, catalog_selector(cache), not_found_label, scorer=scorer)
        if error:
            return error

        rows = [UEXTradeData.model_validate(row) for row in await fetch_prices(matched.id)]
        rows = await filter_by_location(
            rows, cache, self.client, star_system, orbit, terminal, moon, near, max_distance,
        )

        # Opt-in: only commodity rows carry a real inventory status. Vehicle/item rows
        # don't, and a status_buy there doesn't mean stock.
        buy_rows = [
            r for r in rows
            if r.price_you_pay_to_acquire is not None
            and not (skip_out_of_stock and r.status_buy == OUT_OF_STOCK_STATUS)
        ]
        sell_rows = [r for r in rows if r.price_you_receive_when_selling is not None]

        cheapest_to_buy = min(buy_rows, key=attrgetter("price_you_pay_to_acquire"), default=None)
        best_to_sell = max(sell_rows, key=attrgetter("price_you_receive_when_selling"), default=None)

        terminals = terminals_by_id(cache.terminals)

        def row_dict(row: UEXTradeData) -> dict[str, Any]:
            data = row.model_dump(exclude_none=True, exclude=RAW_LOCATION_FIELDS)
            data["location"] = location_phrase(terminals.get(row.terminal_id), row.terminal_name)
            # An explicit boolean, not just a status code the model has to know how to read
            # -- it was still picking the cheapest-priced row out of all_results now and then.
            if skip_out_of_stock and row.price_you_pay_to_acquire is not None:
                data["in_stock"] = row.status_buy != OUT_OF_STOCK_STATUS
            return data

        return {
            "cheapest_to_buy": row_dict(cheapest_to_buy) if cheapest_to_buy else None,
            "best_to_sell": row_dict(best_to_sell) if best_to_sell else None,
            "all_results": [row_dict(r) for r in rows],
        }
