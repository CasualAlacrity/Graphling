"""Covers find_best_route's runner-up bookkeeping — specifically that runner_up_scu is
the runner-up's own reachable load and not a leftover of the winner's.

The regression this guards (fixed 2026-09-18): best_route names a runner-up out loud, so
the pilot can pick it, so start_trade_run needs its load size to commit it. Tracking only
best_scu meant the second route was describable but not startable.
"""
from types import SimpleNamespace

from tools.route_ranking import find_best_route

FIXED_TRAVEL_SECONDS = 100.0


def _route_row(commodity_name, destination_id, destination_name, scu, margin):
    """A commodities_routes payload row. Container sizes are all 1 SCU so reachable_scu
    lands exactly on the stock ceiling rather than on a packing remainder — the test is
    about which scu gets carried where, not about the packing DP."""
    return {
        "id_commodity": 1, "commodity_name": commodity_name,
        "id_terminal_origin": 100, "origin_terminal_name": "Origin",
        "origin_star_system_name": "Stanton", "origin_planet_name": "Hurston",
        "id_terminal_destination": destination_id, "destination_terminal_name": destination_name,
        "destination_star_system_name": "Stanton", "destination_planet_name": "Hurston",
        "price_origin": 100.0, "price_destination": 100.0 + margin, "price_margin": margin,
        "scu_origin": scu, "scu_destination": scu,
        "status_origin": 2, "status_destination": 1,
        "distance": 10,
        "is_on_ground_origin": 0, "is_on_ground_destination": 0,
        "container_sizes_origin": "1", "container_sizes_destination": "1",
    }


# Two routes commonly DO reach the same SCU — both filling the ship is the ordinary case,
# covered by test_equal_scu_is_reported_for_both below. These two are stock-limited to
# different loads on purpose, which catches a narrower thing: a "fix" that sets
# runner_up_scu = best_scu would satisfy every equal-load test forever while still
# misreporting whenever the loads genuinely differ.
WINNER = _route_row("Titanium", 200, "Rich Dest", scu=64, margin=100.0)
RUNNER_UP = _route_row("Aluminium", 201, "Lean Dest", scu=32, margin=50.0)

# Ample stock at both ends, so each is capped by the ship's hold instead — a Railen's 96.
RAILEN_SCU = 96
FILLS_SHIP_RICH = _route_row("Iron", 202, "Rich Dest", scu=200, margin=100.0)
FILLS_SHIP_LEAN = _route_row("Aluminium", 203, "Lean Dest", scu=200, margin=50.0)


class _FakeUexClient:
    def __init__(self, rows):
        self._rows = rows

    async def get_commodity_routes(self, commodity_id=None, origin_terminal_id=None):
        return self._rows


def _cache():
    terminals = [
        SimpleNamespace(id=100, is_auto_load=1),
        SimpleNamespace(id=200, is_auto_load=1),
        SimpleNamespace(id=201, is_auto_load=0),
    ]
    return SimpleNamespace(terminals=terminals)


async def _rank(monkeypatch, rows, ship_scu=200):
    async def _fixed_travel(*args, **kwargs):
        return FIXED_TRAVEL_SECONDS

    monkeypatch.setattr("tools.route_ranking.estimate_travel_time", _fixed_travel)
    return await find_best_route(
        _FakeUexClient(rows), None, [100], "Railen", ship_scu, _cache(), commodity_id=1,
    )


async def test_runner_up_carries_its_own_scu(monkeypatch):
    """Winner seen first — the runner-up is assigned in the elif branch."""
    best, _, best_scu, runner_up, _, runner_up_scu = await _rank(monkeypatch, [WINNER, RUNNER_UP])

    assert best.commodity_name == "Titanium"
    assert runner_up.commodity_name == "Aluminium"
    assert best_scu == 64
    assert runner_up_scu == 32


async def test_runner_up_scu_survives_being_demoted(monkeypatch):
    """Runner-up seen first, so it is briefly the best and then demoted. This is the
    branch that dropped the scu: the demoted route's load has to move across with it
    before best_scu is overwritten by the new winner's."""
    best, _, best_scu, runner_up, _, runner_up_scu = await _rank(monkeypatch, [RUNNER_UP, WINNER])

    assert best.commodity_name == "Titanium"
    assert runner_up.commodity_name == "Aluminium"
    assert best_scu == 64
    assert runner_up_scu == 32


async def test_equal_scu_is_reported_for_both(monkeypatch):
    """The ordinary case: both routes have stock to spare, so both fill the ship and land
    on the same load. Nothing about ranking requires the two to differ — a full Railen of
    Iron and a full Railen of Aluminium are both valid options, and each is committable at
    that same 96."""
    best, _, best_scu, runner_up, _, runner_up_scu = await _rank(
        monkeypatch, [FILLS_SHIP_RICH, FILLS_SHIP_LEAN], ship_scu=RAILEN_SCU,
    )

    assert best.commodity_name == "Iron"
    assert runner_up.commodity_name == "Aluminium"
    assert best_scu == RAILEN_SCU
    assert runner_up_scu == RAILEN_SCU


async def test_single_candidate_has_no_runner_up(monkeypatch):
    best, _, best_scu, runner_up, runner_up_score, runner_up_scu = await _rank(monkeypatch, [WINNER])

    assert best.commodity_name == "Titanium"
    assert best_scu == 64
    assert runner_up is None
    assert runner_up_score is None
    assert runner_up_scu is None


async def test_autoload_is_patched_onto_both_routes(monkeypatch):
    """Guards the other latent bug this area had: is_auto_load_* aren't in the routes
    payload, so they default to 0 until find_best_route patches them from the terminal
    cache. Destination 200 is autoload-capable, 201 isn't."""
    best, _, _, runner_up, _, _ = await _rank(monkeypatch, [WINNER, RUNNER_UP])

    assert best.is_auto_load_origin == 1
    assert best.is_auto_load_destination == 1
    assert runner_up.is_auto_load_destination == 0
