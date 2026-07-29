import math

from rapidfuzz import fuzz

from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import resolve_or_hedge

METERS_PER_GM = 1_000_000_000
# locations/positions coordinates are in km, confirmed live against the wiki's own
# route-planner tool's displayed distance (see StarCitizenWikiClient.get_locations).
KM_PER_GM = 1_000_000


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
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) / KM_PER_GM


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
    UEX has no distance data for that case at all. Cross-system jumps aren't estimated
    either way — jump-point transit isn't distance-proportional the way in-system
    quantum travel is, so it's out of scope entirely rather than guessed at.
    """
    cache = await uex_client.get_uex_cache()

    origin_terminal, error = resolve_or_hedge(origin_name, cache.terminals, "location")
    if error:
        return error

    destination_terminal, error = resolve_or_hedge(destination_name, cache.terminals, "location")
    if error:
        return error

    if origin_terminal.star_system_name != destination_terminal.star_system_name:
        return "That route crosses star systems — jump travel time isn't estimated."

    vehicle, error = resolve_or_hedge(ship_name, cache.vehicles, "ship", scorer=fuzz.token_sort_ratio)
    if error:
        return error

    ship_speed = await scw_client.get_ship_speed(vehicle.name)
    if ship_speed is None:
        return f"Couldn't find speed data for '{vehicle.name}'."

    if is_same_physical_location(origin_terminal, destination_terminal):
        distance_gm = 0.0
    elif (
        is_same_orbit_different_place(origin_terminal, destination_terminal)
        and _is_orbital(origin_terminal) and _is_orbital(destination_terminal)
    ):
        # UEX's orbit_distances is inter-orbit only, so this is exactly the gap it can't
        # fill (e.g. two different orbital stations around the same planet) — the wiki's
        # locations/positions dataset has real coordinates for both, so a genuine
        # distance can be computed instead of giving up. Restricted to station-to-station
        # (both _is_orbital) deliberately: the pure QT-cruise formula below is only
        # actually correct when neither end involves atmospheric entry — a station-to-
        # surface-city hop has that same coordinate data available, but computing "9
        # seconds" for it would be a confident, precise-looking undercount (missing the
        # atmospheric entry phase entirely), worse than an honest "can't estimate."
        # Modeling that phase (and the equivalent QT-dropout-to-station-approach
        # distance) is deferred, not attempted here.
        origin_location = await _resolve_location(scw_client, origin_terminal)
        destination_location = await _resolve_location(scw_client, destination_terminal)
        if origin_location is None or destination_location is None:
            return (
                f"Can't estimate travel time between '{origin_name}' and '{destination_name}' — "
                "they're in the same orbit at different specific locations, and no "
                "coordinate data was found for one or both."
            )
        distance_gm = _location_distance_gm(origin_location, destination_location)
    elif is_same_orbit_different_place(origin_terminal, destination_terminal):
        # Same orbit, different place, but at least one end is on the ground — real
        # distance data may exist (positions dataset), but the pure QT-cruise formula
        # would understate it (atmospheric entry, or ground-to-ground flight, isn't QT
        # travel at all). Deferred, not guessed at.
        return (
            f"Can't estimate travel time between '{origin_name}' and '{destination_name}' — "
            "at least one is a surface location, which involves atmospheric entry, not "
            "just quantum cruise distance."
        )
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

    distance_meters = distance_gm * METERS_PER_GM
    return distance_meters / ship_speed.quantum_speed + ship_speed.quantum_spool_time
