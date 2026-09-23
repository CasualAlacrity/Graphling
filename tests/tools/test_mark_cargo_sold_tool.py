"""Covers mark_cargo_sold's entailment backfill: a completed sale is mechanically
impossible without arrival, and for manually-transferred cargo, unloading — so a pilot
reporting the sale directly ("I sold the copper for 4,567") should get those recorded
too, not be refused. Exercises the real trade_run_store functions (via a fake session),
not mocked-out internals.

The autoload/manual distinction matters for the message: autoload never has an
independent unloading step (see _SALE_AUTOLOAD_SEQUENCE), so saying "recorded unloading"
for one would be wrong, not just verbose.
"""
import uuid
from datetime import UTC, datetime

from db.models import CargoTransferType, LegType, TradeLeg
from tools.trade_run import resolver
from tools.trade_run.mark_cargo_sold_tool import MarkCargoSoldTool


def _leg(cargo_transfer_type=CargoTransferType.MANUAL, **overrides):
    fields = dict(
        id=uuid.uuid4(), leg_type=LegType.SALE, terminal_id=2, terminal_name="Admin - Rod's Fuel 'N Supplies",
        commodity_name="Copper", quantity_scu=640, price_per_unit=4700,
        cargo_transfer_type=cargo_transfer_type, cargo_transfer_fee=0,
        created_at=datetime.now(UTC), started_at=datetime.now(UTC),
        reached_at=None, transaction_completed_at=None, transferred_at=None, finalized_at=None,
    )
    fields.update(overrides)
    return TradeLeg(**fields)


class _FakeSession:
    def __init__(self, leg):
        self._leg = leg
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, entity_id):
        return self._leg

    async def commit(self):
        self.committed = True


async def _returning(value):
    return value


def _wire(monkeypatch, leg):
    monkeypatch.setattr(resolver, "resolve_leg", lambda **kwargs: _returning(leg))
    from db import trade_run_store
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(leg))


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
