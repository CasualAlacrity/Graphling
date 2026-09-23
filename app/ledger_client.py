"""HTTP client for the ledger server (app/server/) — the desktop client's only way to
touch the trade ledger now that a real server mediates every read/write (docs/todo.md
Phase 2). Same public function names db/trade_run_store.py's DB-touching functions used
to have before that split, so every caller only ever needed its import line changed, not
its call sites — see tools/trade_run/*.py and overlay/trade_runs_panel.py.

Returns ledger_schemas' TradeRunOut/TradeLegOut (Pydantic, parsed straight from the
server's JSON) rather than db.models' SQLAlchemy classes. db/trade_run_store.py's pure
helpers (ordered_legs, current_leg, run_profit, ...) only ever do plain attribute access
on these objects, so they work unchanged on either — nothing downstream needed to know
the difference.
"""
from uuid import UUID

from api_client import authenticated_client, raise_for_status
from db.models import CargoTransferType, LegMilestone
from db.trade_run_store import next_unset_field
from ledger_schemas import TradeLegOut, TradeRunOut
from tools.uexcorp.trade_data import UEXTradeRoute


async def create_run_from_route(route: UEXTradeRoute, quantity_scu: int, ship: str | None) -> TradeRunOut:
    async with await authenticated_client() as client:
        response = await client.post(
            "/trade-runs",
            json={"route": route.model_dump(mode="json"), "quantity_scu": quantity_scu, "ship": ship},
        )
        raise_for_status(response)
        return TradeRunOut.model_validate(response.json())


async def get_in_progress_runs() -> list[TradeRunOut]:
    async with await authenticated_client() as client:
        response = await client.get("/trade-runs/in-progress")
        raise_for_status(response)
        return [TradeRunOut.model_validate(run) for run in response.json()]


async def get_finalized_runs(limit: int = 50) -> list[TradeRunOut]:
    async with await authenticated_client() as client:
        response = await client.get("/trade-runs/finalized", params={"limit": limit})
        raise_for_status(response)
        return [TradeRunOut.model_validate(run) for run in response.json()]


async def advance_leg(leg_id: UUID) -> TradeLegOut:
    async with await authenticated_client() as client:
        response = await client.post(f"/trade-runs/legs/{leg_id}/advance")
        raise_for_status(response)
        return TradeLegOut.model_validate(response.json())


async def catch_up_before_transaction(leg: TradeLegOut) -> TradeLegOut:
    """Client-side orchestration, not a server endpoint — advances a leg through any
    purely-timestamp milestones still pending before a purchase/sale can be recorded,
    the same entailment logic db/trade_run_store.catch_up_before_transaction used to
    apply locally. Now loops HTTP calls to advance_leg instead of DB calls; the logic
    itself (see the original docstring, preserved in git history) hasn't changed."""
    while next_unset_field(leg) not in (LegMilestone.TRANSACTION_COMPLETED_AT, None):
        leg = await advance_leg(leg.id)
    return leg


async def record_purchase(
        leg_id: UUID, quantity_scu: int, price_per_unit: int,
        cargo_transfer_type: CargoTransferType, cargo_transfer_fee: int,
) -> TradeLegOut:
    async with await authenticated_client() as client:
        response = await client.post(
            f"/trade-runs/legs/{leg_id}/purchase",
            json={
                "quantity_scu": quantity_scu, "price_per_unit": price_per_unit,
                "cargo_transfer_type": cargo_transfer_type.value, "cargo_transfer_fee": cargo_transfer_fee,
            },
        )
        raise_for_status(response)
        return TradeLegOut.model_validate(response.json())


async def record_sale(
        leg_id: UUID, quantity_scu: int, price_per_unit: int,
        cargo_transfer_type: CargoTransferType, cargo_transfer_fee: int,
) -> TradeLegOut:
    async with await authenticated_client() as client:
        response = await client.post(
            f"/trade-runs/legs/{leg_id}/sale",
            json={
                "quantity_scu": quantity_scu, "price_per_unit": price_per_unit,
                "cargo_transfer_type": cargo_transfer_type.value, "cargo_transfer_fee": cargo_transfer_fee,
            },
        )
        raise_for_status(response)
        return TradeLegOut.model_validate(response.json())


async def finalize_run(run_id: UUID) -> TradeRunOut:
    async with await authenticated_client() as client:
        response = await client.post(f"/trade-runs/{run_id}/finalize")
        raise_for_status(response)
        return TradeRunOut.model_validate(response.json())


async def delete_run(run_id: UUID) -> None:
    async with await authenticated_client() as client:
        response = await client.delete(f"/trade-runs/{run_id}")
        raise_for_status(response)
