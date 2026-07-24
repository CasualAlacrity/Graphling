from typing import Any

from pydantic import BaseModel, Field

from db import trade_run_store
from db.models import LegMilestone, LegType, TradeLeg
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousLegError
from tools.uplink_tool import UplinkTool


class ConfirmCargoLoadedArgs(BaseModel):
    commodity: str | None = Field(
        default=None,
        description="The commodity the pilot just finished loading, e.g. 'Iron', 'Laranite', "
                    "'Agricium' — used to identify which trade leg they mean, not to look anything "
                    "up. May be misspelled or phonetically transcribed from speech — pass through "
                    "what the user said, don't try to correct it yourself. Leave unset if the user "
                    "only named a location."
    )
    terminal: str | None = Field(
        default=None,
        description="An exact trading terminal/location name, e.g. 'Area18', 'Port Tressler', "
                    "'Ambitious Dream Refueling'. Only set this if the user named a specific location."
    )


class ConfirmCargoLoadedTool(UplinkTool):
    name: str = "confirm_cargo_loaded"
    description: str = (
        "Confirm that cargo has physically finished loading onto the pilot's ship for their "
        "current, active acquisition leg — call this when they report loading is done, e.g. "
        "'it's loaded', 'cargo's on board', 'finished loading the copper'. Only applies to "
        "buying cargo, not selling it. Only works after the purchase has already been recorded "
        "(see mark_cargo_acquired) — if that hasn't happened yet, this will fail and tell you so "
        "instead of confirming loading."
    )
    args_schema: type[BaseModel] = ConfirmCargoLoadedArgs
    progress_label: str = "Updating trade run, confirming cargo has been loaded."

    async def _arun(self, commodity: str | None, terminal: str | None, *args: Any, **kwargs: Any) -> Any:
        try:
            leg = await resolver.resolve_leg(leg_type=LegType.ACQUISITION, commodity=commodity, terminal=terminal)
            next_step = trade_run_store.next_unset_field(leg)
            if next_step is LegMilestone.TRANSFERRED_AT:
                result = await self._safe_run(trade_run_store.advance_leg(leg.id))
                if isinstance(result, TradeLeg):
                    new_next_step = trade_run_store.next_unset_field(result)
                    return (f"Advanced leg: {result.commodity_name} at {result.terminal_name} "
                            f"from {LegMilestone.TRANSFERRED_AT} to {new_next_step}")
                else:
                    return result
            else:
                return (f"Can't record that cargo has been loaded yet — this leg's next step is "
                        f"{trade_run_store.current_step_title(leg)}, not loading cargo. "
                        f"Call the right tool for that step first, or check with the pilot.")
        except ValueError as ve:
            return str(ve)
        except AmbiguousLegError as ale:
            return str(ale)
