from typing import Any

from pydantic import BaseModel, Field

from db import trade_run_store
from db.models import LegMilestone, LegType, TradeLeg
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousLegError
from tools.uplink_tool import UplinkTool


class ConfirmCargoUnloadedArgs(BaseModel):
    commodity: str | None = Field(
        default=None,
        description="The commodity the pilot just finished unloading, e.g. 'Iron', 'Laranite', "
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


class ConfirmCargoUnloadedTool(UplinkTool):
    name: str = "confirm_cargo_unloaded"
    description: str = (
        "Confirm that cargo has physically finished unloading from the pilot's ship for their "
        "current, active sale leg — call this when they report unloading is done, e.g. 'it's "
        "unloaded', 'cargo's off the ship', 'finished unloading the copper'. Only applies to "
        "selling cargo, not buying it, and only to manually-unloaded cargo — autoload sale legs "
        "never need this, since the sale itself records the unload automatically. Only works "
        "after arrival has already been confirmed (see mark_arrived) — if that hasn't happened "
        "yet, this will fail and tell you so instead of confirming unloading."
    )
    args_schema: type[BaseModel] = ConfirmCargoUnloadedArgs
    progress_label: str = "Updating trade run, confirming cargo has been unloaded."

    async def _arun(self, commodity: str | None, terminal: str | None, *args: Any, **kwargs: Any) -> Any:
        try:
            leg = await resolver.resolve_leg(leg_type=LegType.SALE, commodity=commodity, terminal=terminal)
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
                return (f"Can't record that cargo has been unloaded yet — this leg's next step is "
                        f"{trade_run_store.current_step_title(leg)}, not unloading cargo. "
                        f"Call the right tool for that step first, or check with the pilot.")
        except ValueError as ve:
            return str(ve)
        except AmbiguousLegError as ale:
            return str(ale)
