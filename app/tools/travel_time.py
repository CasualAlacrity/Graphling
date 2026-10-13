import math

from pydantic import BaseModel
from rapidfuzz import fuzz

from tools.starcitizenwiki.client import StarCitizenWikiClient
from tools.uexcorp.client import UEXCorpClient
from tools.uexcorp.matching import resolve_or_hedge

METERS_PER_GM = 1_000_000_000

# What one unit of the wiki's fuel.usage.* figures is, in SCU. NOT confirmed yet —
# the tank (fuel.capacity) is SCU, but the burn figures can't be in the same unit
# (a Railen's mains at 712.5 SCU/s would empty its 18.5 SCU tank in 0.03s), and the
# quantum side's precedent of thousandths (port_olisar_to_arccorp_fuel=274 for a
# 0.274 SCU jump) doesn't fit either — a single 24 km approach would burn more than a
# full tank. Millionths is the reading that gives plausible numbers (~0.2% of a
# Railen's tank per station approach). To be pinned by a measured in-game burn.
HYDROGEN_USAGE_UNIT_SCU = 1e-6


class TripEstimate(BaseModel):
    """One trip, broken into the quantum-travel part and the sublight approach after
    drop-out — computed once, so any question about the trip (how long, how far, how
    much fuel, why so long) reads the fields it needs from the same estimate. Fuel
    fields are None when the ship data needed for them is missing — an unknown, not a
    zero. Distances exclude a cross-system route's jump tunnel, same as the times."""

    seconds: float
    qt_seconds: float
    approach_seconds: float
    distance_m: float
    qt_distance_m: float
    approach_distance_m: float
    qt_fuel_scu: float | None = None
    qt_fuel_pct: float | None = None
    hydrogen_scu: float | None = None
    hydrogen_pct: float | None = None


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


def _location_distance_m(a, b) -> float:
    # locations/positions coordinates are in meters, not km — confirmed live against
    # two independent legs of a real route (33.82 Gm and 90.23 Gm, exact to the
    # decimal) from the wiki's own route-planner tool. An earlier single-point check
    # (Seraphim to Orison) seemed to validate a km-based /1e6 conversion instead, but
    # that was a false match — it wasn't cross-checked against a second data point at
    # the time, and turned out to be off by exactly 1000x once it was.
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _qt_cruise_seconds(distance_m: float, ship_speed) -> float:
    """Time in quantum, excluding spool. With the drive's acceleration stages known:
    stage one accelerates to engage_speed, stage two on to full drive speed, and the
    same profile mirrored to slow down — so a jump too short to reach full speed peaks
    partway through. That matters most on short hops: a Railen's Bolon needs ~31s and
    ~4 Gm just to reach full speed, so a 2 Gm moon hop takes ~32s in quantum where a
    flat-speed model says ~7s. Without the stages, falls back to that flat-speed
    model — the only one there was before the drive stats were fetched."""
    if distance_m <= 0:
        return 0.0
    top_speed = ship_speed.quantum_speed
    stage_one_accel = ship_speed.quantum_stage_one_accel
    stage_two_accel = ship_speed.quantum_stage_two_accel
    engage_speed = ship_speed.quantum_engage_speed
    if not (stage_one_accel and stage_two_accel and engage_speed):
        return distance_m / top_speed
    engage_speed = min(engage_speed, top_speed)

    stage_one_seconds = engage_speed / stage_one_accel
    stage_one_m = 0.5 * stage_one_accel * stage_one_seconds ** 2
    stage_two_seconds = (top_speed - engage_speed) / stage_two_accel
    stage_two_m = (engage_speed + top_speed) / 2 * stage_two_seconds
    ramp_seconds = stage_one_seconds + stage_two_seconds
    ramp_m = stage_one_m + stage_two_m

    if distance_m >= 2 * ramp_m:
        return 2 * ramp_seconds + (distance_m - 2 * ramp_m) / top_speed

    # Never reaches full speed — solve for when the speed-up half covers half the trip.
    half_m = distance_m / 2
    if half_m <= stage_one_m:
        half_seconds = math.sqrt(2 * half_m / stage_one_accel)
    else:
        remaining_m = half_m - stage_one_m
        stage_two_part = (
            -engage_speed + math.sqrt(engage_speed ** 2 + 2 * stage_two_accel * remaining_m)
        ) / stage_two_accel
        half_seconds = stage_one_seconds + stage_two_part
    return 2 * half_seconds


def _approach(distance_m: float, ship_speed) -> tuple[float, float | None]:
    """Sublight flight from QT drop-out to the destination — (seconds, hydrogen SCU).
    Full main thrust up to max (NAV) speed, cruise, then full retro down to zero, the
    profile a pilot actually flies a station approach with; hydrogen burns only while
    thrusting. No atmosphere distinction — there's none in-game for burn right now.

    Without the speed-curve data, falls back to SCM speed for the whole distance and
    an unknown (None) burn, rather than inventing either."""
    if distance_m <= 0:
        return 0.0, 0.0
    max_speed = ship_speed.max_speed
    zero_to_max = ship_speed.zero_to_max
    max_to_zero = ship_speed.max_to_zero
    if not (max_speed and zero_to_max and max_to_zero):
        return distance_m / ship_speed.scm_speed, None

    accel = max_speed / zero_to_max
    decel = max_speed / max_to_zero
    accel_m = 0.5 * max_speed * zero_to_max
    decel_m = 0.5 * max_speed * max_to_zero
    if distance_m >= accel_m + decel_m:
        accel_seconds, decel_seconds = zero_to_max, max_to_zero
        cruise_seconds = (distance_m - accel_m - decel_m) / max_speed
    else:
        peak_speed = math.sqrt(2 * distance_m / (1 / accel + 1 / decel))
        accel_seconds, decel_seconds = peak_speed / accel, peak_speed / decel
        cruise_seconds = 0.0

    hydrogen_scu = None
    if ship_speed.hydrogen_usage_main is not None and ship_speed.hydrogen_usage_retro is not None:
        hydrogen_scu = (
            ship_speed.hydrogen_usage_main * accel_seconds + ship_speed.hydrogen_usage_retro * decel_seconds
        ) * HYDROGEN_USAGE_UNIT_SCU
    return accel_seconds + cruise_seconds + decel_seconds, hydrogen_scu


class _TripBuilder:
    """Accumulates a trip's QT jumps and sublight approaches, then totals them into a
    TripEstimate. Spool is counted once per jump."""

    def __init__(self, ship_speed):
        self.ship_speed = ship_speed
        self.qt_seconds = 0.0
        self.qt_m = 0.0
        self.approach_seconds = 0.0
        self.approach_m = 0.0
        self.hydrogen_scu: float | None = 0.0

    def add_jump(self, distance_m: float) -> None:
        self.qt_seconds += _qt_cruise_seconds(distance_m, self.ship_speed) + self.ship_speed.quantum_spool_time
        self.qt_m += max(distance_m, 0.0)

    def add_approach(self, distance_m: float) -> None:
        seconds, hydrogen_scu = _approach(distance_m, self.ship_speed)
        self.approach_seconds += seconds
        self.approach_m += max(distance_m, 0.0)
        if hydrogen_scu is None or self.hydrogen_scu is None:
            self.hydrogen_scu = None
        else:
            self.hydrogen_scu += hydrogen_scu

    def add_hop(self, distance_m: float, destination) -> None:
        """A jump to destination that drops out at its arrival radius, then the
        approach the rest of the way. destination is a LocationPosition, or None when
        only a coarse distance is known (UEX's orbit-to-orbit fallback) — the whole
        distance is QT then, the older behavior."""
        arrival_radius = destination.arrival_radius if destination is not None else None
        if not arrival_radius:
            self.add_jump(distance_m)
            return
        if distance_m <= arrival_radius:
            # Already inside the drop-out radius — nobody spools a jump for this.
            self.add_approach(distance_m)
            return
        self.add_jump(distance_m - arrival_radius)
        self.add_approach(arrival_radius)

    def build(self) -> TripEstimate:
        ship_speed = self.ship_speed
        qt_fuel_scu = qt_fuel_pct = None
        if ship_speed.quantum_fuel_capacity and ship_speed.quantum_range:
            # Fuel per meter is capacity / range — see ShipSpeed's docstring for how
            # that was confirmed against the drive's own SCU/Gm figure.
            qt_fuel_scu = self.qt_m * ship_speed.quantum_fuel_capacity / ship_speed.quantum_range
            qt_fuel_pct = 100 * qt_fuel_scu / ship_speed.quantum_fuel_capacity

        hydrogen_pct = None
        if self.hydrogen_scu is not None and ship_speed.hydrogen_capacity:
            hydrogen_pct = 100 * self.hydrogen_scu / ship_speed.hydrogen_capacity

        return TripEstimate(
            seconds=self.qt_seconds + self.approach_seconds,
            qt_seconds=self.qt_seconds,
            approach_seconds=self.approach_seconds,
            distance_m=self.qt_m + self.approach_m,
            qt_distance_m=self.qt_m,
            approach_distance_m=self.approach_m,
            qt_fuel_scu=qt_fuel_scu,
            qt_fuel_pct=qt_fuel_pct,
            hydrogen_scu=self.hydrogen_scu,
            hydrogen_pct=hydrogen_pct,
        )


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
    physical location, or both ends orbital — station-to-station, whether same orbit
    or not). "rough" — a real number, but a known partial/undercount: either end is on
    the ground (atmospheric entry isn't modeled, regardless of which distance source
    supplied the number), or the pair crosses star systems (sums the QT-cruise legs on
    either side of the jump, excluding jump-transit time itself). "unknown" —
    estimate_travel_time can't compute anything at all for this pair.

    A hint for presentation, not a guarantee the underlying fetch will actually
    succeed — callers should still treat a string result from estimate_travel_time as
    unknown regardless of what this predicts (e.g. coordinate data missing for an
    obscure location)."""
    if origin_terminal.star_system_name != destination_terminal.star_system_name:
        return "rough"
    if is_same_physical_location(origin_terminal, destination_terminal):
        return "high"
    if not (_is_orbital(origin_terminal) and _is_orbital(destination_terminal)):
        return "rough"
    return "high"


async def estimate_travel_time(
        uex_client: UEXCorpClient, scw_client: StarCitizenWikiClient,
        origin_name: str, destination_name: str, ship_name: str,
) -> float | str:
    """Total trip time in seconds, or a plain string explaining why it couldn't be
    estimated — callers (an LLM tool, the Trade Advisor's scoring code) need to check
    which they got back, same pattern as the trade-run tools' _safe_run results. The
    time-only view of estimate_trip, for callers that only rank or display a duration."""
    trip = await estimate_trip(uex_client, scw_client, origin_name, destination_name, ship_name)
    if isinstance(trip, str):
        return trip
    return trip.seconds


async def estimate_trip(
        uex_client: UEXCorpClient, scw_client: StarCitizenWikiClient,
        origin_name: str, destination_name: str, ship_name: str,
) -> TripEstimate | str:
    """Deliberately approximate, but built from game data rather than guesses at each
    step: QT time from the installed drive's acceleration stages, QT drop-out at the
    destination's own arrival radius (stations 24 km, cities 20 km, Levski 60 km), a
    sublight approach the rest of the way, and fuel for both.

    Same-system pairs try real coordinates from the wiki's locations/positions data
    first — validated to the decimal against a real route, and not limited to any
    particular orbit relationship, so it's the primary distance source, not a special
    case. Falls back to UEX's orbit-to-orbit distance only when coordinate data is
    missing for one end (coarser — orbit to orbit, with no drop-out or approach, which
    is why it's the fallback). Cross-system routes: see _estimate_cross_system. Any of
    these can be a known undercount whenever atmospheric entry is involved — see
    travel_time_confidence for callers that need to tell a rough number from a
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

    trip = _TripBuilder(ship_speed)
    if is_same_physical_location(origin_terminal, destination_terminal):
        trip.add_jump(0.0)
        return trip.build()

    # Try the coordinate-based distance first, for any same-system pair — not just
    # same-orbit ones. It's validated to the decimal against real reference data (see
    # _location_distance_m), strictly more accurate than UEX's orbit-to-orbit
    # approximation, and the positions dataset doesn't care about orbit boundaries: it
    # can measure point-to-point distance between two terminals in different orbits
    # just as well as two in the same one. UEX's orbit_distances is only a fallback now,
    # for whichever pairs the positions dataset doesn't have coordinates for.
    origin_location = await _resolve_location(scw_client, origin_terminal)
    destination_location = await _resolve_location(scw_client, destination_terminal)
    if origin_location is not None and destination_location is not None:
        trip.add_hop(_location_distance_m(origin_location, destination_location), destination_location)
        return trip.build()

    if is_same_orbit_different_place(origin_terminal, destination_terminal):
        # Same orbit, different place, and no coordinate data for at least one end —
        # UEX has no data for this case at all (orbit_distances is inter-orbit only),
        # so there's no fallback left to try.
        return (
            f"Can't estimate travel time between '{origin_name}' and '{destination_name}' — "
            "they're in the same orbit at different specific locations, and no "
            "coordinate data was found for one or both."
        )

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

    trip.add_hop(float(matching["distance"]) * METERS_PER_GM, None)
    return trip.build()


async def _estimate_cross_system(
        scw_client: StarCitizenWikiClient, origin_terminal, destination_terminal, ship_speed,
        origin_name: str, destination_name: str,
) -> TripEstimate | str:
    """Partial estimate — the in-system legs on either side of the jump, excluding
    jump-transit time itself (not distance-proportional the way in-system QT is, and
    the wiki's own route-planner tool doesn't show a number for that phase either).

    Origin side: pilots don't QT to the jump point — they QT to the gateway station
    (e.g. Stanton's "Pyro Gateway"), drop out at its arrival radius, and fly the rest
    to the jump point on hydrogen. The drop-out is modeled on the station's star-facing
    side, i.e. arrival radius plus the station-to-point distance (~40-50 km) in a
    straight line: jump points are the outermost structures in a system, so a pilot
    effectively never arrives from beyond them. That's not the exact approach vector,
    which depends on where they came from, but it's consistent across routes, which
    is what ranking needs. Falls back to QT straight to the jump point when no gateway
    station is found.

    Destination side: QT from the exit jump point to the destination, dropping out at
    its arrival radius as usual."""
    origin_system = origin_terminal.star_system_name
    destination_system = destination_terminal.star_system_name

    origin_location = await _resolve_location(scw_client, origin_terminal)
    destination_location = await _resolve_location(scw_client, destination_terminal)
    if origin_location is None or destination_location is None:
        return (
            f"That route crosses star systems, and no coordinate data was found for "
            f"'{origin_name}' or '{destination_name}' to even partially estimate it."
        )

    trip = _TripBuilder(ship_speed)

    gateway = await scw_client.find_gateway(origin_system, destination_system)
    gateway_jump_point = await scw_client.nearest_jump_point(gateway) if gateway is not None else None
    if gateway is not None and gateway_jump_point is not None:
        arrival_radius = gateway.arrival_radius or 0.0
        trip.add_jump(_location_distance_m(origin_location, gateway) - arrival_radius)
        trip.add_approach(arrival_radius + _location_distance_m(gateway, gateway_jump_point))
    else:
        origin_jump_point = await scw_client.find_jump_point(origin_system, destination_system)
        if origin_jump_point is None:
            return _no_jump_point_message(origin_system, destination_system)
        trip.add_jump(_location_distance_m(origin_location, origin_jump_point))

    destination_jump_point = await scw_client.find_jump_point(destination_system, origin_system)
    if destination_jump_point is None:
        return _no_jump_point_message(origin_system, destination_system)
    trip.add_hop(_location_distance_m(destination_jump_point, destination_location), destination_location)

    return trip.build()


def _no_jump_point_message(origin_system: str, destination_system: str) -> str:
    return (
        f"That route crosses star systems, and no jump point data was found "
        f"between '{origin_system}' and '{destination_system}' to even partially estimate it."
    )
