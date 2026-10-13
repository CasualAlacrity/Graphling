"""Covers the trip model in tools/travel_time.py — QT acceleration stages, drop-out at
the destination's arrival radius, the sublight approach, fuel, and the gateway leg of a
cross-system route.

Ship numbers are the Gatac Railen's (Bolon S2 drive) from the wiki, 2026-10-13, so a
failure here reads in the same units a pilot would check in game.
"""
from types import SimpleNamespace

import pytest

from tools.starcitizenwiki.models import LocationPosition, ShipSpeed
from tools.travel_time import (
    HYDROGEN_USAGE_UNIT_SCU,
    METERS_PER_GM,
    _approach,
    _estimate_cross_system,
    _qt_cruise_seconds,
    _TripBuilder,
)

RAILEN = ShipSpeed.model_construct(
    game_name="Gatac Railen", scm_speed=170.0,
    quantum_speed=275_942_800.0, quantum_spool_time=8.0,
    quantum_fuel_capacity=8.8, quantum_range=1_345_565_749_235.0,
    max_speed=1000.0, zero_to_max=25.9, max_to_zero=43.05,
    hydrogen_capacity=18.5, hydrogen_usage_main=712.5, hydrogen_usage_retro=428.75,
    quantum_engage_speed=84_000_000.0, quantum_stage_one_accel=7_206_857.0,
    quantum_stage_two_accel=9_994_285.0,
)

# What a cached payload from before the new fields existed comes back as —
# model_construct fills the missing ones with their None defaults.
RAILEN_OLD_CACHE = ShipSpeed.model_construct(
    game_name="Gatac Railen", scm_speed=170.0, quantum_speed=275_942_800.0, quantum_spool_time=8.0,
)


def _location(name, x, arrival_radius=None, system="stanton", type_="Manmade"):
    return LocationPosition(name=name, type=type_, system=system, x=x, y=0.0, z=0.0, arrival_radius=arrival_radius)


def test_old_cached_payload_has_none_for_new_fields():
    assert RAILEN_OLD_CACHE.quantum_stage_one_accel is None
    assert RAILEN_OLD_CACHE.hydrogen_capacity is None


def test_long_jump_adds_the_ramp_over_flat_speed():
    # The Bolon's ramp is ~31s each way covering ~4 Gm; past 2x that, the staged model
    # is flat speed plus a fixed ramp penalty.
    distance = 42 * METERS_PER_GM
    flat = distance / RAILEN.quantum_speed
    staged = _qt_cruise_seconds(distance, RAILEN)
    assert staged - flat == pytest.approx(33.3, abs=0.5)


def test_short_hop_is_far_slower_than_flat_speed():
    # The case the flat model got most wrong: a ~2 Gm moon hop never reaches full speed.
    distance = 1.91 * METERS_PER_GM
    assert distance / RAILEN.quantum_speed == pytest.approx(6.9, abs=0.1)
    assert _qt_cruise_seconds(distance, RAILEN) == pytest.approx(32.1, abs=0.5)


def test_staged_model_is_continuous_at_the_full_speed_boundary():
    # Just short of and just past 2x the ramp distance should agree — a mismatch there
    # means the short-hop branch and the cruise branch disagree about the ramp.
    engage = RAILEN.quantum_engage_speed
    ramp_m = (
        0.5 * engage ** 2 / RAILEN.quantum_stage_one_accel
        + (engage + RAILEN.quantum_speed) / 2 * (RAILEN.quantum_speed - engage) / RAILEN.quantum_stage_two_accel
    )
    below = _qt_cruise_seconds(2 * ramp_m - 1, RAILEN)
    above = _qt_cruise_seconds(2 * ramp_m + 1, RAILEN)
    assert below == pytest.approx(above, abs=0.01)


def test_missing_drive_stages_fall_back_to_flat_speed():
    distance = 10 * METERS_PER_GM
    assert _qt_cruise_seconds(distance, RAILEN_OLD_CACHE) == distance / RAILEN.quantum_speed


def test_short_approach_never_reaches_max_speed():
    # 24 km is shorter than the ~34.5 km full accel + decel takes, so the profile peaks.
    seconds, hydrogen = _approach(24_000, RAILEN)
    assert seconds == pytest.approx(57.5, abs=0.5)
    assert seconds < 25.9 + 43.05
    assert hydrogen == pytest.approx(30_797 * HYDROGEN_USAGE_UNIT_SCU, rel=0.01)


def test_long_approach_cruises_between_burns():
    seconds, hydrogen = _approach(100_000, RAILEN)
    cruise_m = 100_000 - 0.5 * 1000 * 25.9 - 0.5 * 1000 * 43.05
    assert seconds == pytest.approx(25.9 + 43.05 + cruise_m / 1000)
    # No thrust while cruising — the burn is the same as a bare 0 -> max -> 0.
    assert hydrogen == pytest.approx((712.5 * 25.9 + 428.75 * 43.05) * HYDROGEN_USAGE_UNIT_SCU)


def test_approach_without_speed_curve_uses_scm_and_unknown_burn():
    seconds, hydrogen = _approach(24_000, RAILEN_OLD_CACHE)
    assert seconds == pytest.approx(24_000 / 170.0)
    assert hydrogen is None


def test_hop_drops_out_at_the_destination_arrival_radius():
    trip = _TripBuilder(RAILEN)
    station = _location("Baijini Point", 0.0, arrival_radius=24_000)
    trip.add_hop(42 * METERS_PER_GM, station)
    estimate = trip.build()

    assert estimate.qt_seconds == pytest.approx(_qt_cruise_seconds(42 * METERS_PER_GM - 24_000, RAILEN) + 8.0)
    assert estimate.approach_seconds == pytest.approx(_approach(24_000, RAILEN)[0])
    assert estimate.seconds == pytest.approx(estimate.qt_seconds + estimate.approach_seconds)
    # The drop-out splits the distance; it doesn't add or lose any.
    assert estimate.qt_distance_m == pytest.approx(42 * METERS_PER_GM - 24_000)
    assert estimate.approach_distance_m == 24_000
    assert estimate.distance_m == pytest.approx(42 * METERS_PER_GM)


def test_qt_fuel_matches_the_drives_scu_per_gm():
    # Bolon: 0.00654 SCU/Gm, 8.8 SCU tank.
    trip = _TripBuilder(RAILEN)
    trip.add_hop(42 * METERS_PER_GM, None)
    estimate = trip.build()
    assert estimate.qt_fuel_scu == pytest.approx(42 * 0.00654, rel=0.001)
    assert estimate.qt_fuel_pct == pytest.approx(100 * 42 * 0.00654 / 8.8, rel=0.001)


def test_hop_inside_arrival_radius_skips_the_jump():
    trip = _TripBuilder(RAILEN)
    trip.add_hop(15_000, _location("Orison", 0.0, arrival_radius=20_000, type_="LandingZone"))
    estimate = trip.build()
    assert estimate.qt_seconds == 0.0
    assert estimate.approach_seconds == pytest.approx(_approach(15_000, RAILEN)[0])


def test_hop_without_arrival_radius_is_all_quantum():
    trip = _TripBuilder(RAILEN)
    trip.add_hop(10 * METERS_PER_GM, _location("Somewhere", 0.0))
    estimate = trip.build()
    assert estimate.approach_seconds == 0.0
    assert estimate.hydrogen_scu == 0.0


def test_old_cache_reports_unknown_fuel_not_zero():
    trip = _TripBuilder(RAILEN_OLD_CACHE)
    trip.add_hop(10 * METERS_PER_GM, _location("Station", 0.0, arrival_radius=24_000))
    estimate = trip.build()
    assert estimate.qt_fuel_pct is None
    assert estimate.hydrogen_pct is None


class _FakeWikiClient:
    """One line of locations: origin at 0, Pyro Gateway 30 Gm out, its jump point 40 km
    beyond that; in Pyro, the exit point at 0 and the destination 10 Gm away."""

    def __init__(self, with_gateway=True):
        self.origin = _location("Origin Station", 0.0, arrival_radius=24_000)
        self.gateway = _location("Pyro Gateway", 30 * METERS_PER_GM, arrival_radius=24_000)
        self.origin_jump_point = _location("Stanton-Pyro Jump Point", 30 * METERS_PER_GM + 40_000, type_="Anomaly")
        self.exit_jump_point = _location("Pyro-Stanton Jump Point", 0.0, system="pyro", type_="Anomaly")
        self.destination = _location("Destination Station", 10 * METERS_PER_GM, arrival_radius=24_000, system="pyro")
        self.with_gateway = with_gateway

    async def find_location(self, name, system):
        return {"Origin Station": self.origin, "Destination Station": self.destination}.get(name)

    async def find_gateway(self, from_system, to_system):
        return self.gateway if self.with_gateway else None

    async def nearest_jump_point(self, location):
        return self.origin_jump_point

    async def find_jump_point(self, from_system, to_system):
        return self.origin_jump_point if from_system == "Stanton" else self.exit_jump_point


def _terminal(station, system):
    return SimpleNamespace(
        space_station_name=station, outpost_name=None, city_name=None, displayname=station,
        nickname=None, moon_name=None, planet_name=None, star_system_name=system,
    )


async def test_cross_system_flies_from_the_gateway_to_the_jump_point():
    client = _FakeWikiClient()
    estimate = await _estimate_cross_system(
        client, _terminal("Origin Station", "Stanton"), _terminal("Destination Station", "Pyro"),
        RAILEN, "Origin Station", "Destination Station",
    )

    expected_qt = (
        _qt_cruise_seconds(30 * METERS_PER_GM - 24_000, RAILEN) + 8.0
        + _qt_cruise_seconds(10 * METERS_PER_GM - 24_000, RAILEN) + 8.0
    )
    # Gateway approach: its own 24 km drop-out plus the 40 km on to the jump point.
    expected_approach = _approach(64_000, RAILEN)[0] + _approach(24_000, RAILEN)[0]
    assert estimate.qt_seconds == pytest.approx(expected_qt)
    assert estimate.approach_seconds == pytest.approx(expected_approach)
    # Origin to jump point (30 Gm + 40 km), then exit point to destination (10 Gm).
    assert estimate.distance_m == pytest.approx(40 * METERS_PER_GM + 40_000)


async def test_cross_system_without_a_gateway_jumps_straight_to_the_point():
    client = _FakeWikiClient(with_gateway=False)
    estimate = await _estimate_cross_system(
        client, _terminal("Origin Station", "Stanton"), _terminal("Destination Station", "Pyro"),
        RAILEN, "Origin Station", "Destination Station",
    )
    expected_qt = (
        _qt_cruise_seconds(30 * METERS_PER_GM + 40_000, RAILEN) + 8.0
        + _qt_cruise_seconds(10 * METERS_PER_GM - 24_000, RAILEN) + 8.0
    )
    assert estimate.qt_seconds == pytest.approx(expected_qt)
    assert estimate.approach_seconds == pytest.approx(_approach(24_000, RAILEN)[0])
