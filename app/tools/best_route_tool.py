from typing import Any

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from tools.route_ranking import find_best_route, profit_per_hour
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousRunError
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import match_by_name_or_code, resolve_or_hedge
from tools.uplink_tool import UplinkTool


class BestRouteArgs(BaseModel):
    origin: str = Field(description="Where the pilot currently is, e.g. 'Seraphim'.")
    ship: str | None = Field(
        default=None,
        description="The ship being flown, if named. Leave unset to fall back to an "
                    "active trade run's ship, if there is one."
    )
    commodity: str | None = Field(
        default=None,
        description="Narrow the search to one commodity, if the pilot named one. Leave "
                    "unset to search every commodity sellable from the origin."
    )
    exclude_ground_stations: bool = Field(
        default=False,
        description="Set true if the pilot asks to exclude, skip, or avoid ground "
                    "stations, or wants space/orbital stations only."
    )
    require_autoload: bool = Field(
        default=False,
        description="Set true if the pilot asks for auto-load-only, or wants to avoid "
                    "manually unloading cargo at the destination."
    )


class BestRouteTool(UplinkTool):
    name: str = "best_route"
    description: str = (
        "Find the best trade route to take right now from a given location — call this "
        "when the pilot wants a fresh route recommendation, e.g. 'what's the best route "
        "from Seraphim', 'what should I haul out of here', 'find me a route in my "
        "Railen'. This is not about whether an existing committed run is still the best "
        "call — use trade_advisor for that instead. Doesn't require an active trade run. "
        "Searches routes matching the ship and origin (and commodity, if named), ranks "
        "by profit per hour, and reports the best one found."
    )
    args_schema: type[BaseModel] = BestRouteArgs
    progress_label: str = "Searching routes from your location."
    uex_client: UEXCorpClient
    scw_client: StarCitizenWikiClient

    async def _arun(
            self, origin: str, ship: str | None = None, commodity: str | None = None,
            exclude_ground_stations: bool = False, require_autoload: bool = False,
            *args: Any, **kwargs: Any,
    ) -> Any:
        if ship is None:
            try:
                run = await resolver.resolve_run()
            except (ValueError, AmbiguousRunError):
                run = None
            if run is not None:
                ship = run.ship

        if ship is None:
            return "Which ship are you flying?"

        return await self._safe_run(
            self._find_and_report(origin, ship, commodity, exclude_ground_stations, require_autoload)
        )

    async def _find_and_report(
            self, origin: str, ship: str, commodity: str | None,
            exclude_ground_stations: bool = False, require_autoload: bool = False,
    ) -> str:
        cache = await self.uex_client.get_uex_cache()

        origin_terminal, error = resolve_or_hedge(origin, cache.terminals, "location")
        if error:
            return error

        vehicle, error = resolve_or_hedge(ship, cache.vehicles, "ship", scorer=fuzz.token_sort_ratio)
        if error:
            return error

        commodity_id = None
        if commodity is not None:
            matched_commodity = match_by_name_or_code(commodity, cache.commodities)
            if matched_commodity is None:
                return f"Couldn't find a commodity matching '{commodity}'."
            commodity_id = matched_commodity.id

        result = await find_best_route(
            self.uex_client, self.scw_client, origin_terminal.id, ship, vehicle.scu, cache,
            commodity_id=commodity_id, exclude_ground=exclude_ground_stations, require_autoload=require_autoload,
        )
        if result is None:
            qualifiers = []
            if exclude_ground_stations:
                qualifiers.append("excluding ground stations")
            if require_autoload:
                qualifiers.append("auto-load only")
            suffix = f" ({', '.join(qualifiers)})" if qualifiers else ""
            return f"No usable in-system route turned up from {origin_terminal.name}{suffix}."

        best, score, scu = result
        terminal_kind = "a ground station" if best.is_on_ground_destination else "an orbital/space station"
        return (
            f"Best from {origin_terminal.name} in the {vehicle.name}: {scu:.0f} SCU of "
            f"{best.commodity_name} to {best.destination_terminal_name} — about "
            f"{profit_per_hour(score):,} aUEC/hour. It's {terminal_kind}."
        )
