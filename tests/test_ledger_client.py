"""Covers ledger_client.py — the desktop client's HTTP calls to the ledger server
(app/server/), replacing what used to be direct SQLAlchemy calls in db/trade_run_store.py
(docs/todo.md Phase 2). Exercises the real httpx.AsyncClient machinery against
httpx.MockTransport rather than mocking httpx itself, so a change to how requests are
built (headers, URLs, JSON bodies) is actually checked.
"""
import uuid
from datetime import UTC, datetime

import httpx
import pytest

import ledger_client
from db.models import CargoTransferType
from ledger_schemas import TradeLegOut, TradeRunOut
from tools.uexcorp.trade_data import UEXTradeRoute


def _leg_payload(**overrides):
    now = datetime.now(UTC).isoformat()
    payload = {
        "id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()), "leg_type": "acquisition",
        "terminal_id": 1, "terminal_name": "Test Terminal", "commodity_name": "Copper",
        "quantity_scu": 40, "price_per_unit": 12, "cargo_transfer_type": "manual",
        "cargo_transfer_fee": 0, "created_at": now, "started_at": now,
        "reached_at": None, "transaction_completed_at": None, "transferred_at": None, "finalized_at": None,
    }
    payload.update(overrides)
    return payload


def _run_payload(legs=None, **overrides):
    now = datetime.now(UTC).isoformat()
    payload = {
        "id": str(uuid.uuid4()), "ship": "Railen", "usable_container_sizes": "1,2,4",
        "created_at": now, "finalized_at": None, "legs": legs if legs is not None else [_leg_payload()],
    }
    payload.update(overrides)
    return payload


def _mock_client(monkeypatch, handler):
    async def _fake_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    monkeypatch.setattr(ledger_client, "authenticated_client", _fake_client)


async def test_get_in_progress_runs_hits_the_right_endpoint_and_parses_the_response(monkeypatch):
    requested = {}

    def handler(request):
        requested["url"] = str(request.url)
        return httpx.Response(200, json=[_run_payload()])

    _mock_client(monkeypatch, handler)

    result = await ledger_client.get_in_progress_runs()

    assert "/trade-runs/in-progress" in requested["url"]
    assert len(result) == 1
    assert isinstance(result[0], TradeRunOut)
    assert result[0].ship == "Railen"


async def test_get_finalized_runs_passes_the_limit_as_a_query_param(monkeypatch):
    requested = {}

    def handler(request):
        requested["url"] = str(request.url)
        return httpx.Response(200, json=[])

    _mock_client(monkeypatch, handler)

    await ledger_client.get_finalized_runs(limit=10)

    assert "limit=10" in requested["url"]


async def test_create_run_from_route_sends_the_route_and_returns_a_run(monkeypatch):
    route = UEXTradeRoute(
        id_commodity=10, commodity_name="Agricium",
        id_terminal_origin=1, origin_terminal_name="Orison TDD",
        origin_star_system_name="Stanton", origin_planet_name="Crusader",
        id_terminal_destination=2, destination_terminal_name="Seraphim Station",
        destination_star_system_name="Stanton", destination_planet_name="Crusader",
        price_origin=10.0, price_destination=20.0, price_margin=10.0,
        scu_origin=100, scu_destination=100, status_origin=2, status_destination=1,
        distance=10.0, is_on_ground_origin=0, is_on_ground_destination=0,
        is_auto_load_origin=0, is_auto_load_destination=0,
        container_sizes_origin=[1, 2, 4], container_sizes_destination=[1, 2, 4],
    )
    sent = {}

    def handler(request):
        sent["body"] = request.read()
        return httpx.Response(200, json=_run_payload())

    _mock_client(monkeypatch, handler)

    result = await ledger_client.create_run_from_route(route, 40, "Railen")

    assert isinstance(result, TradeRunOut)
    assert b'"quantity_scu":40' in sent["body"]


async def test_advance_leg_parses_the_leg_response(monkeypatch):
    now = datetime.now(UTC).isoformat()
    _mock_client(monkeypatch, lambda request: httpx.Response(200, json=_leg_payload(reached_at=now)))

    result = await ledger_client.advance_leg(uuid.uuid4())

    assert isinstance(result, TradeLegOut)
    assert result.reached_at is not None


async def test_a_400_response_raises_value_error_with_the_servers_message(monkeypatch):
    _mock_client(monkeypatch, lambda request: httpx.Response(400, json={"detail": "Trade leg is already finalized"}))

    with pytest.raises(ValueError, match="already finalized"):
        await ledger_client.advance_leg(uuid.uuid4())


async def test_record_purchase_sends_transaction_fields(monkeypatch):
    sent = {}

    def handler(request):
        sent["body"] = request.read()
        return httpx.Response(200, json=_leg_payload(transaction_completed_at=datetime.now(UTC).isoformat()))

    _mock_client(monkeypatch, handler)

    result = await ledger_client.record_purchase(uuid.uuid4(), 40, 12, CargoTransferType.AUTOLOAD, 5000)

    assert isinstance(result, TradeLegOut)
    assert b'"cargo_transfer_type":"autoload"' in sent["body"]


async def test_delete_run_raises_on_error_and_returns_none_on_success(monkeypatch):
    _mock_client(monkeypatch, lambda request: httpx.Response(204))

    assert await ledger_client.delete_run(uuid.uuid4()) is None


# catch_up_before_transaction — client-side orchestration over advance_leg, moved here
# from db/trade_run_store.py's tests since it now loops HTTP calls, not DB calls. Logic
# is unchanged: a completed purchase/sale is mechanically impossible without its
# purely-timestamp prerequisites already being true, so it's evidence for them, not a
# guess (see docs/todo.md's Phase 3 harness entry).

async def test_catch_up_before_transaction_is_a_noop_when_already_at_the_transaction_step(monkeypatch):
    leg = TradeLegOut.model_validate(_leg_payload(leg_type="acquisition", reached_at=datetime.now(UTC).isoformat()))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_leg_payload())

    _mock_client(monkeypatch, handler)

    result = await ledger_client.catch_up_before_transaction(leg)

    assert result is leg
    assert calls == []  # advance_leg was never called


async def test_catch_up_before_transaction_advances_reached_at_for_acquisition(monkeypatch):
    leg = TradeLegOut.model_validate(_leg_payload(leg_type="acquisition"))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_leg_payload(leg_type="acquisition", reached_at=datetime.now(UTC).isoformat()))

    _mock_client(monkeypatch, handler)

    result = await ledger_client.catch_up_before_transaction(leg)

    assert len(calls) == 1
    assert result.reached_at is not None


async def test_catch_up_before_transaction_advances_both_steps_for_a_fresh_manual_sale(monkeypatch):
    leg = TradeLegOut.model_validate(_leg_payload(leg_type="sale", cargo_transfer_type="manual"))
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        now = datetime.now(UTC).isoformat()
        if call_count == 1:
            return httpx.Response(200, json=_leg_payload(leg_type="sale", cargo_transfer_type="manual", reached_at=now))
        return httpx.Response(
            200, json=_leg_payload(leg_type="sale", cargo_transfer_type="manual", reached_at=now, transferred_at=now)
        )

    _mock_client(monkeypatch, handler)

    result = await ledger_client.catch_up_before_transaction(leg)

    assert call_count == 2
    assert result.reached_at is not None
    assert result.transferred_at is not None
