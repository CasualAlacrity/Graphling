"""Covers the DB-touching half of what used to be db/trade_run_store.py, moved to
server/ledger_service.py once a real server became the only thing that talks to
Postgres (docs/todo.md Phase 2) — the advance_leg/finalize_run guardrails that protect
callers (including AI tools, now one hop further away via ledger_client.py) from
silently corrupting an already-finished leg or run, record_purchase/record_sale, and
the multi-tenancy scoping (create_run_from_route stamps user_id; get_in_progress_runs/
get_finalized_runs filter by it) — now an explicit parameter instead of a process-global,
since a server serves concurrent requests from potentially different pilots."""
import uuid
from datetime import UTC, datetime

import pytest

from db.models import CargoTransferType, LegType, TradeLeg, TradeRun
from server import ledger_service


def _make_leg(leg_type, cargo_transfer_type=CargoTransferType.MANUAL, **overrides):
    fields = {
        "id": uuid.uuid4(),
        "leg_type": leg_type,
        "terminal_id": 1,
        "terminal_name": "Test Terminal",
        "commodity_name": "Test Commodity",
        "quantity_scu": 10,
        "price_per_unit": 5,
        "cargo_transfer_type": cargo_transfer_type,
        "cargo_transfer_fee": 0,
        "created_at": datetime.now(UTC),
        "started_at": None,
        "reached_at": None,
        "transaction_completed_at": None,
        "transferred_at": None,
        "finalized_at": None,
    }
    fields.update(overrides)
    return TradeLeg(**fields)


def _make_run(legs, **overrides):
    fields = {"id": uuid.uuid4(), "ship": None, "created_at": datetime.now(UTC), "finalized_at": None, "legs": legs}
    fields.update(overrides)
    return TradeRun(**fields)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, get_value=None, execute_value=None):
        self._get_value = get_value
        self._execute_value = execute_value
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, entity_id):
        return self._get_value

    async def execute(self, stmt):
        return _FakeResult(self._execute_value)

    async def commit(self):
        self.committed = True


async def test_advance_leg_sets_the_next_field(monkeypatch):
    # started_at is already set on any real leg by the time it's queryable (stamped by
    # create_run_from_route/advance_leg's own finalized_at branch, never a manual step) —
    # reflected here so the fixture matches reality, not just the pure sequence logic.
    leg = _make_leg(LegType.ACQUISITION, started_at=datetime.now(UTC))
    session = _FakeSession(get_value=leg)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    result = await ledger_service.advance_leg(leg.id)

    assert result.reached_at is not None
    assert session.committed


async def test_advance_leg_finalizing_starts_the_sibling_leg(monkeypatch):
    # The behavior this whole change is about: finalizing one leg is what starts the
    # other, automatically — no separate "depart" step for the sale leg either.
    now = datetime.now(UTC)
    run_id = uuid.uuid4()
    acquisition = _make_leg(
        LegType.ACQUISITION, started_at=now, reached_at=now,
        transaction_completed_at=now, transferred_at=now,
    )
    acquisition.run_id = run_id
    sale = _make_leg(LegType.SALE)
    sale.run_id = run_id
    session = _FakeSession(get_value=acquisition, execute_value=sale)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    await ledger_service.advance_leg(acquisition.id)

    assert sale.started_at is not None


async def test_advance_leg_finalizing_does_not_clobber_an_already_started_sibling(monkeypatch):
    now = datetime.now(UTC)
    run_id = uuid.uuid4()
    acquisition = _make_leg(
        LegType.ACQUISITION, started_at=now, reached_at=now,
        transaction_completed_at=now, transferred_at=now,
    )
    acquisition.run_id = run_id
    original_start = datetime.now(UTC)
    sale = _make_leg(LegType.SALE, started_at=original_start)
    sale.run_id = run_id
    session = _FakeSession(get_value=acquisition, execute_value=sale)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    await ledger_service.advance_leg(acquisition.id)

    assert sale.started_at == original_start


async def test_advance_leg_raises_when_already_finalized(monkeypatch):
    leg = _make_leg(
        LegType.ACQUISITION, started_at=datetime.now(UTC), reached_at=datetime.now(UTC),
        transaction_completed_at=datetime.now(UTC), transferred_at=datetime.now(UTC), finalized_at=datetime.now(UTC),
    )
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="already finalized"):
        await ledger_service.advance_leg(leg.id)


async def test_advance_leg_raises_when_leg_not_found(monkeypatch):
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: _FakeSession(get_value=None))

    with pytest.raises(ValueError, match="No trade leg"):
        await ledger_service.advance_leg(uuid.uuid4())


async def test_advance_leg_raises_when_next_step_needs_transaction_data(monkeypatch):
    # Guards the store boundary, not just the UI — a future AI/voice tool that reuses
    # advance_leg out of habit shouldn't be able to stamp a transaction timestamp with
    # zero purchase/sale data attached.
    leg = _make_leg(LegType.ACQUISITION, started_at=datetime.now(UTC), reached_at=datetime.now(UTC))
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="record_purchase/record_sale"):
        await ledger_service.advance_leg(leg.id)


async def test_record_purchase_writes_fields_and_stamps_transaction_only(monkeypatch):
    leg = _make_leg(LegType.ACQUISITION)
    session = _FakeSession(get_value=leg)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    result = await ledger_service.record_purchase(leg.id, 40, 12, CargoTransferType.AUTOLOAD, 5000)

    assert result.quantity_scu == 40
    assert result.price_per_unit == 12
    assert result.cargo_transfer_type == CargoTransferType.AUTOLOAD
    assert result.cargo_transfer_fee == 5000
    assert result.transaction_completed_at is not None
    assert result.transferred_at is None  # Confirm Loaded is still its own step on the buy side
    assert session.committed


async def test_record_purchase_raises_on_sale_leg(monkeypatch):
    leg = _make_leg(LegType.SALE)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="not an acquisition leg"):
        await ledger_service.record_purchase(leg.id, 1, 1, CargoTransferType.MANUAL, 0)


async def test_record_purchase_raises_if_already_recorded(monkeypatch):
    leg = _make_leg(LegType.ACQUISITION, transaction_completed_at=datetime.now(UTC))
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="already recorded"):
        await ledger_service.record_purchase(leg.id, 1, 1, CargoTransferType.MANUAL, 0)


async def test_record_sale_writes_fields_and_stamps_both_timestamps(monkeypatch):
    # transferred_at wasn't set going in (no prior Confirm Unloaded step happened) —
    # record_sale falls back to stamping it here too, same as the always-together
    # autoload case.
    leg = _make_leg(LegType.SALE)
    session = _FakeSession(get_value=leg)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    result = await ledger_service.record_sale(leg.id, 30, 20, CargoTransferType.MANUAL, 0)

    assert result.quantity_scu == 30
    assert result.price_per_unit == 20
    assert result.transaction_completed_at is not None
    assert result.transferred_at is not None
    assert session.committed


async def test_record_sale_preserves_existing_transferred_at(monkeypatch):
    # Manual leg that already went through Confirm Unloaded (advance_leg) — record_sale
    # must not clobber that real timestamp with a new one.
    unloaded_at = datetime.now(UTC)
    leg = _make_leg(LegType.SALE, CargoTransferType.MANUAL, transferred_at=unloaded_at)
    session = _FakeSession(get_value=leg)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    result = await ledger_service.record_sale(leg.id, 30, 20, CargoTransferType.MANUAL, 0)

    assert result.transaction_completed_at is not None
    assert result.transferred_at == unloaded_at


async def test_record_sale_raises_on_acquisition_leg(monkeypatch):
    leg = _make_leg(LegType.ACQUISITION)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="not a sale leg"):
        await ledger_service.record_sale(leg.id, 1, 1, CargoTransferType.MANUAL, 0)


async def test_record_sale_raises_if_already_recorded(monkeypatch):
    leg = _make_leg(LegType.SALE, transaction_completed_at=datetime.now(UTC))
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="already recorded"):
        await ledger_service.record_sale(leg.id, 1, 1, CargoTransferType.MANUAL, 0)


async def test_finalize_run_raises_when_a_leg_is_unfinished(monkeypatch):
    finished_leg = _make_leg(
        LegType.ACQUISITION, started_at=datetime.now(UTC), reached_at=datetime.now(UTC),
        transaction_completed_at=datetime.now(UTC), transferred_at=datetime.now(UTC), finalized_at=datetime.now(UTC),
    )
    unfinished_leg = _make_leg(LegType.SALE)
    run = _make_run([finished_leg, unfinished_leg])
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: _FakeSession(execute_value=run))

    with pytest.raises(ValueError, match="unfinished legs"):
        await ledger_service.finalize_run(run.id)


async def test_finalize_run_succeeds_when_every_leg_is_done(monkeypatch):
    def _finished_leg(leg_type, cargo_transfer_type=CargoTransferType.MANUAL):
        now = datetime.now(UTC)
        return _make_leg(
            leg_type, cargo_transfer_type, started_at=now, reached_at=now,
            transaction_completed_at=now, transferred_at=now, finalized_at=now,
        )

    run = _make_run([_finished_leg(LegType.ACQUISITION), _finished_leg(LegType.SALE)])
    session = _FakeSession(execute_value=run)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    result = await ledger_service.finalize_run(run.id)

    assert result.finalized_at is not None
    assert session.committed


async def test_delete_run_deletes_when_found(monkeypatch):
    run = _make_run([])

    class _DeleteSession(_FakeSession):
        def __init__(self):
            super().__init__(get_value=run)
            self.deleted = None

        async def delete(self, obj):
            self.deleted = obj

    session = _DeleteSession()
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    await ledger_service.delete_run(run.id)

    assert session.deleted is run
    assert session.committed


async def test_delete_run_is_a_noop_when_not_found(monkeypatch):
    session = _FakeSession(get_value=None)
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    await ledger_service.delete_run(uuid.uuid4())  # must not raise

    assert session.committed is False


# catch_up_before_transaction moved to ledger_client.py (client-side orchestration over
# advance_leg) — see tests/test_ledger_client.py.


async def test_get_in_progress_runs_filters_by_the_given_user(monkeypatch):
    pilot_id = uuid.uuid4()

    class _CapturingSession:
        def __init__(self):
            self.executed_stmt = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            self.executed_stmt = stmt

            class _EmptyResult:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _EmptyResult()

    session = _CapturingSession()
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    await ledger_service.get_in_progress_runs(pilot_id)

    where_sql = str(session.executed_stmt.whereclause.compile(compile_kwargs={"literal_binds": True}))
    # SQLAlchemy's generic UUID literal renderer drops the hyphens.
    assert f"trade_run.user_id = '{pilot_id.hex}'" in where_sql


async def test_get_finalized_runs_filters_by_the_given_user(monkeypatch):
    pilot_id = uuid.uuid4()

    class _CapturingSession:
        def __init__(self):
            self.executed_stmt = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            self.executed_stmt = stmt

            class _EmptyResult:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _EmptyResult()

    session = _CapturingSession()
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    await ledger_service.get_finalized_runs(pilot_id)

    where_sql = str(session.executed_stmt.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert f"trade_run.user_id = '{pilot_id.hex}'" in where_sql


def _route(is_auto_load_origin=0, is_auto_load_destination=0, **overrides):
    from tools.uexcorp.trade_data import UEXTradeRoute

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
        container_sizes_origin=[1, 2, 4], container_sizes_destination=[1, 2, 4],
    )
    fields.update(overrides)
    return UEXTradeRoute(**fields)


class _AddSession:
    """Just enough of an AsyncSession for create_run_from_route: capture the run passed
    to add(), then hand that same object back on the post-commit re-fetch — the real
    re-fetch exists to eager-load `legs`, but the fake's run already has them attached
    before add() ever runs."""

    def __init__(self):
        self.added = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added = obj

    async def commit(self):
        pass

    async def execute(self, stmt):
        class _Result:
            def __init__(self, value):
                self._value = value

            def scalar_one(self):
                return self._value

        return _Result(self.added)


async def test_create_run_from_route_stamps_user_id_on_the_run_and_both_legs(monkeypatch):
    pilot_id = uuid.uuid4()
    session = _AddSession()
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)

    run = await ledger_service.create_run_from_route(_route(), 40, "Railen", pilot_id)

    assert run.user_id == pilot_id
    assert all(leg.user_id == pilot_id for leg in run.legs)


async def test_create_run_from_route_maps_auto_load_flags_to_cargo_transfer_type(monkeypatch):
    # Regression coverage for the real bug find_best_route had (docs/todo.md Phase 1):
    # is_auto_load_origin/destination weren't being patched onto returned routes, so
    # every acquisition leg came out MANUAL even at an autoload terminal.
    session = _AddSession()
    monkeypatch.setattr(ledger_service, "SessionLocal", lambda: session)
    route = _route(is_auto_load_origin=1, is_auto_load_destination=0)

    run = await ledger_service.create_run_from_route(route, 40, "Railen", uuid.uuid4())

    acquisition = next(leg for leg in run.legs if leg.leg_type.value == "acquisition")
    sale = next(leg for leg in run.legs if leg.leg_type.value == "sale")
    assert acquisition.cargo_transfer_type == CargoTransferType.AUTOLOAD
    assert sale.cargo_transfer_type == CargoTransferType.MANUAL
    assert acquisition.quantity_scu == 40
    assert sale.quantity_scu == 40
    assert run.ship == "Railen"
