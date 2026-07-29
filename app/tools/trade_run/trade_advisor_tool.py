from typing import Any

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from db import trade_run_store
from db.models import LegType
from tools.cargo_packing import best_container_mix, estimate_transfer_time, parse_container_sizes
from tools.route_ranking import find_best_route, profit_per_hour
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousRunError
from tools.travel_time import estimate_travel_time
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import resolve_or_hedge
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

        matched_commodity, error = resolve_or_hedge(commodity, cache.commodities, "commodity")
        if error:
            return error

        matched_vehicle, error = resolve_or_hedge(ship, cache.vehicles, "ship", scorer=fuzz.token_sort_ratio)
        if error:
            return error

        acquisition_leg = next((leg for leg in run.legs if leg.leg_type == LegType.ACQUISITION), None)
        sale_leg = next((leg for leg in run.legs if leg.leg_type == LegType.SALE), None)
        if acquisition_leg is None or sale_leg is None:
            return "This run doesn't have both legs set up yet."

        container_sizes = parse_container_sizes(run.usable_container_sizes)
        committed_mix = best_container_mix(matched_vehicle.scu, container_sizes)
        committed_transfer_seconds = 2 * estimate_transfer_time(committed_mix)

        # A cross-system committed leg means its own time can't be estimated, but that's
        # no reason to give up on the whole comparison — same-origin alternatives may
        # well be in-system and answerable. committed_score stays None in that case
        # rather than short-circuiting the alternative search below.
        committed_travel = await estimate_travel_time(
            self.uex_client, self.scw_client, acquisition_leg.terminal_name, sale_leg.terminal_name, ship
        )
        committed_score = None
        if isinstance(committed_travel, float):
            committed_total_time = committed_transfer_seconds + committed_travel
            committed_profit = trade_run_store.run_profit(run)
            committed_score = committed_profit / committed_total_time if committed_total_time > 0 else 0.0

        result = await find_best_route(
            self.uex_client, self.scw_client, acquisition_leg.terminal_id, ship, matched_vehicle.scu, cache,
            commodity_id=matched_commodity.id, exclude_destination_terminal_name=sale_leg.terminal_name,
        )
        best_alt, best_alt_score, best_alt_scu = result if result is not None else (None, None, None)

        if committed_score is None:
            if best_alt is None:
                return (
                    f"Can't estimate your committed route's time to {sale_leg.terminal_name} — "
                    "it crosses star systems — and no in-system alternative turned up for "
                    f"{matched_commodity.name} from {acquisition_leg.terminal_name}."
                )
            terminal_kind = "a ground station" if best_alt.is_on_ground_destination else "an orbital/space station"
            return (
                f"Can't estimate your committed route's time to {sale_leg.terminal_name} — it "
                f"crosses star systems. In the {matched_vehicle.name}, by profit per hour, the "
                f"best in-system option from {acquisition_leg.terminal_name} is "
                f"{best_alt_scu:.0f} SCU of {best_alt.commodity_name} to "
                f"{best_alt.destination_terminal_name} — about {profit_per_hour(best_alt_score):,} "
                f"aUEC/hour. It's {terminal_kind}."
            )

        if best_alt is None or best_alt_score <= committed_score:
            return (
                f"By profit per hour, {sale_leg.terminal_name} is still the best call — "
                f"about {profit_per_hour(committed_score):,} aUEC/hour."
            )

        terminal_kind = "a ground station" if best_alt.is_on_ground_destination else "an orbital/space station"
        return (
            f"In the {matched_vehicle.name}, by profit per hour, {best_alt_scu:.0f} SCU of "
            f"{best_alt.commodity_name} to {best_alt.destination_terminal_name} beats "
            f"{sale_leg.terminal_name} — about {profit_per_hour(best_alt_score):,} aUEC/hour versus "
            f"{profit_per_hour(committed_score):,} aUEC/hour. It's {terminal_kind}."
        )
