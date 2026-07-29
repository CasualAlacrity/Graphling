import math

from rapidfuzz import fuzz

from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import resolve_or_hedge

METERS_PER_GM = 1_000_000_000


def _location_lookup_candidates(terminal) -> list[str]:
    # UEX and the wiki's positions dataset don't always agree on which of a terminal's
    # several names is "the" name — confirmed live: GrimHEX's space_station_name is its
    # full official name "Green Imperial Housing Exchange", but the wiki's positions
    # data calls the same place "Grim HEX", matching UEX's own displayname field
    # instead. Try every plausible identifier, most-specific-official first, rather
    # than betting on one field always winning — still exact (case-insensitive)
    # matching throughout, just against more than one candidate string.
    candidates = [
        terminal.space_station_name, terminal.outpost_name, terminal.city_name,
        terminal.displayname, terminal.nickname, terminal.moon_name, terminal.planet_name,
    ]
    seen = set()
    result = []
    for name in candidates:
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


async def _resolve_location(scw_client, terminal):
    for name in _location_lookup_candidates(terminal):
        location = await scw_client.find_location(name, terminal.star_system_name)
        if location is not None:
            return location
    return None


def _location_distance_gm(a, b) -> float:
    # locations/positions coordinates are in meters, not km — confirmed live against
    # two independent legs of a real route (33.82 Gm and 90.23 Gm, exact to the
    # decimal) from the wiki's own route-planner tool. An earlier single-point check
    # (Seraphim to Orison) seemed to validate a km-based /1e6 conversion instead, but
    # that was a false match — it wasn't cross-checked against a second data point at
    # the time, and turned out to be off by exactly 1000x once it was.
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) / METERS_PER_GM


def _leg_time(distance_gm: float, ship_speed) -> float:
    return distance_gm * METERS_PER_GM / ship_speed.quantum_speed + ship_speed.quantum_spool_time


def _is_orbital(terminal) -> bool:
    # space_station_name set means the terminal is an orbital platform, checked first
    # (not alongside city_name) for the same reason is_same_physical_location does —
    # a station can have an associated city_name too, so its presence alone doesn't
    # mean "on the ground."
    return bool(terminal.space_station_name)


def is_same_physical_location(origin_terminal, destination_terminal) -> bool:
    """True only when both terminals resolve to the same specific place — same
    orbit_name alone isn't a safe proxy for "no real distance between them." Confirmed
    live against UEX: Seraphim Station (an orbital platform) and TDD Orison (a shop in
    the surface city of Orison) both report orbit_name="Crusader" — UEX's "orbit" means
    the whole planet, not a point.

    Checks space_station_name/outpost_name *before* city_name, not alongside it as an
    OR — Seraphim's own terminal record sets city_name="Orison" (its nearest city, for
    reference) *and* space_station_name="Seraphim Station" simultaneously, so an
    any-match OR would wrongly call it the same place as an actual Orison terminal via
    the city_name coincidence alone, missing that the station name differs. Whichever
    identifier is actually set takes priority; city_name is only the tiebreaker when
    neither terminal is a station or outpost.
    """
    if origin_terminal.space_station_name or destination_terminal.space_station_name:
        return bool(
            origin_terminal.space_station_name
            and origin_terminal.space_station_name == destination_terminal.space_station_name
        )
    if origin_terminal.outpost_name or destination_terminal.outpost_name:
        return bool(origin_terminal.outpost_name and origin_terminal.outpost_name == destination_terminal.outpost_name)
    return bool(origin_terminal.city_name and origin_terminal.city_name == destination_terminal.city_name)


def is_same_orbit_different_place(origin_terminal, destination_terminal) -> bool:
    return (
        origin_terminal.orbit_name == destination_terminal.orbit_name
        and not is_same_physical_location(origin_terminal, destination_terminal)
    )


def travel_time_confidence(origin_terminal, destination_terminal) -> str:
    """"high" — the pure QT-cruise formula is actually accurate for this pair (same
    physical location, station-to-station, or a normal cross-orbit hop via UEX).
    "rough" — a real number, but a known partial/undercount: either at least one end
    is on the ground (atmospheric entry isn't modeled), or the pair crosses star
    systems (the number sums the QT-cruise legs on either side of the jump, excluding
    jump-transit time itself). "unknown" — estimate_travel_time can't compute anything
    at all for this pair (e.g. no jump point data between the two systems).

    A hint for presentation, not a guarantee the underlying fetch will actually
    succeed — callers should still treat a string result from estimate_travel_time as
    unknown regardless of what this predicts (e.g. coordinate data missing for an
    obscure location)."""
    if origin_terminal.star_system_name != destination_terminal.star_system_name:
        return "rough"
    if is_same_orbit_different_place(origin_terminal, destination_terminal) and not (
        _is_orbital(origin_terminal) and _is_orbital(destination_terminal)
    ):
        return "rough"
    return "high"


async def estimate_travel_time(
        uex_client: UEXCorpClient, scw_client: StarCitizenWikiClient,
        origin_name: str, destination_name: str, ship_name: str,
) -> float | str:
    """Returns estimated travel time in seconds, or a plain string explaining why it
    couldn't be estimated — callers (an LLM tool, the Trade Advisor's scoring code) need
    to check which they got back, same pattern as the trade-run tools' _safe_run results.

    Deliberately approximate. Cross-orbit hops use UEX's orbit-to-orbit distance
    (terminal-to-terminal within that, not per-terminal QT drop-out distance). Same-orbit
    hops between different specific places (e.g. an orbital station to a surface city)
    fall back to real coordinates from the wiki's locations/positions data instead —
    UEX has no distance data for that case at all. Cross-system routes get a partial
    estimate the same way — sum of the QT-cruise legs on either side of the jump point,
    excluding jump-transit time itself (not distance-proportional the way in-system QT
    is, and the wiki's own route-planner tool doesn't show a number for that phase
    either, confirmed live). All of these are known undercounts, not full-trip times —
    see travel_time_confidence for callers that need to tell a rough number from a
    confident one.
    """
    cache = await uex_client.get_uex_cache()

    origin_terminal, error = resolve_or_hedge(origin_name, cache.terminals, "location")
    if error:
        return error

    destination_terminal, error = resolve_or_hedge(destination_name, cache.terminals, "location")
    if error:
        return error

    vehicle, error = resolve_or_hedge(ship_name, cache.vehicles, "ship", scorer=fuzz.token_sort_ratio)
    if error:
        return error

    ship_speed = await scw_client.get_ship_speed(vehicle.name)
    if ship_speed is None:
        return f"Couldn't find speed data for '{vehicle.name}'."

    if origin_terminal.star_system_name != destination_terminal.star_system_name:
        return await _estimate_cross_system(
            scw_client, origin_terminal, destination_terminal, ship_speed, origin_name, destination_name,
        )

    if is_same_physical_location(origin_terminal, destination_terminal):
        distance_gm = 0.0
    elif is_same_orbit_different_place(origin_terminal, destination_terminal):
        # UEX's orbit_distances is inter-orbit only, so this is exactly the gap it can't
        # fill (e.g. an orbital station to a surface city, or two stations around the
        # same planet) — the wiki's locations/positions dataset has real coordinates for
        # both ends, so a genuine distance can be computed instead of giving up.
        #
        # When at least one end is on the ground, this is a known, deliberate
        # undercount — the pure QT-cruise formula below doesn't model atmospheric entry
        # (or ground-to-ground flight), a real phase with its own time cost. Reported
        # anyway rather than withheld: a labeled rough estimate is more useful than
        # nothing, as long as callers can tell it apart from a confident one — see
        # travel_time_confidence, which callers should check before deciding how to
        # present this number.
        origin_location = await _resolve_location(scw_client, origin_terminal)
        destination_location = await _resolve_location(scw_client, destination_terminal)
        if origin_location is None or destination_location is None:
            return (
                f"Can't estimate travel time between '{origin_name}' and '{destination_name}' — "
                "they're in the same orbit at different specific locations, and no "
                "coordinate data was found for one or both."
            )
        distance_gm = _location_distance_gm(origin_location, destination_location)
    else:
        origin_orbit = next((o for o in cache.orbits if o.name == origin_terminal.orbit_name), None)
        origin_star_system = next(
            (s for s in cache.star_systems if s.name == origin_terminal.star_system_name), None
        )
        if origin_orbit is None or origin_star_system is None:
            return f"Couldn't resolve location data for '{origin_name}'."

        distances = await uex_client.get_orbit_distances(origin_orbit.id, origin_star_system.id)
        matching = next(
            (d for d in distances if d["orbit_destination_name"] == destination_terminal.orbit_name), None
        )
        if matching is None:
            return f"No known distance between '{origin_name}' and '{destination_name}'."
        distance_gm = float(matching["distance"])

    return _leg_time(distance_gm, ship_speed)


async def _estimate_cross_system(
        scw_client: StarCitizenWikiClient, origin_terminal, destination_terminal, ship_speed,
        origin_name: str, destination_name: str,
) -> float | str:
    """Partial estimate matching the wiki's own route-planner tool: sum the QT-cruise
    legs on either side of the jump (origin to its system's jump point, then the
    destination-side jump point to the destination), excluding jump-transit time
    itself."""
    origin_jump_point = await scw_client.find_jump_point(
        origin_terminal.star_system_name, destination_terminal.star_system_name
    )
    destination_jump_point = await scw_client.find_jump_point(
        destination_terminal.star_system_name, origin_terminal.star_system_name
    )
    if origin_jump_point is None or destination_jump_point is None:
        return (
            f"That route crosses star systems, and no jump point data was found "
            f"between '{origin_terminal.star_system_name}' and "
            f"'{destination_terminal.star_system_name}' to even partially estimate it."
        )

    origin_location = await _resolve_location(scw_client, origin_terminal)
    destination_location = await _resolve_location(scw_client, destination_terminal)
    if origin_location is None or destination_location is None:
        return (
            f"That route crosses star systems, and no coordinate data was found for "
            f"'{origin_name}' or '{destination_name}' to even partially estimate it."
        )

    leg1 = _leg_time(_location_distance_gm(origin_location, origin_jump_point), ship_speed)
    leg2 = _leg_time(_location_distance_gm(destination_jump_point, destination_location), ship_speed)
    return leg1 + leg2
