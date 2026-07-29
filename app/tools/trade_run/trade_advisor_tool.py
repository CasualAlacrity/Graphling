from typing import Any

from pydantic import BaseModel, Field

from db import trade_run_store
from db.models import LegType
from tools.cargo_packing import (
    best_container_mix,
    estimate_transfer_time,
    estimated_profit,
    parse_container_sizes,
    reachable_scu,
    usable_container_sizes,
)
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousRunError
from tools.travel_time import estimate_travel_time
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import LOW_CONFIDENCE_MAX, match_by_name_or_code, match_by_name_or_code_with_score
from tools.uexcorp.trade_data import UEXTradeRoute
from tools.uplink_tool import UplinkTool


class TradeAdvisorArgs(BaseModel):
    commodity: str | None = Field(
        default=None,
        description="The commodity to check alternative destinations for. Leave unset "
                    "to use the active run's own commodity."
    )
    ship: str | None = Field(
        default=None,
        description="The ship being flown. Leave unset to use the active run's own ship."
    )


class TradeAdvisorTool(UplinkTool):
    name: str = "trade_advisor"
    description: str = (
        "Compare the pilot's committed trade run against alternative selling "
        "destinations for the same commodity, ranked by profit per hour (not raw "
        "profit) — call this when the pilot asks something like 'should I keep going "
        "to Pyro, or was there a better option', 'is this still the best place to "
        "sell', or 'was there a better destination'. Reports the comparison and the "
        "reasoning behind it; it never tells the pilot what to do beyond that — e.g. "
        "it will never weigh in on whether to wait at a station for demand to refresh "
        "versus moving on, since that's the pilot's own judgment call to make, not a "
        "number this can compute. Only works for an active trade run."
    )
    args_schema: type[BaseModel] = TradeAdvisorArgs
    progress_label: str = "Comparing this run against alternative destinations."
    uex_client: UEXCorpClient
    scw_client: StarCitizenWikiClient

    async def _arun(self, commodity: str | None = None, ship: str | None = None, *args: Any, **kwargs: Any) -> Any:
        try:
            run = await resolver.resolve_run()
        except (ValueError, AmbiguousRunError):
            run = None

        if run is None:
            return "This only works for an active trade run — start one first."

        if commodity is None:
            active_leg = trade_run_store.current_leg(run)
            if active_leg is not None:
                commodity = active_leg.commodity_name
        if ship is None:
            ship = run.ship

        if commodity is None:
            return "Which commodity do you want me to check?"
        if ship is None:
            return "Which ship are you flying?"

        return await self._safe_run(self._compute_recommendation(run, commodity, ship))

    async def _compute_recommendation(self, run, commodity: str, ship: str) -> str:
        cache = await self.uex_client.get_uex_cache()

        matched_commodity = match_by_name_or_code(commodity, cache.commodities)
        if matched_commodity is None:
            return f"Couldn't find a commodity matching '{commodity}'."

        matched_ship = match_by_name_or_code_with_score(ship, cache.vehicles)
        if matched_ship is None:
            return f"Couldn't find a ship matching '{ship}' in the UEX vehicle catalog."
        matched_vehicle, ship_score = matched_ship
        if ship_score < LOW_CONFIDENCE_MAX:
            return (
                f"Not sure which ship you meant by '{ship}' — closest match is the "
                f"{matched_vehicle.name}. Confirm and I'll check again."
            )

        acquisition_leg = next((leg for leg in run.legs if leg.leg_type == LegType.ACQUISITION), None)
        sale_leg = next((leg for leg in run.legs if leg.leg_type == LegType.SALE), None)
        if acquisition_leg is None or sale_leg is None:
            return "This run doesn't have both legs set up yet."

        container_sizes = parse_container_sizes(run.usable_container_sizes)
        committed_mix = best_container_mix(matched_vehicle.scu, container_sizes)
        committed_transfer_seconds = 2 * estimate_transfer_time(committed_mix)

        committed_travel = await estimate_travel_time(
            self.uex_client, self.scw_client, acquisition_leg.terminal_name, sale_leg.terminal_name, ship
        )
        if isinstance(committed_travel, str):
            return committed_travel

        committed_total_time = committed_transfer_seconds + committed_travel
        committed_profit = trade_run_store.run_profit(run)
        committed_score = committed_profit / committed_total_time if committed_total_time > 0 else 0.0

        raw_routes = await self.uex_client.get_commodity_routes(
            commodity_id=matched_commodity.id, origin_terminal_id=acquisition_leg.terminal_id,
        )
        candidates = [UEXTradeRoute.model_validate(row) for row in raw_routes]

        best_alt: UEXTradeRoute | None = None
        best_alt_score = None
        for route in candidates:
            if route.destination_terminal_name == sale_leg.terminal_name:
                continue  # the committed choice itself, not an alternative

            scu = reachable_scu(route, int(matched_vehicle.scu))
            if scu <= 0:
                continue

            mix = best_container_mix(
                scu, usable_container_sizes(route.container_sizes_origin, route.container_sizes_destination)
            )
            transfer_seconds = 2 * estimate_transfer_time(mix)

            travel = await estimate_travel_time(
                self.uex_client, self.scw_client, route.origin_terminal_name, route.destination_terminal_name, ship
            )
            if isinstance(travel, str):
                continue  # can't estimate this candidate (e.g. cross-system) — skip, don't guess

            total_time = transfer_seconds + travel
            if total_time <= 0:
                continue
            score = estimated_profit(route, scu) / total_time

            if best_alt_score is None or score > best_alt_score:
                best_alt, best_alt_score = route, score

        if best_alt is None or best_alt_score <= committed_score:
            return (
                f"By profit per hour, {sale_leg.terminal_name} is still the best call — "
                f"about {committed_score * 3600:.0f} aUEC/hour."
            )

        terminal_kind = "a ground station" if best_alt.is_on_ground_destination else "an orbital/space station"
        return (
            f"By profit per hour, {best_alt.destination_terminal_name} beats {sale_leg.terminal_name} — "
            f"about {best_alt_score * 3600:.0f} aUEC/hour versus {committed_score * 3600:.0f} aUEC/hour. "
            f"It's {terminal_kind}."
        )
