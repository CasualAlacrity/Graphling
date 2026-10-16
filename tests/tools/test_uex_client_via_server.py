"""Covers UEXCorpClient's two modes (tools/uexcorp/client.py): with via_server=True (the
desktop client) every lookup goes to the server's cache through uex_cache_client and
never to UEX; without it (the server) it calls UEX directly. Plus the per-kind cache
lifetimes in tools/uexcorp/price_cache.py."""
from datetime import UTC, datetime, timedelta

import pytest

import uex_cache_client
from db.models import UexCacheKind, UexPriceCache
from tools.uexcorp import client as client_module
from tools.uexcorp import price_cache
from tools.uexcorp.client import UexCallBudgetExhausted, UEXCorpClient


@pytest.fixture
def server_calls(monkeypatch):
    calls = []

    def recorder(name):
        async def fake(*args, **kwargs):
            calls.append((name, args))
            return [{"from": name}]
        return fake

    for name in ("get_commodity_price_rows", "get_terminal_price_rows", "get_commodity_route_rows",
                 "get_item_price_rows", "get_vehicle_purchase_price_rows", "get_vehicle_rental_price_rows",
                 "get_orbit_distance_rows"):
        monkeypatch.setattr(uex_cache_client, name, recorder(name))
    return calls


@pytest.fixture
def no_direct_uex(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the desktop client must never call UEX directly")

    monkeypatch.setattr(client_module.requests, "get", refuse)


async def test_via_server_every_lookup_goes_to_the_server_cache(server_calls, no_direct_uex):
    client = UEXCorpClient(via_server=True)

    await client.get_commodity_prices(1)
    await client.get_terminal_prices(2)
    await client.get_commodity_routes(commodity_id=3)
    await client.get_item_prices(4)
    await client.get_vehicle_purchase_prices(5)
    await client.get_vehicle_rental_prices(6)
    await client.get_orbit_distances(7, 68)
    await client.get_orbit_distances(7, 68)  # memoized in-process: still one server call

    assert server_calls == [
        ("get_commodity_price_rows", (1,)),
        ("get_terminal_price_rows", (2,)),
        ("get_commodity_route_rows", (3,)),
        ("get_item_price_rows", (4,)),
        ("get_vehicle_purchase_price_rows", (5,)),
        ("get_vehicle_rental_price_rows", (6,)),
        ("get_orbit_distance_rows", (7, 68)),
    ]


async def test_via_server_route_filters_the_server_cant_serve_are_refused(server_calls, no_direct_uex):
    with pytest.raises(NotImplementedError):
        await UEXCorpClient(via_server=True).get_commodity_routes(origin_terminal_id=9)


async def test_the_server_side_client_calls_uex_directly(monkeypatch, server_calls):
    requested = []

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": 1}]}

    def fake_get(url, params=None, headers=None, timeout=None):
        requested.append((url, params, headers))
        return _Response()

    monkeypatch.setattr(client_module.requests, "get", fake_get)

    rows = await UEXCorpClient(api_key="k", bearer_token="t").get_item_prices(4)

    assert rows == [{"id": 1}]
    assert requested[0][0].endswith("items_prices")
    assert requested[0][2] == {"Authorization": "Bearer t"}
    assert server_calls == []


class _Session:
    def __init__(self, row):
        self._row = row

    async def execute(self, stmt):
        row = self._row

        class _Result:
            def scalar_one_or_none(self):
                return row

        return _Result()


def _row(kind, age):
    return UexPriceCache(kind=kind, entity_id=1, rows=[{"cached": True}], fetched_at=datetime.now(UTC) - age)


@pytest.mark.parametrize(("kind", "age", "fresh"), [
    (UexCacheKind.COMMODITY, timedelta(minutes=20), True),
    (UexCacheKind.COMMODITY, timedelta(hours=1), False),
    # Outlives the hourly server prefetch (server/uex_route_prefetch.py).
    (UexCacheKind.ROUTE_BY_ORIGIN, timedelta(hours=1), True),
    (UexCacheKind.ROUTE_BY_ORIGIN, timedelta(hours=2), False),
    (UexCacheKind.ITEM, timedelta(days=3), True),
    (UexCacheKind.VEHICLE_PURCHASE, timedelta(days=3), True),
    (UexCacheKind.VEHICLE_RENTAL, timedelta(days=8), False),
    (UexCacheKind.ORBIT_DISTANCES, timedelta(days=6), True),
])
async def test_cache_lifetime_depends_on_how_often_the_data_changes(kind, age, fresh):
    rows = await price_cache._get_cached_rows(_Session(_row(kind, age)), kind, 1)
    assert (rows is not None) == fresh


def test_every_cache_kind_has_a_lifetime():
    assert set(price_cache.CACHE_TTL) == set(UexCacheKind)


async def test_the_server_side_client_reports_each_call_before_making_it(monkeypatch):
    order = []

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": []}

    def fake_get(url, params=None, headers=None, timeout=None):
        order.append("request")
        return _Response()

    async def before_call(calls):
        order.append(("counted", calls))

    monkeypatch.setattr(client_module.requests, "get", fake_get)

    await UEXCorpClient(bearer_token="t", before_call=before_call).get_terminal_prices(2)

    assert order == [("counted", 1), "request"]


async def test_a_refused_call_never_reaches_uex(monkeypatch):
    def fake_get(*args, **kwargs):
        raise AssertionError("UEX was called past the budget")

    async def refuse(calls):
        raise UexCallBudgetExhausted("spent")

    monkeypatch.setattr(client_module.requests, "get", fake_get)

    with pytest.raises(UexCallBudgetExhausted):
        await UEXCorpClient(bearer_token="t", before_call=refuse).get_terminal_prices(2)


class _StaleSession:
    """One cached row, two hours old: stale for any player-driven kind."""

    def __init__(self, kind):
        self._row = _row(kind, timedelta(hours=2))
        self.stored = False

    async def execute(self, stmt):
        row = self._row

        class _Result:
            def scalar_one_or_none(self):
                return row

        return _Result()

    async def commit(self):
        self.stored = True


class _RefusingClient:
    async def get_terminal_prices(self, terminal_id):
        raise UexCallBudgetExhausted("spent")

    async def get_commodity_routes(self, **kwargs):
        raise UexCallBudgetExhausted("spent")


async def test_with_uex_calls_spent_stale_rows_beat_no_answer():
    session = _StaleSession(UexCacheKind.TERMINAL)

    rows = await price_cache.get_terminal_price_rows(_RefusingClient(), session, 1)

    assert rows == [{"cached": True}]
    assert not session.stored


async def test_with_uex_calls_spent_a_search_origin_falls_back_to_stale_routes():
    rows = await price_cache.fetch_routes_from_terminal(
        _RefusingClient(), _StaleSession(UexCacheKind.ROUTE_BY_ORIGIN), 1
    )

    assert rows == [{"cached": True}]


async def test_with_uex_calls_spent_and_nothing_cached_the_refusal_propagates():
    with pytest.raises(UexCallBudgetExhausted):
        await price_cache.get_terminal_price_rows(_RefusingClient(), _Session(None), 1)
