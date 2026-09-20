import asyncio
import os
from typing import NamedTuple

from dotenv import load_dotenv

from db.session import SessionLocal
from tools.cargo_packing import (
    best_container_mix,
    estimate_transfer_time,
    estimated_profit,
    reachable_scu,
    usable_container_sizes,
)
from tools.travel_time import estimate_travel_time
from tools.uexcorp.price_cache import cached_routes_from_terminal, fetch_routes_from_terminal
from tools.uexcorp.trade_data import UEXTradeRoute

# How many origin terminals a single search may fetch *live* from UEX. Everything already
# cached is used for free on top of this, so a region search warms progressively: the
# first search of Stanton fetches 8 and caches them, the next reuses those 8 and fetches 8
# more. Coverage compounds across searches while the per-search API cost stays flat.
#
# This bounds live calls, not terminals considered — the previous version sliced the
# candidate list itself, so a 117-terminal system was permanently a search of 8.
#
# Env-configurable because the right value depends on UEX's rate limit and on how many
# pilots are searching at once, neither of which is known yet. Tuning it live should be a
# restart, not a deployment. Read at import, so a change needs the process restarted.
load_dotenv()
MAX_LIVE_ROUTE_FETCHES = int(os.getenv("MAX_LIVE_ROUTE_FETCHES", "8"))


class RouteSearch(NamedTuple):
    """find_best_route's result.

    Both profit measures travel together because they answer different questions and have
    very different reliability. `profit` is what actually lands in the pilot's account —
    (sell - buy) x SCU, pure arithmetic on UEX prices. `rate` divides that by an estimated
    duration whose components are both known-weak (travel time models no atmospheric or
    approach phases; the per-crate transfer constants are labelled placeholders in
    cargo_packing). Measured 2026-09-20 on one route: 169,728 per run versus a quoted
    5,364,000 per hour, because the run was estimated at 1.9 minutes.

    origins_searched vs origins_available lets a caller say "best of the 16 I could see"
    rather than implying it searched everything."""
    best: UEXTradeRoute
    best_profit: float
    best_rate: float
    best_scu: int
    runner_up: UEXTradeRoute | None
    runner_up_profit: float | None
    runner_up_rate: float | None
    runner_up_scu: int | None
    origins_searched: int
    origins_available: int


async def _route_rows_for(uex_client, origin_terminal_ids: list[int]) -> tuple[list[dict], int]:
    """Route rows for as many origins as the budget allows, preferring cached ones.

    Cached origins cost nothing, so they're all used. Whatever's left gets up to
    MAX_LIVE_ROUTE_FETCHES live calls; the remainder is skipped this time and will be
    picked up by a later search, since the ones fetched now are cached for next time.
    """
    cached_rows: list[dict] = []
    uncached: list[int] = []

    async with SessionLocal() as session:
        for origin_id in origin_terminal_ids:
            rows = await cached_routes_from_terminal(session, origin_id)
            if rows is None:
                uncached.append(origin_id)
            else:
                cached_rows.extend(rows)

    to_fetch = uncached[:MAX_LIVE_ROUTE_FETCHES]

    async def fetch(origin_id: int) -> list[dict]:
        async with SessionLocal() as session:
            return await fetch_routes_from_terminal(uex_client, session, origin_id)

    fetched = await asyncio.gather(*[fetch(origin_id) for origin_id in to_fetch])
    for rows in fetched:
        cached_rows.extend(rows)

    searched = len(origin_terminal_ids) - len(uncached) + len(to_fetch)
    return cached_rows, searched


def profit_per_hour(score: float) -> int:
    """Converts a raw score (aUEC/second) to aUEC/hour, rounded to the nearest 1,000.
    The figure is already an extrapolation (profit if this exact trade repeated
    continuously for an hour), so reporting it to the exact aUEC is false precision —
    and a live trace showed ElevenLabs mis-pronouncing a 7-digit comma-grouped number
    (misread as starting with "three thousand"); a rounder number is far more reliable
    for TTS to read correctly, independent of the precision concern."""
    return round(score * 3600 / 1000) * 1000


def _terminal_is_auto_load(cache, terminal_id: int) -> bool:
    # is_auto_load lives on the terminals endpoint, not the commodities_routes payload —
    # UEXTradeRoute's is_auto_load_destination defaults to 0 until filled in from here.
    terminal = next((t for t in cache.terminals if t.id == terminal_id), None)
    return bool(terminal and terminal.is_auto_load)


async def find_best_route(
        uex_client, scw_client, origin_terminal_ids: list[int], ship: str, ship_scu: float, cache, *,
        commodity_id: int | None = None, exclude_destination_terminal_name: str | None = None,
        exclude_ground: bool = False, require_autoload: bool = False,
        destination_terminal_ids: set[int] | None = None, rank_by: str = "profit",
) -> RouteSearch | None:
    """Searches commodity routes from every terminal in origin_terminal_ids and ranks
    the union by profit per hour — narrowed to one commodity if commodity_id is given,
    otherwise every commodity sellable from any of them. A single-pinned-terminal search
    (the original, still-common case) is just origin_terminal_ids=[that one id]; a
    region search ("near Crusader"/"in Crusader") passes every candidate terminal in
    that region instead (see app/tools/uexcorp/matching.py's terminals_near/
    terminals_within). Pass *every* candidate — do not pre-slice. Live API calls are
    budgeted internally by MAX_LIVE_ROUTE_FETCHES, and anything already cached is searched
    for free on top of that, so slicing beforehand would discard warm origins.

    Returns a RouteSearch — scu is the reachable SCU amount the score was actually computed from,
    since profit per hour is meaningless to report without the load size it assumes.
    runner_up is None if only one candidate qualified, and is tracked across the WHOLE
    union when searching a region, not per-origin-terminal — it's still "the second-best
    option overall."

    runner_up_scu is tracked symmetrically with best_scu, not derivable afterwards: a
    caller that lets the pilot pick the runner-up needs its load size to commit it, and
    re-deriving that from the route later would recompute against whatever ship state
    exists then rather than the one it was ranked for.

    The runner-up isn't a nice-to-have — without it, "why is this the best route" has no
    real answer to give: a live trace showed the model inventing an unsupported "beats
    alternatives" claim when the only route ever kept was the winner, with every other
    candidate discarded. Reporting the runner-up gives that question a real, computed
    answer instead of a guess, and doesn't require an active run to ask it.

    Returns None if nothing qualifies at all (e.g. every candidate turned out
    cross-system, none reach a usable SCU amount, or the ground/autoload filters
    excluded everything that was left).

    exclude_ground and require_autoload only ever constrain the destination — the origin
    is always the pilot's own pinned starting point(s) here (unlike the overlay's filter
    panel, where either end can be open), so filtering it by its own ground/autoload
    status would wrongly exclude routes just because of where the pilot already is.
    destination_terminal_ids is the analogous containment constraint for a bare "in X"/
    "on X" request where BOTH ends stay in the named region — None means destination is
    open, same as before this existed.

    Shared by trade_advisor (comparing against a committed leg, hence
    exclude_destination_terminal_name to skip the committed choice itself) and any tool
    that just wants "the best option from here" with no active run involved.
    """
    raw_rows, origins_searched = await _route_rows_for(uex_client, origin_terminal_ids)

    # Cached rows are unfiltered by commodity on purpose (one cache entry serves every
    # commodity out of that terminal), so narrowing happens here rather than server-side.
    candidates = []
    for row in raw_rows:
        route = UEXTradeRoute.model_validate(row)
        if commodity_id is not None and route.commodity_id != commodity_id:
            continue
        candidates.append(route)

    best: UEXTradeRoute | None = None
    best_score = None
    best_profit = None
    best_rate = None
    best_scu = None
    runner_up: UEXTradeRoute | None = None
    runner_up_score = None
    runner_up_profit = None
    runner_up_rate = None
    runner_up_scu = None
    for route in candidates:
        if exclude_destination_terminal_name and route.destination_terminal_name == exclude_destination_terminal_name:
            continue
        if destination_terminal_ids is not None and route.destination_terminal_id not in destination_terminal_ids:
            continue
        if exclude_ground and route.is_on_ground_destination:
            continue
        if require_autoload and not _terminal_is_auto_load(cache, route.destination_terminal_id):
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

        profit = estimated_profit(route, scu)
        rate = profit / total_time
        # Ranking by profit ignores the duration estimate entirely, which is why it's the
        # default: dividing by a known-undercounted time systematically promotes the
        # shortest hops, whose times are the most wrong.
        score = rate if rank_by == "per_hour" else profit

        if best_score is None or score > best_score:
            runner_up, runner_up_score = best, best_score
            runner_up_profit, runner_up_rate, runner_up_scu = best_profit, best_rate, best_scu
            best, best_score = route, score
            best_profit, best_rate, best_scu = profit, rate, scu
        elif runner_up_score is None or score > runner_up_score:
            runner_up, runner_up_score = route, score
            runner_up_profit, runner_up_rate, runner_up_scu = profit, rate, scu

    if best is None:
        return None

    # is_auto_load_origin/destination aren't in the commodities_routes payload UEXTradeRoute
    # was validated from above — they default to 0/False — so callers that hand this route
    # straight to create_run_from_route (start_trade_run) would silently get MANUAL on both
    # legs even for an autoload-capable terminal. Patch here, once, for every caller of
    # find_best_route, rather than relying on each caller to remember to do it.
    best = _patch_auto_load(best, cache)
    if runner_up is not None:
        runner_up = _patch_auto_load(runner_up, cache)

    return RouteSearch(
        best, best_profit, best_rate, best_scu,
        runner_up, runner_up_profit, runner_up_rate, runner_up_scu,
        origins_searched, len(origin_terminal_ids),
    )


def _patch_auto_load(route: UEXTradeRoute, cache) -> UEXTradeRoute:
    return route.model_copy(update={
        "is_auto_load_origin": _terminal_is_auto_load(cache, route.origin_terminal_id),
        "is_auto_load_destination": _terminal_is_auto_load(cache, route.destination_terminal_id),
    })
