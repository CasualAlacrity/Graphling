from typing import TypeVar

from pydantic import BaseModel
from rapidfuzz import process

_HasNameAndCode = TypeVar("_HasNameAndCode")

DEFAULT_NEAR_DISTANCE = 25  # gm — used when 'near' is set without an explicit max_distance


class OrbitDistance(BaseModel):
    orbit_destination_name: str
    distance: float


def match_by_name_or_code(query: str, items: list[_HasNameAndCode], score_cutoff: int = 60) -> _HasNameAndCode | None:
    choices = []
    lookup = []
    for item in items:
        choices.append(item.name)
        lookup.append(item)

        code = getattr(item, "code", None)
        if code:
            choices.append(code)
            lookup.append(item)

    match = process.extractOne(query, choices, score_cutoff=score_cutoff)
    return lookup[match[2]] if match else None


def filter_by_match(rows, query, candidates, attr):
    if not query:
        return rows

    match = match_by_name_or_code(query, candidates)
    if not match:
        return rows

    result = []
    for r in rows:
        if getattr(r, attr) == match.name:
            result.append(r)
    return result


def _find_orbit_by_name(cache, orbit_name):
    for o in cache.orbits:
        if o.name == orbit_name:
            return o
    return None


async def filter_by_distance(rows, near, max_distance, cache, client):
    origin = match_by_name_or_code(near, cache.orbits)
    if origin:
        origin_id = origin.id
        origin_system_id = origin.id_star_system
        origin_name = origin.name
    else:
        moon = match_by_name_or_code(near, cache.moons)
        if moon:
            origin_id = moon.id_orbit
            origin_system_id = moon.id_star_system
            origin_name = moon.orbit_name
        else:
            terminal = match_by_name_or_code(near, cache.terminals)
            if not terminal or not terminal.orbit_name:
                return rows

            matched_orbit = _find_orbit_by_name(cache, terminal.orbit_name)
            if not matched_orbit:
                return rows

            origin_id = matched_orbit.id
            origin_system_id = matched_orbit.id_star_system
            origin_name = matched_orbit.name

    distances = await client.get_orbit_distances(origin_id, origin_system_id)

    parsed = []
    for d in distances:
        parsed.append(OrbitDistance.model_validate(d))

    lookup = {}
    for d in parsed:
        lookup[d.orbit_destination_name] = d.distance
    lookup[origin_name] = 0

    result = []
    for r in rows:
        if r.orbit_name in lookup and lookup[r.orbit_name] <= max_distance:
            result.append(r)
    return result
