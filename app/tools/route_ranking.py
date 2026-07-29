from tools.cargo_packing import (
    best_container_mix,
    estimate_transfer_time,
    estimated_profit,
    reachable_scu,
    usable_container_sizes,
)
from tools.travel_time import estimate_travel_time
from tools.uexcorp.trade_data import UEXTradeRoute


async def find_best_route(
        uex_client, scw_client, origin_terminal_id: int, ship: str, ship_scu: float, *,
        commodity_id: int | None = None, exclude_destination_terminal_name: str | None = None,
        exclude_ground: bool = False,
) -> tuple[UEXTradeRoute, float] | None:
    """Searches commodity routes from origin_terminal_id — narrowed to one commodity if
    commodity_id is given, otherwise every commodity sellable from there — and ranks by
    profit per hour. Returns the best (route, score), or None if nothing qualifies (e.g.
    every candidate turned out cross-system, none reach a usable SCU amount, or
    exclude_ground filtered out everything that was left).

    Shared by trade_advisor (comparing against a committed leg, hence
    exclude_destination_terminal_name to skip the committed choice itself) and any tool
    that just wants "the best option from here" with no active run involved.
    """
    raw_routes = await uex_client.get_commodity_routes(
        commodity_id=commodity_id, origin_terminal_id=origin_terminal_id,
    )
    candidates = [UEXTradeRoute.model_validate(row) for row in raw_routes]

    best: UEXTradeRoute | None = None
    best_score = None
    for route in candidates:
        if exclude_destination_terminal_name and route.destination_terminal_name == exclude_destination_terminal_name:
            continue
        if exclude_ground and route.is_on_ground_destination:
            continue

        scu = reachable_scu(route, int(ship_scu))
        if scu <= 0:
            continue

        mix = best_container_mix(
            scu, usable_container_sizes(route.container_sizes_origin, route.container_sizes_destination)
        )
        transfer_seconds = 2 * estimate_transfer_time(mix)

        travel = await estimate_travel_time(
            uex_client, scw_client, route.origin_terminal_name, route.destination_terminal_name, ship
        )
        if isinstance(travel, str):
            continue  # can't estimate this candidate (e.g. cross-system) — skip, don't guess

        total_time = transfer_seconds + travel
        if total_time <= 0:
            continue
        score = estimated_profit(route, scu) / total_time

        if best_score is None or score > best_score:
            best, best_score = route, score

    return (best, best_score) if best is not None else None
