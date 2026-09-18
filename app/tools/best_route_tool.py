from typing import Any, Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from tools.route_ranking import find_best_route, profit_per_hour
from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.trade_run import resolver, route_cache
from tools.trade_run.resolver import AmbiguousRunError
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import (
    DEFAULT_NEAR_DISTANCE,
    cargo_capable_vehicles,
    resolve_or_hedge,
    terminals_near,
    terminals_within,
    trade_terminals,
)
from tools.uplink_tool import UplinkTool


class BestRouteArgs(BaseModel):
    origin: str = Field(
        description="Where the pilot is starting from — either an exact terminal name "
                    "('Seraphim') or a region (a planet/orbit/moon/system, e.g. "
                    "'Crusader', 'ArcCorp') if the pilot named an area rather than a "
                    "specific station. Pass through what the pilot said."
    )
    origin_mode: Literal["exact", "near", "within"] = Field(
        default="exact",
        description="How to interpret `origin`, driven by the pilot's own wording, not "
                    "a default to fall back on:\n"
                    "- 'exact' — the pilot named a specific station (default; use this "
                    "whenever origin is a terminal, not a region).\n"
                    "- 'near' — the pilot said 'near X' — search within a radius of X, "
                    "not only terminals exactly at X.\n"
                    "- 'within' (or 'in X' / 'on X') — the pilot named a region with a "
                    "bare containment phrase and NO destination/origin verb — this means "
                    "they don't want to leave that region at all. When you set this, "
                    "ALSO set `destination_region` to the same value, so both ends stay "
                    "inside it.\n"
                    "If the pilot instead said 'starting in X' / 'from X' / 'out of X', "
                    "that means origin only — use 'within' here but leave "
                    "`destination_region` unset."
    )
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
    destination_region: str | None = Field(
        default=None,
        description="Only set this if the destination must also stay within a named "
                    "region — either because the pilot used a bare containment phrase "
                    "for the whole route ('in Crusader', 'on ArcCorp' — both ends, set "
                    "this to the same value as `origin`), or because they specifically "
                    "said 'ending in X' / 'to X' / 'selling in X' (destination only — "
                    "set this without necessarily constraining origin the same way). "
                    "Leave unset for the ordinary case of an open destination."
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
        "from Seraphim', 'find me a route near Crusader', 'what should I haul out of "
        "here'. Works for an exact station or a whole region (planet/orbit/moon) — see "
        "origin_mode and destination_region for exactly how the pilot's wording maps to "
        "search scope; if that phrasing is genuinely unclear (names a region with no "
        "verb making the scope obvious), ask which they mean rather than guessing. This "
        "is not about whether an existing committed run is still the best call — use "
        "trade_advisor for that instead. Doesn't require an active trade run. Ranks by "
        "profit per hour, and reports the best one found. The profit/hour figure already "
        "accounts for estimated travel time and cargo transfer time, not just the raw "
        "trade margin — if asked whether travel time factors in, the answer is yes. If "
        "the pilot then commits to a route ('yes', 'let's do it', 'start that run'), "
        "call start_trade_run with the matching route_token this reply carries — the reply "
        "may carry two, one for the best route and one for the alternative, so pass the "
        "token for whichever one the pilot actually chose. Never re-describe the route by "
        "name yourself, and never call best_route again just to double-check a route you "
        "already have a token for."
    )
    args_schema: type[BaseModel] = BestRouteArgs
    progress_label: str = "Searching routes from your location."
    uex_client: UEXCorpClient
    scw_client: StarCitizenWikiClient

    async def _arun(
            self, origin: str, origin_mode: str = "exact", ship: str | None = None, commodity: str | None = None,
            destination_region: str | None = None, exclude_ground_stations: bool = False,
            require_autoload: bool = False, *args: Any, **kwargs: Any,
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
            self._find_and_report(
                origin, origin_mode, ship, commodity, destination_region,
                exclude_ground_stations, require_autoload,
            )
        )

    async def _resolve_origin(self, origin: str, origin_mode: str, cache) -> tuple[list, str, str | None]:
        """Returns (candidate_terminals, label_for_messages, error_message) — exactly
        one of (candidates, label) vs. error is meaningful.

        Every candidate is returned, uncapped. find_best_route budgets its own live API
        calls and searches cached origins for free, so slicing here would throw away warm
        terminals it could have used at no cost."""
        # Hauling searches only ever want terminals that can trade commodities — the
        # full catalog is 81% shops, pizza counters and fuel points, and matching against
        # it resolved "Port Tressler" to a pizza counter. Applied here rather than inside
        # the shared region helpers: what counts as a valid terminal is this workflow's
        # decision, and an item or mining tool needs a different pool entirely.
        pool = trade_terminals(cache)

        if origin_mode == "exact":
            origin_terminal, error = resolve_or_hedge(origin, pool, "location")
            if error:
                return [], "", error
            return [origin_terminal], f"from {origin_terminal.name}", None

        if origin_mode == "near":
            candidates = await terminals_near(origin, pool, cache, self.uex_client)
            if candidates is None:
                return [], "", f"Couldn't find a location matching '{origin}'."
            if not candidates:
                return [], "", f"No terminals found within {DEFAULT_NEAR_DISTANCE} Gm of {origin}."
            return candidates, f"near {origin}", None

        candidates = terminals_within(origin, pool, cache)
        if candidates is None:
            return [], "", f"Couldn't find a location matching '{origin}'."
        if not candidates:
            return [], "", f"No terminals found in {origin}."
        return candidates, f"in {origin}", None

    async def _find_and_report(
            self, origin: str, origin_mode: str, ship: str, commodity: str | None, destination_region: str | None,
            exclude_ground_stations: bool = False, require_autoload: bool = False,
    ) -> str:
        cache = await self.uex_client.get_uex_cache()

        # Hauling: only ships that can carry cargo are candidates. See
        # cargo_capable_vehicles() for why this is here and not in resolve_or_hedge.
        vehicle, error = resolve_or_hedge(
            ship, cargo_capable_vehicles(cache), "ship", scorer=fuzz.token_sort_ratio
        )
        if error:
            return error

        commodity_id = None
        if commodity is not None:
            # Matched against the full catalog on purpose. Restricting the pool to
            # buyable commodities is what the overlay does, but that's a dropdown — not
            # offering a choice is a fine way for a UI to say "unavailable". A pilot can
            # say any word out loud, so the useful move is to resolve it and explain.
            matched_commodity, error = resolve_or_hedge(commodity, cache.commodities, "commodity")
            if error:
                return error

            # No terminal sells these — they're mined, salvaged or mission-derived, and a
            # hauling route needs a buy price at the origin, so UEX returns zero routes
            # for every one of them. Without this the search just comes back empty and
            # reports it like a market condition ("nothing good right now"), sending the
            # pilot off to try other stations for something that can never work.
            if not matched_commodity.is_buyable:
                return (
                    f"{matched_commodity.name} isn't sold at any terminal — it has to be "
                    "mined or salvaged, so there's no buy-and-haul route for it. I can "
                    "help with where to sell it once you've got some."
                )

            commodity_id = matched_commodity.id

        origin_terminals, origin_label, error = await self._resolve_origin(origin, origin_mode, cache)
        if error:
            return error

        destination_terminal_ids = None
        if destination_region is not None:
            destination_terminals = terminals_within(destination_region, trade_terminals(cache), cache)
            if destination_terminals is None:
                return f"Couldn't find a location matching '{destination_region}'."
            if not destination_terminals:
                return f"No terminals found in {destination_region}."
            destination_terminal_ids = {t.id for t in destination_terminals}

        result = await find_best_route(
            self.uex_client, self.scw_client, [t.id for t in origin_terminals], ship, vehicle.scu, cache,
            commodity_id=commodity_id, exclude_ground=exclude_ground_stations, require_autoload=require_autoload,
            destination_terminal_ids=destination_terminal_ids,
        )
        if result is None:
            qualifiers = []
            if exclude_ground_stations:
                qualifiers.append("excluding ground stations")
            if require_autoload:
                qualifiers.append("auto-load only")
            if destination_region is not None:
                qualifiers.append(f"staying within {destination_region}")
            suffix = f" ({', '.join(qualifiers)})" if qualifiers else ""
            return f"No usable in-system route turned up {origin_label}{suffix}."

        best, score, scu = result.best, result.best_score, result.best_scu
        runner_up, runner_up_score, runner_up_scu = result.runner_up, result.runner_up_score, result.runner_up_scu
        terminal_kind = "a ground station" if best.is_on_ground_destination else "an orbital/space station"
        message = (
            f"Best from {best.origin_terminal_name} in the {vehicle.name}: {scu:.0f} SCU of "
            f"{best.commodity_name} to {best.destination_terminal_name} — about "
            f"{profit_per_hour(score):,} aUEC/hour. It's {terminal_kind}."
        )

        # A region can hold far more terminals than one search may fetch live, so say so
        # rather than letting "best in Stanton" imply all 117 were checked. Asking again
        # genuinely does widen it — the terminals fetched this time are now cached, so the
        # next search reuses them for free and spends its budget on new ones.
        if result.origins_searched < result.origins_available:
            message += (
                f" That's the best of {result.origins_searched} terminals I checked "
                f"{origin_label}, out of {result.origins_available} — ask again and I'll "
                "widen the search."
            )

        # Stashed so a follow-up "let's do it" can hand this exact route to
        # start_trade_run without re-resolving origin/commodity/ship by name a second
        # time. Not meant to be spoken — phrased as an aside so the persona reads it as
        # bookkeeping, not part of the answer.
        token = route_cache.stash(best, int(scu), vehicle.name)
        tokens_note = f"route_token={token} for the {best.commodity_name} run"

        if runner_up is not None:
            message += (
                f" Next best was {runner_up.commodity_name} to {runner_up.destination_terminal_name} "
                f"at about {profit_per_hour(runner_up_score):,} aUEC/hour."
            )
            # The runner-up gets its own token because naming it out loud without one
            # offers the pilot something they can't actually pick — start_trade_run can
            # only reach a route that was stashed, not one described in prose.
            runner_up_token = route_cache.stash(runner_up, int(runner_up_scu), vehicle.name)
            tokens_note += f"; route_token={runner_up_token} for the {runner_up.commodity_name} alternative"

        message += f" (Internal note, don't say this part aloud: {tokens_note}.)"
        return message
