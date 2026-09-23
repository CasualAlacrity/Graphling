"""Covers the milestone-sequencing logic (the exact bug class Arkanis itself had to fix
for autoload) and the ungated money aggregates — the pure, DB-free half of what used to
be this whole module. The DB-touching functions (advance_leg, record_purchase/sale,
finalize_run, create_run_from_route, get_in_progress_runs/get_finalized_runs) moved to
server/ledger_service.py once a real server, not this desktop client, became the only
thing that talks to Postgres — see tests/server/test_ledger_service.py."""
import uuid
from datetime import UTC, datetime

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
    leg = _make_leg(LegType.SALE, CargoTransferType.AUTOLOAD, reached_at=datetime.now(UTC))
    assert trade_run_store.next_unset_field(leg) == "transaction_completed_at"

    now = datetime.now(UTC)
    leg.transaction_completed_at = now
    leg.transferred_at = now
    assert trade_run_store.next_unset_field(leg) == "finalized_at"


def test_sale_manual_next_unset_field_reaches_transferred_before_transaction():
    # The new behavior this whole change is about: manual unload is a real, independent,
    # timestamped step that happens before the sale, not bundled with it.
    leg = _make_leg(LegType.SALE, CargoTransferType.MANUAL, reached_at=datetime.now(UTC))
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
