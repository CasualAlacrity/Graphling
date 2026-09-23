from typing import Any

from pydantic import BaseModel, Field

from db import trade_run_store
from db.models import LegType
from tools.trade_run import route_cache
from tools.uplink_tool import UplinkTool


class StartTradeRunArgs(BaseModel):
    route_token: str = Field(
        description="The route_token from a best_route or trade_advisor reply the pilot "
                    "is now committing to. Copy it exactly as it appeared in that reply "
                    "— never invent one, and never call this tool without one."
    )
    quantity_scu: int | None = Field(
        default=None,
        description="Override the SCU amount to commit, if the pilot named a different "
                    "quantity than what was found. Leave unset to use the full reachable "
                    "capacity that was already computed for this route."
    )


class StartTradeRunTool(UplinkTool):
    name: str = "start_trade_run"
    description: str = (
        "Commit to a route best_route or trade_advisor already found and start "
        "tracking it as a trade run — creates the buy and sell legs. Call this only "
        "once the pilot has explicitly chosen to start hauling a specific "
        "already-found route ('yes', 'let's do it', 'start that run'), passing the "
        "route_token that reply carried. Never call this speculatively, and never on "
        "a route the pilot only described from scratch — run best_route on it first "
        "so there's a route_token to commit to."
    )
    args_schema: type[BaseModel] = StartTradeRunArgs
    progress_label: str = "Starting a new trade run."

    async def _arun(self, route_token: str, quantity_scu: int | None = None, *args: Any, **kwargs: Any) -> Any:
        return await self._safe_run(self._start(route_token, quantity_scu))

    async def _start(self, route_token: str, quantity_scu: int | None) -> str:
        cached = route_cache.get(route_token)
        if cached is None:
            return "That recommendation isn't available anymore — want me to search again?"
        route, scu_hint, vehicle_name = cached

        qty = quantity_scu or scu_hint
        if qty <= 0:
            return "That route can't fill any cargo right now — origin or destination stock is at zero."

        run = await trade_run_store.create_run_from_route(route, qty, vehicle_name)

        acquisition = next(leg for leg in run.legs if leg.leg_type == LegType.ACQUISITION)
        return (
            f"Run started — {route.commodity_name}, from {route.origin_terminal_name} to "
            f"{route.destination_terminal_name}."
        )
