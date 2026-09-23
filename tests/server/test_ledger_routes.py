"""Covers server/routes/ledger.py's HTTP wiring — that each route is actually behind
get_current_user, and that a ledger_service ValueError surfaces as a 400 with the
message intact (ledger_client.py's callers depend on getting that exact text back, see
its _raise_for_status). Uses FastAPI's dependency_overrides for auth rather than a real
JWT — server/test_auth.py already covers the auth relay itself.
"""
import uuid
from datetime import UTC, datetime

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


def test_advance_leg_surfaces_a_value_error_as_400(monkeypatch):
    fake_user = User(id=uuid.uuid4(), discord_id="123")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    leg_id = uuid.uuid4()

    async def fake_advance_leg(given_leg_id):
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
