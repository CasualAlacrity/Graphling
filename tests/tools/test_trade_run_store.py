"""Covers the milestone-sequencing logic (the exact bug class Arkanis itself had to fix
for autoload), the ungated money aggregates, record_purchase/record_sale, and the
advance_leg/finalize_run guardrails that protect callers (including future AI tools)
from silently corrupting an already-finished leg or run."""
import uuid
from datetime import UTC, datetime

import pytest

from db import trade_run_store
from db.models import CargoTransferType, LegType, TradeLeg, TradeRun


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


ACQUISITION_SEQUENCE = [
    ("started_at", "Depart for pickup"),
    ("reached_at", "Mark arrived"),
    ("transaction_completed_at", "Buy cargo"),
    ("transferred_at", "Confirm loaded"),
    ("finalized_at", "Mark leg finalized"),
]


def test_current_step_title_walks_acquisition_sequence():
    leg = _make_leg(LegType.ACQUISITION)

    for field, expected_label in ACQUISITION_SEQUENCE:
        assert trade_run_store.current_step_title(leg) == expected_label
        setattr(leg, field, datetime.now(UTC))

    assert trade_run_store.current_step_title(leg) == "Leg finalized"


def test_current_step_title_walks_sale_manual_sequence():
    # Manual unload genuinely takes time — real, physical, before the sale can be
    # recorded at the kiosk — so it gets its own step between arrival and the sale,
    # unlike autoload below.
    leg = _make_leg(LegType.SALE, CargoTransferType.MANUAL)

    assert trade_run_store.current_step_title(leg) == "Depart for sale"
    leg.started_at = datetime.now(UTC)
    assert trade_run_store.current_step_title(leg) == "Mark arrived"
    leg.reached_at = datetime.now(UTC)
    assert trade_run_store.current_step_title(leg) == "Confirm unloaded"
    leg.transferred_at = datetime.now(UTC)
    assert trade_run_store.current_step_title(leg) == "Sell cargo"
    leg.transaction_completed_at = datetime.now(UTC)
    assert trade_run_store.current_step_title(leg) == "Mark leg finalized"
    leg.finalized_at = datetime.now(UTC)
    assert trade_run_store.current_step_title(leg) == "Leg finalized"


def test_current_step_title_walks_sale_autoload_sequence():
    # Autoload's unload is instant (just pay the fee) — record_sale stamps
    # transaction_completed_at and transferred_at together, so there's no independent
    # confirm-unloaded step to walk through here.
    leg = _make_leg(LegType.SALE, CargoTransferType.AUTOLOAD)

    assert trade_run_store.current_step_title(leg) == "Depart for sale"
    leg.started_at = datetime.now(UTC)
    assert trade_run_store.current_step_title(leg) == "Mark arrived"
    leg.reached_at = datetime.now(UTC)
    assert trade_run_store.current_step_title(leg) == "Sell cargo"
    now = datetime.now(UTC)
    leg.transaction_completed_at = now
    leg.transferred_at = now
    assert trade_run_store.current_step_title(leg) == "Mark leg finalized"
    leg.finalized_at = datetime.now(UTC)
    assert trade_run_store.current_step_title(leg) == "Leg finalized"


def test_sale_autoload_next_unset_field_skips_transferred_at_once_transacted():
    # This is where the Arkanis-style ordering bug used to live (manual-vs-autoload
    # swapping which of transaction_completed_at/transferred_at came first). Autoload's
    # sequence has no transferred_at position at all — record_sale sets it together with
    # transaction_completed_at, so the position in between is never independently
    # reachable — this asserts that skip actually happens.
    leg = _make_leg(
        LegType.SALE, CargoTransferType.AUTOLOAD, started_at=datetime.now(UTC), reached_at=datetime.now(UTC)
    )
    assert trade_run_store.next_unset_field(leg) == "transaction_completed_at"

    now = datetime.now(UTC)
    leg.transaction_completed_at = now
    leg.transferred_at = now
    assert trade_run_store.next_unset_field(leg) == "finalized_at"


def test_sale_manual_next_unset_field_reaches_transferred_before_transaction():
    # The new behavior this whole change is about: manual unload is a real, independent,
    # timestamped step that happens before the sale, not bundled with it.
    leg = _make_leg(
        LegType.SALE, CargoTransferType.MANUAL, started_at=datetime.now(UTC), reached_at=datetime.now(UTC)
    )
    assert trade_run_store.next_unset_field(leg) == "transferred_at"

    leg.transferred_at = datetime.now(UTC)
    assert trade_run_store.next_unset_field(leg) == "transaction_completed_at"


def test_breadcrumb_steps_acquisition_skips_travel_fields():
    leg = _make_leg(LegType.ACQUISITION)
    assert trade_run_store.breadcrumb_steps(leg) == [
        ("transaction_completed_at", "Buy cargo"),
        ("transferred_at", "Confirm loaded"),
        ("finalized_at", "Finalize"),
    ]


def test_breadcrumb_steps_sale_manual_includes_unload():
    leg = _make_leg(LegType.SALE, CargoTransferType.MANUAL)
    assert trade_run_store.breadcrumb_steps(leg) == [
        ("transferred_at", "Confirm unloaded"),
        ("transaction_completed_at", "Sell cargo"),
        ("finalized_at", "Finalize"),
    ]


def test_breadcrumb_steps_sale_autoload_skips_unload():
    leg = _make_leg(LegType.SALE, CargoTransferType.AUTOLOAD)
    assert trade_run_store.breadcrumb_steps(leg) == [
        ("transaction_completed_at", "Sell cargo"),
        ("finalized_at", "Finalize"),
    ]


def test_run_investment_sums_acquisition_legs_regardless_of_transaction_state():
    # quantity_scu/price_per_unit already mean "actual once transacted, planned estimate
    # until then" — no gate needed, this is what makes the number a genuine projection.
    transacted = _make_leg(
        LegType.ACQUISITION, quantity_scu=10, price_per_unit=5, transaction_completed_at=datetime.now(UTC)
    )
    still_planned = _make_leg(LegType.ACQUISITION, quantity_scu=20, price_per_unit=3)
    run = _make_run([transacted, still_planned])

    assert trade_run_store.run_investment(run) == 50 + 60


def test_run_revenue_sums_sale_legs_regardless_of_transaction_state():
    transacted = _make_leg(LegType.SALE, quantity_scu=10, price_per_unit=8, transaction_completed_at=datetime.now(UTC))
    still_planned = _make_leg(LegType.SALE, quantity_scu=5, price_per_unit=4)
    run = _make_run([transacted, still_planned])

    assert trade_run_store.run_revenue(run) == 80 + 20


def test_run_fees_sums_all_legs_regardless_of_transfer_state():
    leg_a = _make_leg(LegType.ACQUISITION, cargo_transfer_fee=15)
    leg_b = _make_leg(LegType.SALE, cargo_transfer_fee=25)
    run = _make_run([leg_a, leg_b])

    assert trade_run_store.run_fees(run) == 40


def test_run_profit_is_revenue_minus_investment_minus_fees():
    acquisition = _make_leg(
        LegType.ACQUISITION, quantity_scu=10, price_per_unit=5, cargo_transfer_fee=3,
        transaction_completed_at=datetime.now(UTC), transferred_at=datetime.now(UTC),
    )
    sale = _make_leg(
        LegType.SALE, quantity_scu=10, price_per_unit=8, cargo_transfer_fee=2,
        transaction_completed_at=datetime.now(UTC), transferred_at=datetime.now(UTC),
    )
    run = _make_run([acquisition, sale])

    assert trade_run_store.run_profit(run) == (80 - 50 - 5)


def test_run_acquired_scu_sums_acquisition_legs_only():
    acquisition = _make_leg(LegType.ACQUISITION, quantity_scu=40)
    sale = _make_leg(LegType.SALE, quantity_scu=25)
    run = _make_run([acquisition, sale])

    assert trade_run_store.run_acquired_scu(run) == 40


def test_run_sold_scu_sums_sale_legs_only():
    acquisition = _make_leg(LegType.ACQUISITION, quantity_scu=40)
    sale = _make_leg(LegType.SALE, quantity_scu=25)
    run = _make_run([acquisition, sale])

    assert trade_run_store.run_sold_scu(run) == 25


def test_run_duration_is_finalized_minus_created():
    from datetime import timedelta

    created = datetime.now(UTC)
    finalized = created + timedelta(minutes=12)
    run = _make_run([_make_leg(LegType.ACQUISITION)], created_at=created, finalized_at=finalized)

    assert trade_run_store.run_duration(run) == timedelta(minutes=12)


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
    leg = _make_leg(LegType.ACQUISITION)
    session = _FakeSession(get_value=leg)
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: session)

    result = await trade_run_store.advance_leg(leg.id)

    assert result.started_at is not None
    assert session.committed


async def test_advance_leg_raises_when_already_finalized(monkeypatch):
    leg = _make_leg(
        LegType.ACQUISITION, started_at=datetime.now(UTC), reached_at=datetime.now(UTC),
        transaction_completed_at=datetime.now(UTC), transferred_at=datetime.now(UTC), finalized_at=datetime.now(UTC),
    )
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="already finalized"):
        await trade_run_store.advance_leg(leg.id)


async def test_advance_leg_raises_when_leg_not_found(monkeypatch):
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(get_value=None))

    with pytest.raises(ValueError, match="No trade leg"):
        await trade_run_store.advance_leg(uuid.uuid4())


async def test_advance_leg_raises_when_next_step_needs_transaction_data(monkeypatch):
    # Guards the store boundary, not just the UI — a future AI/voice tool that reuses
    # advance_leg out of habit shouldn't be able to stamp a transaction timestamp with
    # zero purchase/sale data attached.
    leg = _make_leg(LegType.ACQUISITION, started_at=datetime.now(UTC), reached_at=datetime.now(UTC))
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="record_purchase/record_sale"):
        await trade_run_store.advance_leg(leg.id)


async def test_record_purchase_writes_fields_and_stamps_transaction_only(monkeypatch):
    leg = _make_leg(LegType.ACQUISITION)
    session = _FakeSession(get_value=leg)
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: session)

    result = await trade_run_store.record_purchase(leg.id, 40, 12, CargoTransferType.AUTOLOAD, 5000)

    assert result.quantity_scu == 40
    assert result.price_per_unit == 12
    assert result.cargo_transfer_type == CargoTransferType.AUTOLOAD
    assert result.cargo_transfer_fee == 5000
    assert result.transaction_completed_at is not None
    assert result.transferred_at is None  # Confirm Loaded is still its own step on the buy side
    assert session.committed


async def test_record_purchase_raises_on_sale_leg(monkeypatch):
    leg = _make_leg(LegType.SALE)
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="not an acquisition leg"):
        await trade_run_store.record_purchase(leg.id, 1, 1, CargoTransferType.MANUAL, 0)


async def test_record_purchase_raises_if_already_recorded(monkeypatch):
    leg = _make_leg(LegType.ACQUISITION, transaction_completed_at=datetime.now(UTC))
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="already recorded"):
        await trade_run_store.record_purchase(leg.id, 1, 1, CargoTransferType.MANUAL, 0)


async def test_record_sale_writes_fields_and_stamps_both_timestamps(monkeypatch):
    # transferred_at wasn't set going in (no prior Confirm Unloaded step happened) —
    # record_sale falls back to stamping it here too, same as the always-together
    # autoload case.
    leg = _make_leg(LegType.SALE)
    session = _FakeSession(get_value=leg)
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: session)

    result = await trade_run_store.record_sale(leg.id, 30, 20, CargoTransferType.MANUAL, 0)

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
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: session)

    result = await trade_run_store.record_sale(leg.id, 30, 20, CargoTransferType.MANUAL, 0)

    assert result.transaction_completed_at is not None
    assert result.transferred_at == unloaded_at


async def test_record_sale_raises_on_acquisition_leg(monkeypatch):
    leg = _make_leg(LegType.ACQUISITION)
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="not a sale leg"):
        await trade_run_store.record_sale(leg.id, 1, 1, CargoTransferType.MANUAL, 0)


async def test_record_sale_raises_if_already_recorded(monkeypatch):
    leg = _make_leg(LegType.SALE, transaction_completed_at=datetime.now(UTC))
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(get_value=leg))

    with pytest.raises(ValueError, match="already recorded"):
        await trade_run_store.record_sale(leg.id, 1, 1, CargoTransferType.MANUAL, 0)


async def test_finalize_run_raises_when_a_leg_is_unfinished(monkeypatch):
    finished_leg = _make_leg(
        LegType.ACQUISITION, started_at=datetime.now(UTC), reached_at=datetime.now(UTC),
        transaction_completed_at=datetime.now(UTC), transferred_at=datetime.now(UTC), finalized_at=datetime.now(UTC),
    )
    unfinished_leg = _make_leg(LegType.SALE)
    run = _make_run([finished_leg, unfinished_leg])
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(execute_value=run))

    with pytest.raises(ValueError, match="unfinished legs"):
        await trade_run_store.finalize_run(run.id)


async def test_finalize_run_succeeds_when_every_leg_is_done(monkeypatch):
    def _finished_leg(leg_type, cargo_transfer_type=CargoTransferType.MANUAL):
        now = datetime.now(UTC)
        return _make_leg(
            leg_type, cargo_transfer_type, started_at=now, reached_at=now,
            transaction_completed_at=now, transferred_at=now, finalized_at=now,
        )

    run = _make_run([_finished_leg(LegType.ACQUISITION), _finished_leg(LegType.SALE)])
    session = _FakeSession(execute_value=run)
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: session)

    result = await trade_run_store.finalize_run(run.id)

    assert result.finalized_at is not None
    assert session.committed
