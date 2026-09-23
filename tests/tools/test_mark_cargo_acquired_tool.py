"""Covers mark_cargo_acquired's entailment backfill: a completed purchase is
mechanically impossible without arrival, so a pilot reporting the purchase directly
("I bought the copper") should get arrival recorded too, not be refused and told to
report it separately. Exercises the real trade_run_store functions (via a fake session),
not mocked-out internals — a passing test here means the tool and the store actually
agree, not just that the tool calls something.
"""
import uuid
from datetime import UTC, datetime

from db.models import CargoTransferType, LegType, TradeLeg
from tools.trade_run import resolver
from tools.trade_run.mark_cargo_acquired_tool import MarkCargoAcquiredTool


def _leg(**overrides):
    fields = dict(
        id=uuid.uuid4(), leg_type=LegType.ACQUISITION, terminal_id=1, terminal_name="Admin - Seraphim",
        commodity_name="Copper", quantity_scu=640, price_per_unit=3198,
        cargo_transfer_type=CargoTransferType.MANUAL, cargo_transfer_fee=0,
        created_at=datetime.now(UTC), started_at=datetime.now(UTC),
        reached_at=None, transaction_completed_at=None, transferred_at=None, finalized_at=None,
    )
    fields.update(overrides)
    return TradeLeg(**fields)


class _FakeSession:
    """get() always hands back the same leg object, so a mutation from one call (e.g.
    the catch-up advance) is visible to the next (the purchase itself) — matches how
    trade_run_store's own tests fake a session."""

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


def _wire(monkeypatch, leg):
    monkeypatch.setattr(resolver, "resolve_leg", lambda **kwargs: _returning(leg))
    from db import trade_run_store
    monkeypatch.setattr(trade_run_store, "SessionLocal", lambda: _FakeSession(leg))


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
