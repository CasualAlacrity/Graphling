from typing import Any, TypeVar

from pydantic import BaseModel
from rapidfuzz import fuzz, process

_HasNameAndCode = TypeVar("_HasNameAndCode")

DEFAULT_NEAR_DISTANCE = 25  # gm — used when 'near' is set without an explicit max_distance

# Below this score (but still >= score_cutoff), treat a match as a guess worth confirming
# rather than a certainty — e.g. a voice-transcribed "railing" matching "Railen" at 60,
# one point above the cutoff, with zero margin. Only meaningful to callers using
# match_by_name_or_code_with_score; match_by_name_or_code's plain callers are unaffected.
LOW_CONFIDENCE_MAX = 80


class OrbitDistance(BaseModel):
    orbit_destination_name: str
    distance: float


def _choices_and_lookup(items: list[_HasNameAndCode]) -> tuple[list[str], list[_HasNameAndCode]]:
    choices = []
    lookup = []
    for item in items:
        choices.append(item.name)
        lookup.append(item)

        code = getattr(item, "code", None)
        if code:
            choices.append(code)
            lookup.append(item)

        # Vehicles have name_full (e.g. "Gatac Railen") alongside the short name
        # ("Railen") — manufacturer-prefixed phrasing is how ships are naturally
        # referred to (including by trade_run_status's own output), so leaving it out
        # of the match pool meant a correctly-typed full name still scored worse than
        # it should have, purely from the unmatched "Gatac " prefix diluting the score.
        name_full = getattr(item, "name_full", None)
        if name_full:
            choices.append(name_full)
            lookup.append(item)
    return choices, lookup


def match_by_name_or_code(
        query: str, items: list[_HasNameAndCode], score_cutoff: int = 60, scorer: Any = fuzz.WRatio,
) -> _HasNameAndCode | None:
    choices, lookup = _choices_and_lookup(items)
    match = process.extractOne(query, choices, score_cutoff=score_cutoff, scorer=scorer)
    return lookup[match[2]] if match else None


def match_by_name_or_code_with_score(
        query: str, items: list[_HasNameAndCode], score_cutoff: int = 60, scorer: Any = fuzz.WRatio,
) -> tuple[_HasNameAndCode, float] | None:
    """Same matching as match_by_name_or_code, but also returns the match score so a
    caller can tell a barely-passing match (near score_cutoff) from a confident one, and
    hedge accordingly instead of silently committing to a guess. Kept separate from
    match_by_name_or_code — that function has 10+ call sites that don't need this.

    scorer defaults to WRatio (rapidfuzz's own default) — good for short queries against
    long official names (e.g. "Orison" against a terminal's full official name), since it
    rewards a query matching a substring/fragment of the choice. Vehicle name matching
    wants the opposite: fuzz.token_sort_ratio, a whole-string comparison — WRatio's
    substring credit is what let unrelated short names like "600i Touring" outscore the
    actually-closest "Railen" for a garbled query like "railing"."""
    choices, lookup = _choices_and_lookup(items)
    match = process.extractOne(query, choices, score_cutoff=score_cutoff, scorer=scorer)
    if not match:
        return None
    return lookup[match[2]], match[1]


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


def find_commodity_by_id(cache, commodity_id):
    for c in cache.commodities:
        if c.id == commodity_id:
            return c
    return None


async def filter_by_location(rows, cache, client, star_system=None, orbit=None, terminal=None, moon=None,
                              near=None, max_distance=None):
    """The star_system/orbit/terminal/moon + optional near/distance filter sequence shared by
    every UEX price/rental/yield tool. Terminal filtering and the near/distance concept don't
    apply to everything (e.g. mining locations, which filter four different result lists with
    no terminal or distance axis) — those stay hand-rolled rather than being forced through here.
    """
    rows = filter_by_match(rows, star_system, cache.star_systems, "star_system_name")
    rows = filter_by_match(rows, orbit, cache.orbits, "orbit_name")
    rows = filter_by_match(rows, terminal, cache.terminals, "terminal_name")
    rows = filter_by_match(rows, moon, cache.moons, "moon_name")

    if near:
        effective_max_distance = max_distance if max_distance is not None else DEFAULT_NEAR_DISTANCE
        rows = await filter_by_distance(rows, near, effective_max_distance, cache, client)

    return rows


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
