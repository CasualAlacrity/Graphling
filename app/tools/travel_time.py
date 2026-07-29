from rapidfuzz import fuzz

from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import resolve_or_hedge

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
