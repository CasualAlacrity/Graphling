from operator import attrgetter
from typing import Any

from pydantic import BaseModel, Field

from tools.uexcorp.args import LocationArgs
from tools.uexcorp.matching import filter_by_location, find_commodity_by_id, match_by_name_or_code
from tools.uplink_tool import UplinkTool


def _resolve_raw_commodity(matched_commodity, cache):
    if matched_commodity.is_raw:
        return matched_commodity

    if matched_commodity.is_refined and matched_commodity.id_parent:
        return find_commodity_by_id(cache, matched_commodity.id_parent)

    return None


class RefineryYieldArgs(LocationArgs):
    commodity: str = Field(
        description="The commodity to check refinery yield bonuses for, e.g. 'Iron', 'Laranite', "
                    "'Gold'. You can name either the raw ore or the refined material — it's "
                    "resolved automatically. May be misspelled or phonetically transcribed from "
                    "speech — pass through what the user said, don't try to correct it yourself."
    )


class RefineryYieldTool(UplinkTool):
    name: str = "refinery_yield_lookup"
    description: str = (
        "Look up current refinery yield bonuses for a Star Citizen raw ore across refinery "
        "terminals — how much extra (or less) refined material a terminal currently produces for "
        "that ore, as a percentage. Returns best_yield (the single best terminal to refine at) — "
        "use it directly for 'best'/'highest bonus' questions rather than scanning all_results "
        "yourself. all_results lists every matching terminal, for when the user wants alternatives "
        "or a specific location instead of the single best option. Only applies to commodities "
        "that go through the ore-refining pipeline — not every commodity has a raw/refined form."
    )
    args_schema: type[BaseModel] = RefineryYieldArgs
    progress_label: str = "UEX to check refinery yield bonuses"

    async def _arun(self, commodity: str, star_system: str | None = None, orbit: str | None = None,
                    terminal: str | None = None, moon: str | None = None, near: str | None = None,
                    max_distance: float | None = None) -> dict[str, Any] | str:
        return await self._safe_run(
            self._lookup(commodity, star_system, orbit, terminal, moon, near, max_distance)
        )

    async def _lookup(self, commodity, star_system, orbit, terminal, moon, near, max_distance) -> dict[str, Any] | str:
        cache = await self.client.get_uex_cache()

        matched_commodity = match_by_name_or_code(commodity, cache.commodities)
        if matched_commodity is None:
            return f"No commodity matching '{commodity}' was found."

        raw_commodity = _resolve_raw_commodity(matched_commodity, cache)
        if raw_commodity is None:
            return (f"{matched_commodity.name} isn't refined from a raw ore, so there's no "
                    f"refinery yield data for it.")

        rows = []
        for row in cache.refinery_yields:
            if row.commodity_name == raw_commodity.name:
                rows.append(row)

        rows = await filter_by_location(
            rows, cache, self.client, star_system, orbit, terminal, moon, near, max_distance,
        )

        best_yield = max(rows, key=attrgetter("yield_bonus_percent"), default=None)

        return {
            "best_yield": best_yield.model_dump(exclude_none=True) if best_yield else None,
            "all_results": [r.model_dump(exclude_none=True) for r in rows],
        }
