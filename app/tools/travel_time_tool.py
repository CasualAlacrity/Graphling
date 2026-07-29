from typing import Any

from pydantic import BaseModel, Field

from db import trade_run_store
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousRunError
from tools.travel_time import estimate_travel_time
from tools.uexcorp.client import UEXCorpClient
from tools.uplink_tool import UplinkTool


class TravelTimeArgs(BaseModel):
    destination: str = Field(
        description="Where the pilot wants to know the travel time to, e.g. 'Orison', "
                    "'Seraphim', 'Baijini Point'."
    )
    origin: str | None = Field(
        default=None,
        description="Where the pilot is traveling from, if they said one. Leave unset "
                    "if they didn't — falls back to their active trade run's current "
                    "location when there is one."
    )
    ship: str | None = Field(
        default=None,
        description="The ship the pilot is asking about, if they named one. Leave unset "
                    "if they didn't — falls back to their active trade run's ship when "
                    "there is one."
    )


class TravelTimeTool(UplinkTool):
    name: str = "estimate_travel_time"
    description: str = (
        "Estimate how long it takes to fly somewhere by quantum travel — call this when "
        "the pilot asks something like 'how long to get to Orison', 'how far is "
        "Seraphim', or 'what's the ETA to Baijini Point'. Not specific to trade runs — "
        "usable for any travel-time question, though it'll use an active trade run's "
        "ship/location as a default when the pilot doesn't specify one. Only estimates "
        "travel within a single star system; says so plainly if the route crosses "
        "systems rather than guessing."
    )
    args_schema: type[BaseModel] = TravelTimeArgs
    progress_label: str = "Estimating travel time."
    uex_client: UEXCorpClient
    scw_client: StarCitizenWikiClient

    async def _arun(
            self, destination: str, origin: str | None = None, ship: str | None = None,
            *args: Any, **kwargs: Any,
    ) -> Any:
        if origin is None or ship is None:
            try:
                run = await resolver.resolve_run()
            except (ValueError, AmbiguousRunError):
                run = None

            if run is not None:
                if origin is None:
                    active_leg = trade_run_store.current_leg(run)
                    if active_leg is not None:
                        origin = active_leg.terminal_name
                if ship is None:
                    ship = run.ship

        if origin is None:
            return "I don't know where you're starting from — where are you traveling from?"
        if ship is None:
            return "I don't know which ship you're flying — which ship?"

        result = await self._safe_run(
            estimate_travel_time(self.uex_client, self.scw_client, origin, destination, ship)
        )
        if not isinstance(result, float):
            return result

        minutes, seconds = divmod(int(round(result)), 60)
        return f"About {minutes}:{seconds:02d} from {origin} to {destination} in the {ship}."
