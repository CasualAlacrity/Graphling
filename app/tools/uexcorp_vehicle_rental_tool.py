import logging
from operator import attrgetter
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from tools.uexcorp_args import LocationArgs
from tools.uexcorp_client import UEXCorpClient
from tools.uexcorp_matching import DEFAULT_NEAR_DISTANCE, filter_by_match, match_by_name_or_code, filter_by_distance

logger = logging.getLogger(__name__)


class VehicleRentalData(BaseModel):
    terminal_name: str
    star_system_name: str | None
    orbit_name: str | None
    moon_name: str | None
    planet_name: str | None
    price_per_day: float = Field(validation_alias="price_rent")


class VehicleRentalArgs(LocationArgs):
    vehicle: str = Field(
        description="The name of the ship or ground vehicle to look up, e.g. '100i', 'Cutlass "
                    "Black', 'Freelancer'. May be misspelled or phonetically transcribed from "
                    "speech — pass through what the user said, don't try to correct it yourself."
    )


class VehicleRentalTool(BaseTool):
    name: str = "vehicle_rental_lookup"
    description: str = (
        "Look up current daily rental rates for a Star Citizen ship or ground vehicle across "
        "rental terminals. Returns cheapest_rental (the single best terminal to rent from) — use "
        "it directly for 'cheapest'/'best price' questions rather than scanning all_results "
        "yourself. all_results lists every matching terminal, for when the user wants alternatives "
        "or a specific location instead of the single best option."
    )
    args_schema: type[BaseModel] = VehicleRentalArgs
    client: UEXCorpClient

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("VehicleRentalTool only supports async execution — use _arun.")

    async def _arun(self, vehicle: str, star_system: str | None = None, orbit: str | None = None,
                    terminal: str | None = None, moon: str | None = None, near: str | None = None,
                    max_distance: float | None = None) -> dict[str, Any] | str:
        try:
            cache = await self.client.get_uex_cache()

            matched_vehicle = match_by_name_or_code(vehicle, cache.vehicles)
            if matched_vehicle is None:
                return f"No vehicle matching '{vehicle}' was found."

            rows = [VehicleRentalData.model_validate(row) for row in
                    await self.client.get_vehicle_rental_prices(matched_vehicle.id)]

            rows = filter_by_match(rows, star_system, cache.star_systems, "star_system_name")
            rows = filter_by_match(rows, orbit, cache.orbits, "orbit_name")
            rows = filter_by_match(rows, terminal, cache.terminals, "terminal_name")
            rows = filter_by_match(rows, moon, cache.moons, "moon_name")

            if near:
                effective_max_distance = max_distance if max_distance is not None else DEFAULT_NEAR_DISTANCE
                rows = await filter_by_distance(rows, near, effective_max_distance, cache, self.client)

            cheapest_rental = min(rows, key=attrgetter("price_per_day"), default=None)

            return {
                "cheapest_rental": cheapest_rental.model_dump(exclude_none=True) if cheapest_rental else None,
                "all_results": [r.model_dump(exclude_none=True) for r in rows],
            }
        except Exception:
            logger.exception("vehicle_rental_lookup failed")
            return "The UEX pricing API is temporarily unavailable. Tell the user their request couldn't be completed and suggest trying again shortly."
