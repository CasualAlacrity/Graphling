from typing import Any

from pydantic import BaseModel

from db import trade_run_store
from tools.uplink_tool import UplinkTool


class TradeRunStatusArgs(BaseModel):
    pass


class TradeRunStatusTool(UplinkTool):
    name: str = "trade_run_status"
    description: str = (
        "Report the pilot's current trade run status — call this whenever they ask "
        "something like 'what's my status', 'what's next', 'what's my destination', or "
        "'what am I hauling', instead of guessing or relying on earlier context in the "
        "conversation. Describes every leg of the run (commodity, quantity, terminal, "
        "price), not just the active one, so it also answers questions about a leg "
        "that hasn't started yet — e.g. where the pilot is headed after the current "
        "leg. Read-only — this never changes anything, it only reports."
    )
    args_schema: type[BaseModel] = TradeRunStatusArgs
    progress_label: str = "Checking trade run, looking for current status."

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        result = await self._safe_run(trade_run_store.get_in_progress_runs())
        if not isinstance(result, list):
            return result
        if not result:
            return "No active trade runs."
        return "\n".join(trade_run_store.trade_run_info(run) for run in result)
