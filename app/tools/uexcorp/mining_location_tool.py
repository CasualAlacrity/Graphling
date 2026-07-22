from typing import Any

from pydantic import BaseModel, Field

from tools.uexcorp.matching import filter_by_match, find_commodity_by_id, match_by_name_or_code
from tools.uplink_tool import UEXBackedTool


def _has_location_data(commodity) -> bool:
    return bool(commodity.ids_planets or commodity.ids_moons or commodity.ids_orbits or commodity.ids_poi)


def _resolve_locatable_commodity(matched_commodity, cache):
    if _has_location_data(matched_commodity):
        return matched_commodity

    if matched_commodity.id_parent:
        parent = find_commodity_by_id(cache, matched_commodity.id_parent)
        if parent and _has_location_data(parent):
            return parent

    return None


class MiningLocationArgs(BaseModel):
    commodity: str = Field(
        description="The raw ore or harvestable material to find spawn locations for, e.g. "
                    "'Copper', 'Iron', 'Carinite'. You can name either the raw/harvestable form or "
                    "the refined form — it's resolved automatically. May be misspelled or "
                    "phonetically transcribed from speech — pass through what the user said, don't "
                    "try to correct it yourself."
    )
    star_system: str | None = Field(
        default=None,
        description="The star system to narrow results to, e.g. 'Stanton' or 'Pyro'. Leave unset "
                    "if the user didn't mention one."
    )
    orbit: str | None = Field(
        default=None,
        description="A specific planet to narrow results to, e.g. 'microTech', 'Hurston'. Leave "
                    "unset if the user didn't mention one."
    )
    moon: str | None = Field(
        default=None,
        description="A specific moon to narrow results to, e.g. 'Yela', 'Daymar'. Leave unset if "
                    "the user didn't mention one."
    )


class MiningLocationTool(UEXBackedTool):
    name: str = "mining_location_lookup"
    description: str = (
        "Look up where a Star Citizen raw ore or harvestable material can actually be found/mined "
        "in the world — planets, moons, Lagrange-point orbital sites, and points of interest "
        "(asteroid belts, etc.) it spawns at. This is about physical spawn locations, not where to "
        "buy or sell it — use commodity_price_lookup for trading. Not every commodity is mineable; "
        "manufactured or refined-only goods won't have results here."
    )
    args_schema: type[BaseModel] = MiningLocationArgs
    progress_label: str = "UEX to look up mining locations"

    async def _arun(self, commodity: str, star_system: str | None = None, orbit: str | None = None,
                    moon: str | None = None) -> dict[str, Any] | str:
        return await self._safe_run(self._lookup(commodity, star_system, orbit, moon))

    async def _lookup(self, commodity, star_system, orbit, moon) -> dict[str, Any] | str:
        cache = await self.client.get_uex_cache()

        matched_commodity = match_by_name_or_code(commodity, cache.commodities)
        if matched_commodity is None:
            return f"No commodity matching '{commodity}' was found."

        locatable = _resolve_locatable_commodity(matched_commodity, cache)
        if locatable is None:
            return (f"{matched_commodity.name} isn't a raw ore or harvestable material, so it "
                    f"doesn't have spawn locations.")

        planets = []
        for o in cache.orbits:
            if o.id in locatable.ids_planets:
                planets.append(o)

        orbital_locations = []
        for o in cache.orbits:
            if o.id in locatable.ids_orbits:
                orbital_locations.append(o)

        moons = []
        for m in cache.moons:
            if m.id in locatable.ids_moons:
                moons.append(m)

        points_of_interest = []
        for p in cache.poi:
            if p.id in locatable.ids_poi:
                points_of_interest.append(p)

        # Not delegated to filter_by_location: no terminal or near/distance axis here, and each
        # of the four lists filters on a different attr name — genuinely a different shape, not
        # the same duplicated block as the price/rental/yield tools.
        planets = filter_by_match(planets, star_system, cache.star_systems, "star_system_name")
        planets = filter_by_match(planets, orbit, cache.orbits, "name")

        orbital_locations = filter_by_match(orbital_locations, star_system, cache.star_systems, "star_system_name")

        moons = filter_by_match(moons, star_system, cache.star_systems, "star_system_name")
        moons = filter_by_match(moons, orbit, cache.orbits, "orbit_name")
        moons = filter_by_match(moons, moon, cache.moons, "name")

        points_of_interest = filter_by_match(
            points_of_interest, star_system, cache.star_systems, "star_system_name",
        )
        points_of_interest = filter_by_match(points_of_interest, orbit, cache.orbits, "orbit_name")
        points_of_interest = filter_by_match(points_of_interest, moon, cache.moons, "moon_name")

        return {
            "planets": [p.model_dump(exclude_none=True) for p in planets],
            "orbital_locations": [o.model_dump(exclude_none=True) for o in orbital_locations],
            "moons": [m.model_dump(exclude_none=True) for m in moons],
            "points_of_interest": [p.model_dump(exclude_none=True) for p in points_of_interest],
        }
