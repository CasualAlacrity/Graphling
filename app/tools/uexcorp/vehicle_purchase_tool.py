from typing import Any

from pydantic import BaseModel, Field

from tools.uexcorp.args import LocationArgs
from tools.uexcorp.trade_tool import TradePriceTool


class VehiclePurchaseArgs(LocationArgs):
    vehicle: str = Field(
        description="The name of the ship or ground vehicle to look up, e.g. '100i', 'Cutlass "
                    "Black', 'Freelancer'. May be misspelled or phonetically transcribed from "
                    "speech — pass through what the user said, don't try to correct it yourself."
    )


class VehiclePurchaseTool(TradePriceTool):
    name: str = "vehicle_purchase_lookup"
    description: str = (
        "Look up current purchase prices for a Star Citizen ship or ground vehicle across "
        "in-game sales terminals (not real-money pledge purchases). Returns cheapest_to_buy (the "
        "single best terminal to purchase from) — use it directly for 'cheapest'/'best price' "
        "questions rather than scanning all_results yourself. all_results lists every matching "
        "terminal, for when the user wants alternatives or a specific location instead of the "
        "single best option. Ships have no in-game resale market, so best_to_sell is always null "
        "for this tool — don't mention it."
    )
    args_schema: type[BaseModel] = VehiclePurchaseArgs
    progress_label: str = "UEX to check vehicle purchase prices"

    async def _arun(self, vehicle: str, star_system: str | None = None, orbit: str | None = None,
                    terminal: str | None = None, moon: str | None = None, near: str | None = None,
                    max_distance: float | None = None) -> dict[str, Any] | str:
        return await self._safe_run(self._lookup(
            vehicle, lambda cache: cache.vehicles, self.client.get_vehicle_purchase_prices, "vehicle",
            star_system=star_system, orbit=orbit, terminal=terminal, moon=moon,
            near=near, max_distance=max_distance,
        ))
