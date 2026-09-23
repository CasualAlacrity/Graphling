"""Covers wiki_cache_client.py — the desktop client's HTTP calls to the server's wiki
cache (app/server/wiki_cache_service.py), same style as tests/test_uex_cache_client.py.
"""
import httpx

import wiki_cache_client


def _mock_client(monkeypatch, handler):
    async def _fake_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    monkeypatch.setattr(wiki_cache_client, "authenticated_client", _fake_client)


async def test_get_ship_speed_sends_the_name_as_a_query_param(monkeypatch):
    # Ship names contain spaces ("Gatac Railen") — this is the regression a bare
    # f-string path segment would hit.
    requested = {}

    def handler(request):
        requested["url"] = str(request.url)
        return httpx.Response(200, json={"game_name": "Gatac Railen", "scm_speed": 100.0,
                                          "quantum_speed": 1000.0, "quantum_spool_time": 5.0})

    _mock_client(monkeypatch, handler)

    result = await wiki_cache_client.get_ship_speed("Gatac Railen")

    assert "ship_name=Gatac+Railen" in requested["url"] or "ship_name=Gatac%20Railen" in requested["url"]
    assert result.game_name == "Gatac Railen"


async def test_get_ship_speed_returns_none_for_a_miss(monkeypatch):
    # httpx.Response(200, json=None) leaves the body empty rather than writing the
    # literal "null" — content= is what actually puts a JSON null on the wire.
    _mock_client(monkeypatch, lambda request: httpx.Response(200, content=b"null"))

    result = await wiki_cache_client.get_ship_speed("Not A Real Ship")

    assert result is None


async def test_get_locations_parses_a_list(monkeypatch):
    payload = [{"name": "Orison", "type": "City", "system": "Stanton", "x": 1.0, "y": 2.0, "z": 3.0}]
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json=payload))

    result = await wiki_cache_client.get_locations()

    assert len(result) == 1
    assert result[0].name == "Orison"
