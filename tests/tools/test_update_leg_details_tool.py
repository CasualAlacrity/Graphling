"""update_leg_details saves stated buy/sell values without completing the transaction --
the pilot reads the autoload fee off the kiosk a click before buying, and that must not
record the purchase (2026-10-17 bug: the fee call went through mark_cargo_acquired)."""
import uuid
from datetime import UTC, datetime

import ledger_client
from db.models import CargoTransferType, LegType
from ledger_schemas import TradeLegOut
from tools.trade_run import resolver
from tools.trade_run.update_leg_details_tool import UpdateLegDetailsTool


def _leg(**overrides):
    fields = dict(
        id=uuid.uuid4(), run_id=uuid.uuid4(), leg_type=LegType.ACQUISITION, terminal_id=1,
        terminal_name="Admin - HUR-L3", commodity_name="Nitrogen", quantity_scu=640, price_per_unit=100,
        cargo_transfer_type=CargoTransferType.MANUAL, cargo_transfer_fee=0,
        created_at=datetime.now(UTC), started_at=datetime.now(UTC), reached_at=datetime.now(UTC),
        transaction_completed_at=None, transferred_at=None, finalized_at=None,
    )
    fields.update(overrides)
    return TradeLegOut.model_validate(fields)


async def _returning(value):
    return value


def _wire(monkeypatch, leg):
    monkeypatch.setattr(resolver, "resolve_leg", lambda **kwargs: _returning(leg))

    async def fake_update(leg_id, quantity_scu=None, price_per_unit=None,
                          cargo_transfer_fee=None, cargo_transfer_type=None):
        if cargo_transfer_fee is not None:
            leg.cargo_transfer_fee = cargo_transfer_fee
        if cargo_transfer_type is not None:
            leg.cargo_transfer_type = cargo_transfer_type
        return leg

    monkeypatch.setattr(ledger_client, "update_transaction", fake_update)


async def test_fee_is_saved_and_the_purchase_is_left_unrecorded(monkeypatch):
    leg = _leg()
    _wire(monkeypatch, leg)

    message = await UpdateLegDetailsTool()._arun(cargo_transfer_fee=19350)

    assert leg.cargo_transfer_fee == 19350
    assert leg.transaction_completed_at is None
    assert "19,350" in message
    assert "NOT been recorded" in message


async def test_correcting_a_recorded_purchase_does_not_ask_whether_they_bought(monkeypatch):
    leg = _leg(transaction_completed_at=datetime.now(UTC))
    _wire(monkeypatch, leg)

    message = await UpdateLegDetailsTool()._arun(cargo_transfer_fee=19350)

    assert "NOT been recorded" not in message


async def test_nothing_stated_changes_nothing(monkeypatch):
    leg = _leg()
    _wire(monkeypatch, leg)

    message = await UpdateLegDetailsTool()._arun()

    assert "Nothing to update" in message
