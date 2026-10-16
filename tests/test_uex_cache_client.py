"""Covers uex_cache_client.py — the desktop client's HTTP calls to the server's UEX
cache (app/server/uex_cache_service.py), replacing what used to be direct SQLAlchemy
calls in tools/uexcorp/client.py, tools/route_ranking.py, and overlay/uex_lookup.py
(docs/todo.md Phase 2's cache follow-up). Exercises the real httpx.AsyncClient machinery
against httpx.MockTransport, same style as tests/test_ledger_client.py.
"""
from datetime import UTC, datetime

import httpx

import uex_cache_client


def _reference_cache_payload():
    return {
        "fetched_at": datetime.now(UTC).isoformat(), "commodities": [], "star_systems": [], "orbits": [],
        "terminals": [], "moons": [], "item_categories": [], "items": [], "vehicles": [],
        "refinery_yields": [], "poi": [], "commodity_statuses": [],
    }


def _mock_client(monkeypatch, handler):
    async def _fake_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    monkeypatch.setattr(uex_cache_client, "authenticated_client", _fake_client)


async def test_get_reference_cache_hits_the_right_endpoint_and_parses_the_response(monkeypatch):
    requested = {}

    def handler(request):
        requested["url"] = str(request.url)
        return httpx.Response(200, json=_reference_cache_payload())

    _mock_client(monkeypatch, handler)

    result = await uex_cache_client.get_reference_cache()

    assert "/uex-cache/reference" in requested["url"]
    assert result.commodities == []


async def test_get_commodity_price_rows_hits_the_right_endpoint(monkeypatch):
    requested = {}

    def handler(request):
        requested["url"] = str(request.url)
        return httpx.Response(200, json=[{"id_commodity": 5}])

    _mock_client(monkeypatch, handler)

    result = await uex_cache_client.get_commodity_price_rows(5)

    assert "/uex-cache/commodity-prices/5" in requested["url"]
    assert result == [{"id_commodity": 5}]


async def test_cached_routes_from_terminals_asks_once_and_keys_rows_by_terminal_id(monkeypatch):
    requested = []

    def handler(request):
        requested.append((request.method, str(request.url), request.content))
        # JSON object keys are always strings on the wire.
        return httpx.Response(200, json={"100": [{"origin": 100}]})

    _mock_client(monkeypatch, handler)

    result = await uex_cache_client.cached_routes_from_terminals([100, 200])

    assert len(requested) == 1
    method, url, body = requested[0]
    assert method == "POST"
    assert "/uex-cache/routes-from-terminals/cached" in url
    assert body == b"[100,200]"
    assert result == {100: [{"origin": 100}]}


async def test_fetch_routes_from_terminal_posts_and_returns_rows(monkeypatch):
    requested = {}

    def handler(request):
        requested["method"] = request.method
        requested["url"] = str(request.url)
        return httpx.Response(200, json=[{"origin": 100}])

    _mock_client(monkeypatch, handler)

    result = await uex_cache_client.fetch_routes_from_terminal(100)

    assert requested["method"] == "POST"
    assert "/uex-cache/routes-from-terminal/100/fetch" in requested["url"]
    assert result == [{"origin": 100}]


async def test_static_price_and_distance_rows_hit_their_endpoints(monkeypatch):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, json=[{"ok": True}])

    _mock_client(monkeypatch, handler)

    await uex_cache_client.get_item_price_rows(11)
    await uex_cache_client.get_vehicle_purchase_price_rows(22)
    await uex_cache_client.get_vehicle_rental_price_rows(33)
    rows = await uex_cache_client.get_orbit_distance_rows(44, 68)

    assert rows == [{"ok": True}]
    assert requested[0].endswith("/uex-cache/item-prices/11")
    assert requested[1].endswith("/uex-cache/vehicle-purchase-prices/22")
    assert requested[2].endswith("/uex-cache/vehicle-rental-prices/33")
    assert requested[3].endswith("/uex-cache/orbit-distances/44?star_system_id=68")
