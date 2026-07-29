from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import LOW_CONFIDENCE_MAX, match_by_name_or_code, match_by_name_or_code_with_score

METERS_PER_GM = 1_000_000_000


async def estimate_travel_time(
        uex_client: UEXCorpClient, scw_client: StarCitizenWikiClient,
        origin_name: str, destination_name: str, ship_name: str,
) -> float | str:
    """Returns estimated travel time in seconds, or a plain string explaining why it
    couldn't be estimated — callers (an LLM tool, the Trade Advisor's scoring code) need
    to check which they got back, same pattern as the trade-run tools' _safe_run results.

    Deliberately approximate, matching the demo's scope: orbit-to-orbit distance stands
    in for terminal-to-terminal (no per-terminal QT drop-out distance modeled), and
    cross-system jumps aren't estimated at all — jump-point transit isn't distance-
    proportional the way in-system quantum travel is, so it's out of scope entirely
    rather than guessed at.
    """
    cache = await uex_client.get_uex_cache()

    origin_terminal = match_by_name_or_code(origin_name, cache.terminals)
    if origin_terminal is None:
        return f"Couldn't find a location matching '{origin_name}'."
    destination_terminal = match_by_name_or_code(destination_name, cache.terminals)
    if destination_terminal is None:
        return f"Couldn't find a location matching '{destination_name}'."

    if origin_terminal.star_system_name != destination_terminal.star_system_name:
        return "That route crosses star systems — jump travel time isn't estimated."

    matched_ship = match_by_name_or_code_with_score(ship_name, cache.vehicles)
    if matched_ship is None:
        return f"Couldn't find a ship matching '{ship_name}'."
    vehicle, ship_score = matched_ship
    if ship_score < LOW_CONFIDENCE_MAX:
        return (
            f"Not sure which ship you meant by '{ship_name}' — closest match is the "
            f"{vehicle.name}. Confirm and I'll check again."
        )

    ship_speed = await scw_client.get_ship_speed(vehicle.name)
    if ship_speed is None:
        return f"Couldn't find speed data for '{vehicle.name}'."

    if origin_terminal.orbit_name == destination_terminal.orbit_name:
        distance_gm = 0.0
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
