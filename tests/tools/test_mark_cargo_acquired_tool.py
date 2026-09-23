"""Covers mark_cargo_acquired's entailment backfill: a completed purchase is
mechanically impossible without arrival, so a pilot reporting the purchase directly
("I bought the copper") should get arrival recorded too, not be refused and told to
report it separately. Fakes only the true IO boundary — ledger_client.advance_leg/
record_purchase, the HTTP calls to the server — so ledger_client.catch_up_before_
transaction's real orchestration logic (looping advance_leg) is what's actually under
test here, not a re-implementation of it.
"""
import uuid
from datetime import UTC, datetime

import ledger_client
from db.models import CargoTransferType, LegType
from db.trade_run_store import next_unset_field
from ledger_schemas import TradeLegOut
from tools.trade_run import resolver
from tools.trade_run.mark_cargo_acquired_tool import MarkCargoAcquiredTool


def _leg(**overrides):
    fields = dict(
        id=uuid.uuid4(), run_id=uuid.uuid4(), leg_type=LegType.ACQUISITION, terminal_id=1,
        terminal_name="Admin - Seraphim", commodity_name="Copper", quantity_scu=640, price_per_unit=3198,
        cargo_transfer_type=CargoTransferType.MANUAL, cargo_transfer_fee=0,
        created_at=datetime.now(UTC), started_at=datetime.now(UTC),
        reached_at=None, transaction_completed_at=None, transferred_at=None, finalized_at=None,
    )
    fields.update(overrides)
    return TradeLegOut.model_validate(fields)


def _wire(monkeypatch, leg):
    """leg is a plain object shared by reference across fakes, so a mutation from one
    call (e.g. the catch-up advance) is visible to the next (the purchase itself) —
    matches how the real server's single row persists between calls."""
    monkeypatch.setattr(resolver, "resolve_leg", lambda **kwargs: _returning(leg))

    async def _fake_advance_leg(leg_id):
        setattr(leg, next_unset_field(leg), datetime.now(UTC))
        return leg

    async def _fake_record_purchase(leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        leg.quantity_scu = quantity_scu
        leg.price_per_unit = price_per_unit
        leg.cargo_transfer_type = cargo_transfer_type
        leg.cargo_transfer_fee = cargo_transfer_fee
        leg.transaction_completed_at = datetime.now(UTC)
        return leg

    monkeypatch.setattr(ledger_client, "advance_leg", _fake_advance_leg)
    monkeypatch.setattr(ledger_client, "record_purchase", _fake_record_purchase)


async def _returning(value):
    return value


async def test_arrival_already_confirmed_records_purchase_without_catch_up(monkeypatch):
    leg = _leg(reached_at=datetime.now(UTC))
    _wire(monkeypatch, leg)

    tool = MarkCargoAcquiredTool()
    message = await tool._arun(
        commodity=None, terminal=None, quantity_scu=None, price_per_unit=None,
        cargo_transfer_type=None, cargo_transfer_fee=None,
    )

    assert leg.transaction_completed_at is not None
    assert "Advanced leg" in message
    assert "Recorded arrival" not in message


async def test_reporting_the_purchase_directly_backfills_arrival(monkeypatch):
    leg = _leg()  # fresh — nothing confirmed yet
    _wire(monkeypatch, leg)

    tool = MarkCargoAcquiredTool()
    message = await tool._arun(
        commodity=None, terminal=None, quantity_scu=None, price_per_unit=3200,
        cargo_transfer_type=None, cargo_transfer_fee=None,
    )

    assert leg.reached_at is not None
    assert leg.transaction_completed_at is not None
    assert leg.price_per_unit == 3200
    assert "Recorded arrival and the purchase" in message
