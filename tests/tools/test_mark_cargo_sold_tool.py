"""Covers mark_cargo_sold's entailment backfill: a completed sale is mechanically
impossible without arrival, and for manually-transferred cargo, unloading — so a pilot
reporting the sale directly ("I sold the copper for 4,567") should get those recorded
too, not be refused. Fakes only the true IO boundary — ledger_client.advance_leg/
record_sale, the HTTP calls to the server — so ledger_client.catch_up_before_
transaction's real orchestration logic (looping advance_leg) is what's actually under
test here, not a re-implementation of it.

The autoload/manual distinction matters for the message: autoload never has an
independent unloading step (see _SALE_AUTOLOAD_SEQUENCE), so saying "recorded unloading"
for one would be wrong, not just verbose.
"""
import uuid
from datetime import UTC, datetime

import ledger_client
from db.models import CargoTransferType, LegType
from db.trade_run_store import next_unset_field
from ledger_schemas import TradeLegOut
from tools.trade_run import resolver
from tools.trade_run.mark_cargo_sold_tool import MarkCargoSoldTool


def _leg(cargo_transfer_type=CargoTransferType.MANUAL, **overrides):
    fields = dict(
        id=uuid.uuid4(), run_id=uuid.uuid4(), leg_type=LegType.SALE, terminal_id=2,
        terminal_name="Admin - Rod's Fuel 'N Supplies", commodity_name="Copper", quantity_scu=640,
        price_per_unit=4700, cargo_transfer_type=cargo_transfer_type, cargo_transfer_fee=0,
        created_at=datetime.now(UTC), started_at=datetime.now(UTC),
        reached_at=None, transaction_completed_at=None, transferred_at=None, finalized_at=None,
    )
    fields.update(overrides)
    return TradeLegOut.model_validate(fields)


async def _returning(value):
    return value


def _wire(monkeypatch, leg):
    """leg is shared by reference across fakes, so a mutation from one call (e.g. the
    catch-up advance) is visible to the next (the sale itself) — matches how the real
    server's single row persists between calls."""
    monkeypatch.setattr(resolver, "resolve_leg", lambda **kwargs: _returning(leg))

    async def _fake_advance_leg(leg_id):
        setattr(leg, next_unset_field(leg), datetime.now(UTC))
        return leg

    async def _fake_record_sale(leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        leg.quantity_scu = quantity_scu
        leg.price_per_unit = price_per_unit
        leg.cargo_transfer_type = cargo_transfer_type
        leg.cargo_transfer_fee = cargo_transfer_fee
        leg.transaction_completed_at = datetime.now(UTC)
        return leg

    monkeypatch.setattr(ledger_client, "advance_leg", _fake_advance_leg)
    monkeypatch.setattr(ledger_client, "record_sale", _fake_record_sale)


async def _sell(price=4567):
    tool = MarkCargoSoldTool()
    return await tool._arun(
        commodity=None, terminal=None, quantity_scu=None, price_per_unit=price,
        cargo_transfer_type=None, cargo_transfer_fee=None,
    )


async def test_arrival_and_unload_already_confirmed_records_sale_without_catch_up(monkeypatch):
    leg = _leg(CargoTransferType.MANUAL, reached_at=datetime.now(UTC), transferred_at=datetime.now(UTC))
    _wire(monkeypatch, leg)

    message = await _sell()

    assert leg.transaction_completed_at is not None
    assert message == "Sale recorded for Copper at Admin - Rod's Fuel 'N Supplies."


async def test_fresh_manual_leg_backfills_arrival_and_unloading(monkeypatch):
    leg = _leg(CargoTransferType.MANUAL)  # nothing confirmed yet
    _wire(monkeypatch, leg)

    message = await _sell(price=4567)

    assert leg.reached_at is not None
    assert leg.transferred_at is not None
    assert leg.price_per_unit == 4567
    assert message == "Recorded arrival, unloading, and the sale for Copper at Admin - Rod's Fuel 'N Supplies."


async def test_already_arrived_manual_leg_backfills_only_unloading(monkeypatch):
    leg = _leg(CargoTransferType.MANUAL, reached_at=datetime.now(UTC))
    _wire(monkeypatch, leg)

    message = await _sell()

    assert leg.transferred_at is not None
    assert message == "Recorded unloading and the sale for Copper at Admin - Rod's Fuel 'N Supplies."


async def test_fresh_autoload_leg_backfills_only_arrival_never_mentions_unloading(monkeypatch):
    leg = _leg(CargoTransferType.AUTOLOAD)  # nothing confirmed yet
    _wire(monkeypatch, leg)

    message = await _sell()

    assert leg.reached_at is not None
    assert message == "Recorded arrival and the sale for Copper at Admin - Rod's Fuel 'N Supplies."
    assert "unload" not in message.lower()
