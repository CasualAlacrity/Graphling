from typing import Any

from pydantic import BaseModel, Field

from db import trade_run_store
from db.models import LegMilestone, TradeLeg
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousLegError
from tools.uplink_tool import UplinkTool


class MarkArrivedArgs(BaseModel):
    commodity: str | None = Field(
        default=None,
        description="The commodity involved in the leg the pilot just arrived for, e.g. 'Iron', "
                    "'Laranite', 'Agricium' — used to identify which trade leg they mean, not to "
                    "look anything up. May be misspelled or phonetically transcribed from speech — "
                    "pass through what the user said, don't try to correct it yourself. Leave unset "
                    "if the user only named a location."
    )
    terminal: str | None = Field(
        default=None,
        description="An exact trading terminal/location name, e.g. 'Area18', 'Port Tressler', "
                    "'Ambitious Dream Refueling'. Only set this if the user named a specific location."
    )


class MarkArrivedTool(UplinkTool):
    name: str = "mark_arrived"
    description: str = (
        "Mark the pilot as having physically arrived at the terminal for their current, "
        "active trade leg — this applies to either the pickup (acquisition) or the "
        "drop-off (sale) stop, whichever is next; you don't need to know or say which. "
        "Call this when the pilot reports arriving somewhere in transit — 'I'm here', "
        "'just landed at Area18', 'I'm at the buyer now' — not when they say they're "
        "leaving or starting travel. This only stamps arrival time; it does not record "
        "a purchase or sale (use the buy/sell tools for that) and does not confirm cargo "
        "has been loaded or unloaded."
    )
    args_schema: type[BaseModel] = MarkArrivedArgs
    progress_label: str = "Resolving trade leg to flag."

    async def _arun(self, commodity: str | None, terminal: str | None, *args: Any, **kwargs: Any) -> Any:
        try:
            leg = await resolver.resolve_leg(commodity=commodity, terminal=terminal)
            if trade_run_store.next_unset_field(leg) == LegMilestone.REACHED_AT:
                result = await self._safe_run(trade_run_store.advance_leg(leg.id))
                if isinstance(result, TradeLeg):
                    next_step = trade_run_store.next_unset_field(result)
                    return (f"Advanced leg: {result.commodity_name} at {result.terminal_name} "
                            f"from {LegMilestone.REACHED_AT} to {next_step}")
                return result
            else:
                return f"Current leg: {leg.commodity_name} at {leg.terminal_name}, is not at {LegMilestone.REACHED_AT}"
        except ValueError as ve:
            return str(ve)
        except AmbiguousLegError as ale:
            return str(ale)