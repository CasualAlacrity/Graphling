import os

from db import trade_run_store
from db.models import LegType, TradeLeg
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import match_by_name_or_code

uex_client = UEXCorpClient(
    api_key=os.getenv("UEXCORP_API_KEY"),
    bearer_token=os.getenv("UEXCORP_BEARER_TOKEN"),
)


class AmbiguousLegError(Exception):
    def __init__(self, candidates: list[TradeLeg]):
        self.candidates = candidates


async def resolve_leg(leg_type: LegType, commodity: str | None = None, terminal: str | None = None) -> TradeLeg:
    cache = await uex_client.get_uex_cache()
    current_runs = await trade_run_store.get_in_progress_runs()

    matched_commodity = match_by_name_or_code(commodity, cache.commodities)
    matched_terminal = match_by_name_or_code(terminal, cache.terminals)

    matches = []
    for run in current_runs:
        leg = trade_run_store.current_leg(run)
        if leg and leg.leg_type == leg_type:
            if ((matched_commodity and matched_commodity.name == leg.commodity_name)
                    or (matched_terminal and matched_terminal.name == leg.terminal_name)):
                matches.append(leg)

    if len(matches) > 1:
        raise AmbiguousLegError(matches)
    elif len(matches) < 1:
        raise ValueError(f"No run with leg for {leg_type} with {commodity} or {terminal}")

    return matches[0] if matches else None
