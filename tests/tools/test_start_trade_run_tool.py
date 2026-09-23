"""Covers StartTradeRunTool's route_token -> run creation path and the round-trip
through route_cache it depends on. This is the first cross-turn dependency in the tool
surface: best_route stashes a token, and a later turn's start_trade_run must resolve
*that exact* one — the regression this guards is a model paraphrasing or dropping the
token instead of copying it, or the plumbing losing data along the way (autoload flag,
quantity override, zero-stock guard). See docs/start-route-tool.md "Tests still to add".
"""
import uuid

from db import trade_run_store
from db.models import CargoTransferType
from tools.trade_run import route_cache
from tools.trade_run.start_trade_run_tool import StartTradeRunTool
from tools.uexcorp.trade_data import UEXTradeRoute

_PILOT_ID = uuid.uuid4()


def _route(is_auto_load_origin=1, is_auto_load_destination=0, **overrides):
    fields = dict(
        id_commodity=10, commodity_name="Agricium",
        id_terminal_origin=1, origin_terminal_name="Orison TDD",
        origin_star_system_name="Stanton", origin_planet_name="Crusader",
        id_terminal_destination=2, destination_terminal_name="Seraphim Station",
        destination_star_system_name="Stanton", destination_planet_name="Crusader",
        price_origin=10.0, price_destination=20.0, price_margin=10.0,
        scu_origin=100, scu_destination=100, status_origin=2, status_destination=1,
        distance=10.0, is_on_ground_origin=0, is_on_ground_destination=0,
        is_auto_load_origin=is_auto_load_origin, is_auto_load_destination=is_auto_load_destination,
        container_sizes_origin=[1, 2, 4, 8, 16, 24, 32], container_sizes_destination=[1, 2, 4, 8, 16, 24, 32],
    )
    fields.update(overrides)
    return UEXTradeRoute(**fields)


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeSession:
    """Just enough of an AsyncSession for create_run_from_route: capture the run passed
    to add(), then hand that same object back on the post-commit re-fetch — the real
    re-fetch exists to eager-load `legs`, but the fake's run already has them attached
    before add() ever runs."""

    def __init__(self):
        self.added = None
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added = obj

    async def commit(self):
        self.committed = True

    async def execute(self, stmt):
        return _FakeResult(self.added)


async def test_happy_path_creates_run_with_correct_cargo_transfer_type(monkeypatch):
    # Regression coverage for the real bug find_best_route had (docs/todo.md Phase 1):
    # is_auto_load_origin/destination weren't being patched onto returned routes, so
    # every acquisition leg came out MANUAL even at an autoload terminal. This exercises
    # the real create_run_from_route, not a stub, so that mapping is actually checked.
    route = _route(is_auto_load_origin=1, is_auto_load_destination=0)
    token = route_cache.stash(route, 40, "Railen")
    monkeypatch.setattr(trade_run_store, "get_in_progress_runs", _async_return([]))
    monkeypatch.setattr(trade_run_store, "get_current_user_id", lambda: _PILOT_ID)
    session = _FakeSession()
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: session)

    tool = StartTradeRunTool()
    message = await tool._arun(route_token=token)

    assert session.committed
    run = session.added
    assert run.user_id == _PILOT_ID
    assert len(run.legs) == 2
    acquisition = next(leg for leg in run.legs if leg.leg_type.value == "acquisition")
    sale = next(leg for leg in run.legs if leg.leg_type.value == "sale")
    assert acquisition.cargo_transfer_type == CargoTransferType.AUTOLOAD
    assert sale.cargo_transfer_type == CargoTransferType.MANUAL
    assert acquisition.quantity_scu == 40
    assert sale.quantity_scu == 40
    assert acquisition.user_id == _PILOT_ID
    assert sale.user_id == _PILOT_ID
    assert run.ship == "Railen"
    assert "Run started" in message


async def test_unknown_token_returns_not_available_message_and_creates_no_run(monkeypatch):
    calls = []

    async def fake_create_run_from_route(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(trade_run_store, "create_run_from_route", fake_create_run_from_route)

    tool = StartTradeRunTool()
    message = await tool._arun(route_token="not-a-real-token")

    assert "isn't available anymore" in message
    assert calls == []


async def test_quantity_override_replaces_cached_scu_hint(monkeypatch):
    route = _route()
    token = route_cache.stash(route, 40, "Railen")
    monkeypatch.setattr(trade_run_store, "get_in_progress_runs", _async_return([]))
    monkeypatch.setattr(trade_run_store, "get_current_user_id", lambda: _PILOT_ID)
    session = _FakeSession()
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: session)

    tool = StartTradeRunTool()
    await tool._arun(route_token=token, quantity_scu=15)

    assert all(leg.quantity_scu == 15 for leg in session.added.legs)


async def test_cached_scu_hint_used_when_quantity_not_overridden(monkeypatch):
    route = _route()
    token = route_cache.stash(route, 40, "Railen")
    monkeypatch.setattr(trade_run_store, "get_in_progress_runs", _async_return([]))
    monkeypatch.setattr(trade_run_store, "get_current_user_id", lambda: _PILOT_ID)
    session = _FakeSession()
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: session)

    tool = StartTradeRunTool()
    await tool._arun(route_token=token)

    assert all(leg.quantity_scu == 40 for leg in session.added.legs)


async def test_zero_scu_guard_blocks_run_creation(monkeypatch):
    route = _route()
    token = route_cache.stash(route, 0, "Railen")
    calls = []

    async def fake_create_run_from_route(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(trade_run_store, "create_run_from_route", fake_create_run_from_route)

    tool = StartTradeRunTool()
    message = await tool._arun(route_token=token)

    assert "can't fill any cargo" in message
    assert calls == []


def test_route_cache_stash_get_round_trip():
    route = _route()

    token = route_cache.stash(route, 40, "Railen")
    cached_route, scu, vehicle_name = route_cache.get(token)

    assert cached_route is route
    assert scu == 40
    assert vehicle_name == "Railen"


def test_route_cache_get_returns_none_for_unknown_token():
    assert route_cache.get("not-a-real-token") is None


def test_route_cache_get_does_not_pop_on_read():
    # A failed commit (e.g. start_trade_run hitting an unrelated error after lookup)
    # shouldn't force the pilot to search again for the same route.
    route = _route()
    token = route_cache.stash(route, 40, "Railen")

    route_cache.get(token)

    assert route_cache.get(token) is not None
