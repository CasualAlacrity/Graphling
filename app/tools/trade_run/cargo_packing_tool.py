from typing import Any

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from db.models import LegType
from tools.cargo_packing import best_container_mix, parse_container_sizes
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousRunError
from tools.uexcorp.matching import match_by_name_or_code
from tools.uexcorp.reference_cache import UexReferenceCache
from tools.uplink_tool import UEXBackedTool


class CargoPackingArgs(BaseModel):
    ship: str | None = Field(
        default=None,
        description="The pilot's ship name, only needed to disambiguate if they have "
                    "more than one active trade run right now. Leave unset otherwise."
    )


class CargoPackingTool(UEXBackedTool):
    name: str = "cargo_packing_suggestion"
    description: str = (
        "Suggest which cargo container sizes the pilot should buy to best fill their "
        "ship's hold for their current, active trade run — call this when they ask "
        "something like 'what SCU crates should I buy', 'how should I load my hold', "
        "or 'what's the best container mix'. Only works for a run that already has a "
        "ship assigned; if the run has no ship, or the ship can't be identified, this "
        "will fail and tell you so instead of guessing."
    )
    args_schema: type[BaseModel] = CargoPackingArgs
    progress_label: str = "Working out the best container mix for the ship's hold."

    async def _arun(self, ship: str | None = None, *args: Any, **kwargs: Any) -> Any:
        try:
            run = await resolver.resolve_run(ship=ship)
        except ValueError as ve:
            return str(ve)
        except AmbiguousRunError as are:
            return str(are)

        if not run.ship:
            return "This run has no ship assigned yet, so I can't work out hold capacity."

        cache_result = await self._safe_run(self.client.get_uex_cache())
        if not isinstance(cache_result, UexReferenceCache):
            return cache_result

        matched_vehicle = match_by_name_or_code(run.ship, cache_result.vehicles, scorer=fuzz.token_sort_ratio)
        if matched_vehicle is None:
            return f"Couldn't find a ship matching '{run.ship}' in the UEX vehicle catalog."

        sizes = parse_container_sizes(run.usable_container_sizes)
        if not sizes:
            return "No known usable container sizes for this run's route, so I can't suggest a mix."

        acquisition_leg = next(leg for leg in run.legs if leg.leg_type == LegType.ACQUISITION)
        capacity = min(acquisition_leg.quantity_scu, matched_vehicle.scu)

        mix = best_container_mix(capacity, sizes)
        if not mix:
            return (f"Couldn't fit any of this route's usable container sizes into "
                    f"the {matched_vehicle.name_full}'s hold.")

        breakdown = " + ".join(f"{count}×{size}" for size, count in sorted(mix.items(), reverse=True))
        total = sum(size * count for size, count in mix.items())
        return (f"Best fill for the {matched_vehicle.name_full}: {breakdown} SCU "
                f"({total}/{matched_vehicle.scu:.0f} SCU)")
