"""Covers server/routes/ledger.py's HTTP wiring — that each route is actually behind
get_current_user, and that a ledger_service ValueError surfaces as a 400 with the
message intact (ledger_client.py's callers depend on getting that exact text back, see
its _raise_for_status). Uses FastAPI's dependency_overrides for auth rather than a real
JWT — server/test_auth.py already covers the auth relay itself.
"""
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from db.models import LegType, TradeLeg, TradeRun, User
from server import ledger_service
from server.dependencies import get_current_user
from server.main import app


def _make_leg(**overrides):
    fields = dict(
        id=uuid.uuid4(), run_id=uuid.uuid4(), user_id=uuid.uuid4(), leg_type=LegType.ACQUISITION,
        terminal_id=1, terminal_name="Test Terminal", commodity_name="Copper", quantity_scu=10,
        price_per_unit=5, cargo_transfer_type="manual", cargo_transfer_fee=0,
        created_at=datetime.now(UTC), started_at=None, reached_at=None,
        transaction_completed_at=None, transferred_at=None, finalized_at=None,
    )
    fields.update(overrides)
    return TradeLeg(**fields)


def _make_run(legs, **overrides):
    fields = dict(
        id=uuid.uuid4(), user_id=uuid.uuid4(), ship="Railen", usable_container_sizes="1,2,4",
        created_at=datetime.now(UTC), finalized_at=None, legs=legs,
    )
    fields.update(overrides)
    return TradeRun(**fields)


def test_routes_require_authentication():
    client = TestClient(app)

    response = client.get("/trade-runs/in-progress")

    assert response.status_code in (401, 403)


def test_list_in_progress_runs_returns_the_service_result(monkeypatch):
    fake_user = User(id=uuid.uuid4(), discord_id="123")
    app.dependency_overrides[get_current_user] = lambda: fake_user

    async def fake_get_in_progress_runs(user_id):
        assert user_id == fake_user.id
        return [_make_run([_make_leg()])]

    monkeypatch.setattr(ledger_service, "get_in_progress_runs", fake_get_in_progress_runs)

    try:
        client = TestClient(app)
        response = client.get("/trade-runs/in-progress")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["ship"] == "Railen"
    assert len(body[0]["legs"]) == 1


def test_update_transaction_returns_the_updated_leg(monkeypatch):
    fake_user = User(id=uuid.uuid4(), discord_id="123")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    leg_id = uuid.uuid4()

    async def fake_update_transaction(
            given_leg_id, quantity_scu, price_per_unit, cargo_transfer_fee, cargo_transfer_type, *, user_id,
    ):
        assert given_leg_id == leg_id
        assert user_id == fake_user.id
        assert quantity_scu == 99
        return _make_leg(id=leg_id, quantity_scu=99, transaction_completed_at=datetime.now(UTC))

    monkeypatch.setattr(ledger_service, "update_transaction", fake_update_transaction)

    try:
        client = TestClient(app)
        response = client.patch(f"/trade-runs/legs/{leg_id}/transaction", json={"quantity_scu": 99})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json()["quantity_scu"] == 99


def test_update_transaction_surfaces_a_value_error_as_400(monkeypatch):
    fake_user = User(id=uuid.uuid4(), discord_id="123")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    leg_id = uuid.uuid4()

    async def fake_update_transaction(*args, **kwargs):
        raise ValueError(f"Trade leg {leg_id} has no recorded transaction yet")

    monkeypatch.setattr(ledger_service, "update_transaction", fake_update_transaction)

    try:
        client = TestClient(app)
        response = client.patch(f"/trade-runs/legs/{leg_id}/transaction", json={"quantity_scu": 99})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 400
    assert "has no recorded transaction yet" in response.json()["detail"]


def test_advance_leg_surfaces_a_value_error_as_400(monkeypatch):
    fake_user = User(id=uuid.uuid4(), discord_id="123")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    leg_id = uuid.uuid4()

    async def fake_advance_leg(given_leg_id, *, user_id):
        assert given_leg_id == leg_id
        raise ValueError(f"Trade leg {leg_id} is already finalized")

    monkeypatch.setattr(ledger_service, "advance_leg", fake_advance_leg)

    try:
        client = TestClient(app)
        response = client.post(f"/trade-runs/legs/{leg_id}/advance")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 400
    assert "already finalized" in response.json()["detail"]


# Ownership at the HTTP layer: every id-based mutating route must hand the JWT's user.id
# to the service and turn its NotFoundError into a 404 (not 403 — that would confirm the
# id belongs to someone). The real service is used against a fake session holding a
# run/leg owned by a different pilot, so this covers routes + service together.

class _OtherPilotsSession:
    def __init__(self, record):
        self._record = record
        self.committed = False
        self.deleted = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, entity_id):
        return self._record

    async def execute(self, stmt):
        record = self._record

        class _Result:
            def scalar_one_or_none(self):
                return record

        return _Result()

    async def delete(self, obj):
        self.deleted = obj

    async def commit(self):
        self.committed = True


_TXN = {"quantity_scu": 5, "price_per_unit": 7, "cargo_transfer_type": "manual", "cargo_transfer_fee": 0}


@pytest.mark.parametrize(("method", "path", "json", "kind"), [
    ("post", "/trade-runs/{id}/finalize", None, "run"),
    ("delete", "/trade-runs/{id}", None, "run"),
    ("post", "/trade-runs/legs/{id}/advance", None, "leg"),
    ("post", "/trade-runs/legs/{id}/purchase", _TXN, "leg"),
    ("post", "/trade-runs/legs/{id}/sale", _TXN, "sale_leg"),
    ("patch", "/trade-runs/legs/{id}/transaction", {"quantity_scu": 999}, "done_leg"),
])
def test_mutating_routes_return_404_for_another_pilots_record(monkeypatch, method, path, json, kind):
    caller = User(id=uuid.uuid4(), discord_id="caller")
    owner_id = uuid.uuid4()
    record = {
        "run": lambda: _make_run([], user_id=owner_id),
        "leg": lambda: _make_leg(user_id=owner_id, started_at=datetime.now(UTC)),
        "sale_leg": lambda: _make_leg(user_id=owner_id, leg_type=LegType.SALE),
        "done_leg": lambda: _make_leg(user_id=owner_id, transaction_completed_at=datetime.now(UTC)),
    }[kind]()
    session = _OtherPilotsSession(record)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)
    app.dependency_overrides[get_current_user] = lambda: caller

    try:
        client = TestClient(app)
        response = client.request(method, path.format(id=record.id), json=json)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 404
    assert str(record.id) in response.json()["detail"]
    assert session.committed is False
    assert session.deleted is None
