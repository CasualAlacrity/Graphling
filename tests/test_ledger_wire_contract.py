"""The desktop client and the ledger server are tested separately elsewhere, each against
its own mock of the other, which is how the create-run 422 slipped through: the client
re-sent a UEXTradeRoute dumped by field name (commodity_id) and the server's copy of the
model only accepted UEX's id_* aliases. This feeds the body ledger_client really builds
into the real FastAPI route, so the two can't drift apart unnoticed.
"""
import json
import uuid
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

import ledger_client
from db.models import User
from server import ledger_service
from server.dependencies import get_current_user
from server.main import app
from tools.uexcorp.trade_data import UEXTradeRoute

# A route row as UEX's commodities_routes endpoint returns it (aliased id_* keys, CSV sizes).
RAW_UEX_ROUTE = dict(
    id_commodity=1, commodity_name="Recycled Material Composite",
    id_terminal_origin=2, origin_terminal_name="Lorville L19",
    origin_star_system_name="Stanton", origin_planet_name="Hurston",
    id_terminal_destination=3, destination_terminal_name="Terra Mills",
    destination_star_system_name="Stanton", destination_planet_name="Crusader",
    price_origin=7587, price_destination=8900, price_margin=14.8,
    scu_origin=1000, scu_destination=1000, status_origin=3, status_destination=3,
    distance=10, is_on_ground_origin=0, is_on_ground_destination=0,
    container_sizes_origin="1,2,4,8", container_sizes_destination="1,2",
)


async def test_create_run_body_from_the_client_is_accepted_by_the_server_route(monkeypatch):
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    async def fake_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    monkeypatch.setattr(ledger_client, "authenticated_client", fake_client)
    route = UEXTradeRoute.model_validate(RAW_UEX_ROUTE)
    try:
        await ledger_client.create_run_from_route(route, 240, "Constellation")
    except Exception:
        pass  # the mock's empty response isn't a valid TradeRunOut; only the request matters

    received = {}

    async def fake_create(route, quantity_scu, ship, user_id):
        received["route"] = route
        raise RuntimeError("stop before touching the database")

    monkeypatch.setattr(ledger_service, "create_run_from_route", fake_create)
    app.dependency_overrides[get_current_user] = lambda: User(id=uuid.uuid4(), discord_id="1")
    try:
        response = TestClient(app, raise_server_exceptions=False).post("/trade-runs", json=sent["body"])
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code != 422, response.text
    assert received["route"] == route
