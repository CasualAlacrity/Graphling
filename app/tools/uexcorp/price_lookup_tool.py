"""price_lookup: every buy / sell / rent price question in one tool, replacing the separate
commodity, item, vehicle-purchase and vehicle-rental lookups (docs/todo.md 6a, 2026-10-17).

"How much is a Hull A", "where can I buy a multitool", "who pays most for quant", "can I
rent a Cat near Lorville" all read the same thing: per-terminal price rows for an action.
The model passes what the pilot named and the action; catalog_resolution.py works out
which thing that is across all catalogues, so the model no longer guesses a catalogue
first (where Hull A went wrong every run), and the four tool descriptions (~3,100 tokens
on every reply) shrink to one.
"""
from operator import attrgetter
from typing import Any, Literal

from pydantic import BaseModel, Field

from tools.uexcorp import catalog_resolution
from tools.uexcorp.args import LocationArgs
from tools.uexcorp.location_phrase import (
    LOCATION_GUIDANCE,
    RAW_LOCATION_FIELDS,
    location_phrase,
    terminals_by_id,
)
from tools.uexcorp.matching import filter_by_location
from tools.uexcorp.trade_data import UEXTradeData, VehicleRentalData
from tools.uplink_tool import UEXBackedTool

# UEX's commodities_status table, buy side: 1 is "Out of Stock". The cheapest-looking
# terminal with nothing to sell can't be the best place to buy (2026-10-06).
OUT_OF_STOCK_STATUS = 1
# Alternatives kept for a follow-up ("what else?", "anywhere in Pyro?"); the answer itself
# is `best`. More would be tokens nobody hears.
MAX_OTHERS = 4
# Sold at more terminals than this and no place given: the useful answer is the nearest
# one, so ALICE should ask where the pilot is (Jeff's matrix note on the multitool).
MANY_TERMINALS = 10

_UNSPOKEN_FIELDS = {
    "buy": {"price_you_receive_when_selling", "status_buy", "status_sell"},
    "sell": {"price_you_pay_to_acquire", "status_buy", "status_sell"},
    "rent": set(),
}


class PriceLookupArgs(LocationArgs):
    what: str = Field(
        description="What the pilot named: a commodity, item, ship or vehicle, e.g. 'Laranite', 'quant', "
                    "'Pyro RYT multitool', 'Hull A', 'Cutlass Black', 'Cat'. Pass their words through as "
                    "heard, even misspelled; don't decide what kind of thing it is."
    )
    action: Literal["buy", "sell", "rent"] = Field(
        description="buy: the pilot wants to buy it or asks what it costs or where to get it. sell: they "
                    "want to sell it or ask who pays most. rent: they want to rent a vehicle."
    )


class PriceLookupTool(UEXBackedTool):
    name: str = "price_lookup"
    description: str = (
        "Buy, sell and rent prices for anything in Star Citizen: trade commodities, items, ships and "
        "vehicles. Use it for 'how much is X', 'where can I buy X', 'cheapest X', 'who pays the most "
        "for X', 'can I sell X', 'can I rent X'. Returns `best`, the one terminal to name (cheapest to "
        "buy or rent, highest paying to sell); answer with it alone. `others` are for when the pilot "
        "asks for alternatives. `understood_as` is what their words were taken to mean: say it when "
        "it isn't obvious ('The Hull A ship is ...'). If `ask_where` is set, the thing is sold in many "
        "places: give the best one and offer the nearest if they say where they are. For a narrower "
        "follow-up ('what about in Pyro?'), call again with the filter set."
    ) + LOCATION_GUIDANCE
    args_schema: type[BaseModel] = PriceLookupArgs
    progress_label: str = "UEX to check prices"

    async def _arun(self, what: str, action: str, star_system: str | None = None, orbit: str | None = None,
                    terminal: str | None = None, moon: str | None = None, near: str | None = None,
                    max_distance: float | None = None) -> dict[str, Any] | str:
        return await self._safe_run(self._lookup(
            what, action, star_system, orbit, terminal, moon, near, max_distance,
        ))

    async def _fetch_rows(self, candidate: catalog_resolution.Candidate, action: str) -> list[dict]:
        entity_id = candidate.entity.id
        if candidate.kind == catalog_resolution.COMMODITY:
            return await self.client.get_commodity_prices(entity_id)
        if candidate.kind == catalog_resolution.ITEM:
            return await self.client.get_item_prices(entity_id)
        if action == catalog_resolution.RENT:
            return await self.client.get_vehicle_rental_prices(entity_id)
        return await self.client.get_vehicle_purchase_prices(entity_id)

    async def _lookup(self, what, action, star_system, orbit, terminal, moon, near, max_distance):
        cache = await self.client.get_uex_cache()

        async def fetch(candidate):
            return await self._fetch_rows(candidate, action)

        resolution = await catalog_resolution.resolve(what, action, cache, fetch)
        if resolution.chosen is None:
            return resolution.message
        chosen = resolution.chosen

        if action == catalog_resolution.RENT:
            rows = [VehicleRentalData.model_validate(row) for row in chosen.rows]
        else:
            rows = [UEXTradeData.model_validate(row) for row in chosen.rows]
        rows = await filter_by_location(rows, cache, self.client, star_system, orbit, terminal, moon,
                                        near, max_distance)

        offering = self._offering(rows, action, chosen.kind)
        if not offering:
            return f"No terminal there {self._verb(action)} {chosen.name}."
        if action == catalog_resolution.SELL:
            offering.sort(key=attrgetter("price_you_receive_when_selling"), reverse=True)
        elif action == catalog_resolution.RENT:
            offering.sort(key=attrgetter("price_per_day"))
        else:
            offering.sort(key=attrgetter("price_you_pay_to_acquire"))

        terminals = terminals_by_id(cache.terminals)
        others = []
        for row in offering[1:1 + MAX_OTHERS]:
            others.append(self._row(row, terminals, action))
        result = {
            "what": chosen.name,
            "kind": chosen.kind,
            "price_unit": "aUEC per SCU" if chosen.kind == catalog_resolution.COMMODITY else "aUEC",
            "best": self._row(offering[0], terminals, action),
            "others": others,
            "terminal_count": len(offering),
        }
        if action == catalog_resolution.RENT:
            result["price_unit"] = "aUEC per day"
        if resolution.understood_as:
            result["understood_as"] = resolution.understood_as
        no_place_given = not any((star_system, orbit, terminal, moon, near))
        if no_place_given and len(offering) > MANY_TERMINALS:
            result["ask_where"] = True
        return result

    @staticmethod
    def _verb(action: str) -> str:
        return {"buy": "sells", "sell": "buys", "rent": "rents out"}[action]

    @staticmethod
    def _offering(rows: list, action: str, kind: str) -> list:
        offering = []
        for row in rows:
            if action == catalog_resolution.RENT:
                if row.price_per_day:
                    offering.append(row)
            elif action == catalog_resolution.SELL:
                if row.price_you_receive_when_selling is not None:
                    offering.append(row)
            elif row.price_you_pay_to_acquire is not None:
                # Only commodity rows carry real stock; a vehicle's status_buy isn't stock.
                if kind == catalog_resolution.COMMODITY and row.status_buy == OUT_OF_STOCK_STATUS:
                    continue
                offering.append(row)
        return offering

    @staticmethod
    def _row(row, terminals, action: str) -> dict:
        """A terminal as the model sees it: its place phrase and the price for this action
        only. The other direction's price and the raw status codes are left out: a buy
        answer carrying a sell price is noise, and invites mixing the two up."""
        data = row.model_dump(exclude_none=True, exclude=RAW_LOCATION_FIELDS | _UNSPOKEN_FIELDS[action])
        data["location"] = location_phrase(terminals.get(row.terminal_id), row.terminal_name)
        return data
