"""Covers find_best_route's runner-up bookkeeping — specifically that runner_up_scu is
the runner-up's own reachable load and not a leftover of the winner's.

The regression this guards (fixed 2026-09-18): best_route names a runner-up out loud, so
the pilot can pick it, so start_trade_run needs its load size to commit it. Tracking only
best_scu meant the second route was describable but not startable.
"""
from types import SimpleNamespace

from tools.route_ranking import _route_rows_for, find_best_route

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

    async def _rows_for(_client, origin_terminal_ids):
        return rows, len(origin_terminal_ids)

    monkeypatch.setattr("tools.route_ranking.estimate_travel_time", _fixed_travel)
    # These cover ranking. The cache/fetch budgeting has its own tests below, and leaving
    # it in the path here would drag a live Postgres session into every ranking assertion.
    monkeypatch.setattr("tools.route_ranking._route_rows_for", _rows_for)
    return await find_best_route(
        _FakeUexClient(rows), None, [100], "Railen", ship_scu, _cache(), commodity_id=1,
    )


async def test_runner_up_carries_its_own_scu(monkeypatch):
    """Winner seen first — the runner-up is assigned in the elif branch."""
    result = await _rank(monkeypatch, [WINNER, RUNNER_UP])

    assert result.best.commodity_name == "Titanium"
    assert result.runner_up.commodity_name == "Aluminium"
    assert result.best_scu == 64
    assert result.runner_up_scu == 32


async def test_runner_up_scu_survives_being_demoted(monkeypatch):
    """Runner-up seen first, so it is briefly the best and then demoted. This is the
    branch that dropped the scu: the demoted route's load has to move across with it
    before best_scu is overwritten by the new winner's."""
    result = await _rank(monkeypatch, [RUNNER_UP, WINNER])

    assert result.best.commodity_name == "Titanium"
    assert result.runner_up.commodity_name == "Aluminium"
    assert result.best_scu == 64
    assert result.runner_up_scu == 32


async def test_equal_scu_is_reported_for_both(monkeypatch):
    """The ordinary case: both routes have stock to spare, so both fill the ship and land
    on the same load. Nothing about ranking requires the two to differ — a full Railen of
    Iron and a full Railen of Aluminium are both valid options, and each is committable at
    that same 96."""
    result = await _rank(
        monkeypatch, [FILLS_SHIP_RICH, FILLS_SHIP_LEAN], ship_scu=RAILEN_SCU,
    )

    assert result.best.commodity_name == "Iron"
    assert result.runner_up.commodity_name == "Aluminium"
    assert result.best_scu == RAILEN_SCU
    assert result.runner_up_scu == RAILEN_SCU


async def test_single_candidate_has_no_runner_up(monkeypatch):
    result = await _rank(monkeypatch, [WINNER])

    assert result.best.commodity_name == "Titanium"
    assert result.best_scu == 64
    assert result.runner_up is None
    assert result.runner_up_rate is None
    assert result.runner_up_scu is None


async def test_autoload_is_patched_onto_both_routes(monkeypatch):
    """Guards the other latent bug this area had: is_auto_load_* aren't in the routes
    payload, so they default to 0 until find_best_route patches them from the terminal
    cache. Destination 200 is autoload-capable, 201 isn't."""
    result = await _rank(monkeypatch, [WINNER, RUNNER_UP])

    assert result.best.is_auto_load_origin == 1
    assert result.best.is_auto_load_destination == 1
    assert result.runner_up.is_auto_load_destination == 0


# --- live-fetch budgeting / lazy cache population ----------------------------

class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


TEST_BUDGET = 8


def _budget_env(monkeypatch, cached_ids):
    """Patches the cache layer so no Postgres is involved, and pins the budget so these
    don't depend on whatever MAX_LIVE_ROUTE_FETCHES is set to locally. Returns the list
    recording which origins were fetched live."""
    fetched = []

    monkeypatch.setattr("tools.route_ranking.MAX_LIVE_ROUTE_FETCHES", TEST_BUDGET)

    async def _cached(_session, terminal_id):
        if terminal_id in cached_ids:
            return [{"origin": terminal_id, "source": "cache"}]
        return None

    async def _fetch(_client, _session, terminal_id):
        fetched.append(terminal_id)
        return [{"origin": terminal_id, "source": "live"}]

    monkeypatch.setattr("tools.route_ranking.SessionLocal", _FakeSession)
    monkeypatch.setattr("tools.route_ranking.cached_routes_from_terminal", _cached)
    monkeypatch.setattr("tools.route_ranking.fetch_routes_from_terminal", _fetch)
    return fetched


async def test_live_fetches_are_capped_but_cached_origins_are_free(monkeypatch):
    """The point of the budget: it limits calls to UEX, not terminals considered. 20
    origins with 10 already warm should search all 10 cached plus 8 fetched — 18 — while
    only paying for 8."""
    origins = list(range(20))
    cached_ids = set(range(10))
    fetched = _budget_env(monkeypatch, cached_ids)

    rows, searched = await _route_rows_for(object(), origins)

    assert len(fetched) == TEST_BUDGET
    assert searched == len(cached_ids) + TEST_BUDGET
    assert len(rows) == searched


async def test_only_uncached_origins_consume_the_budget(monkeypatch):
    """Warm origins must not be re-fetched — that's what makes coverage compound across
    searches instead of resetting."""
    origins = list(range(12))
    fetched = _budget_env(monkeypatch, cached_ids=set(range(12)))

    rows, searched = await _route_rows_for(object(), origins)

    assert fetched == []
    assert searched == 12
    assert all(row["source"] == "cache" for row in rows)


async def test_everything_is_searched_when_it_fits_in_the_budget(monkeypatch):
    origins = [1, 2, 3]
    fetched = _budget_env(monkeypatch, cached_ids=set())

    _rows, searched = await _route_rows_for(object(), origins)

    assert sorted(fetched) == origins
    assert searched == 3
