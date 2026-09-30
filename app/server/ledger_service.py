"""The DB-touching half of what used to be db/trade_run_store.py, moved here now that a
real server (not a single-pilot-per-process desktop client) is what talks to Postgres.
The pure, DB-free helpers (ordered_legs, current_leg, run_profit, etc.) stayed in
db/trade_run_store.py — both this module and the client's ledger_client.py import them
from there unchanged.

Every function here takes user_id explicitly instead of reading a process-global
(db/current_user.py, now deleted) — a server handles concurrent requests from
potentially different pilots, so scoping has to be per-call, resolved by the caller
(server/routes/ledger.py) from the JWT via server/dependencies.get_current_user.
"""
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.models import CargoTransferType, LegMilestone, LegType, TradeLeg, TradeRun
from db.session import SessionLocal
from db.trade_run_store import next_unset_field
from tools.cargo_packing import format_container_sizes, usable_container_sizes
from tools.uexcorp.trade_data import UEXTradeRoute

# Multi-tenancy scoping boundary (docs/todo.md Phase 2): create_run_from_route stamps
# user_id, and get_in_progress_runs/get_finalized_runs filter by it — those are the only
# two ways a pilot's client ever learns a run/leg id in the first place (resolver.py
# resolves against get_in_progress_runs; the overlay lists via the same two functions).
# advance_leg/record_purchase/record_sale/finalize_run/delete_run take an id directly and
# don't re-check ownership themselves — a UUID from a run/leg you were never handed isn't
# guessable, so this is scoped transitively rather than duplicating the check at every
# id-based call. Revisit if that stops being true (e.g. ids start getting shared/logged
# somewhere cross-tenant).


async def create_run_from_route(
        route: UEXTradeRoute, quantity_scu: int, ship: str | None, user_id: UUID,
) -> TradeRun:
    acquisition = TradeLeg(
        user_id=user_id,
        leg_type=LegType.ACQUISITION,
        terminal_id=route.origin_terminal_id,
        terminal_name=route.origin_terminal_name,
        commodity_name=route.commodity_name,
        quantity_scu=quantity_scu,
        price_per_unit=int(round(route.price_origin)),
        cargo_transfer_type=CargoTransferType.AUTOLOAD if route.is_auto_load_origin else CargoTransferType.MANUAL,
        # A run is only ever created once the pilot has committed to it — travel to
        # pickup starts right then, with nothing left to separately confirm.
        started_at=datetime.now(UTC),
    )
    sale = TradeLeg(
        user_id=user_id,
        leg_type=LegType.SALE,
        terminal_id=route.destination_terminal_id,
        terminal_name=route.destination_terminal_name,
        commodity_name=route.commodity_name,
        quantity_scu=quantity_scu,
        price_per_unit=int(round(route.price_destination)),
        cargo_transfer_type=(
            CargoTransferType.AUTOLOAD if route.is_auto_load_destination else CargoTransferType.MANUAL
        ),
    )
    usable_sizes = usable_container_sizes(route.container_sizes_origin, route.container_sizes_destination)
    run = TradeRun(
        user_id=user_id, ship=ship,
        usable_container_sizes=format_container_sizes(usable_sizes), legs=[acquisition, sale],
    )

    async with SessionLocal() as session:
        session.add(run)
        await session.commit()
        result = await session.execute(
            select(TradeRun).where(TradeRun.id == run.id).options(selectinload(TradeRun.legs))
        )
        return result.scalar_one()


async def get_in_progress_runs(user_id: UUID) -> list[TradeRun]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(TradeRun)
            .where(TradeRun.finalized_at.is_(None), TradeRun.user_id == user_id)
            .options(selectinload(TradeRun.legs))
            .order_by(TradeRun.created_at)
        )
        return list(result.scalars().all())


async def get_finalized_runs(user_id: UUID, limit: int = 50) -> list[TradeRun]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(TradeRun)
            .where(TradeRun.finalized_at.is_not(None), TradeRun.user_id == user_id)
            .options(selectinload(TradeRun.legs))
            .order_by(TradeRun.finalized_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def advance_leg(leg_id: UUID) -> TradeLeg:
    async with SessionLocal() as session:
        leg = await session.get(TradeLeg, leg_id)
        if leg is None:
            raise ValueError(f"No trade leg with id {leg_id}")

        field = next_unset_field(leg)
        if field is None:
            raise ValueError(f"Trade leg {leg_id} is already finalized")
        if field == LegMilestone.TRANSACTION_COMPLETED_AT:
            raise ValueError(
                f"Trade leg {leg_id}'s next step needs purchase/sale data — "
                "use record_purchase/record_sale instead of advance_leg"
            )

        setattr(leg, field, datetime.now(UTC))

        if field == LegMilestone.FINALIZED_AT:
            # This leg is done — the run's other leg starts traveling right now, the
            # same "no separate confirmation" rule create_run_from_route applies to the
            # very first leg. Only ever one sibling per run (Acquisition + Sale).
            result = await session.execute(
                select(TradeLeg).where(TradeLeg.run_id == leg.run_id, TradeLeg.id != leg.id)
            )
            sibling = result.scalar_one_or_none()
            if sibling is not None and sibling.started_at is None:
                sibling.started_at = datetime.now(UTC)

        await session.commit()
        return leg


def _apply_transaction(
        leg: TradeLeg,
        quantity_scu: int,
        price_per_unit: int,
        cargo_transfer_type: CargoTransferType,
        cargo_transfer_fee: int,
        *,
        also_stamp_transferred: bool,
) -> None:
    leg.quantity_scu = quantity_scu
    leg.price_per_unit = price_per_unit
    leg.cargo_transfer_type = cargo_transfer_type
    leg.cargo_transfer_fee = cargo_transfer_fee
    leg.transaction_completed_at = datetime.now(UTC)
    if also_stamp_transferred:
        leg.transferred_at = datetime.now(UTC)


async def record_purchase(
        leg_id: UUID, quantity_scu: int, price_per_unit: int,
        cargo_transfer_type: CargoTransferType, cargo_transfer_fee: int,
) -> TradeLeg:
    async with SessionLocal() as session:
        leg = await session.get(TradeLeg, leg_id)
        if leg is None:
            raise ValueError(f"No trade leg with id {leg_id}")
        if leg.leg_type != LegType.ACQUISITION:
            raise ValueError(f"Trade leg {leg_id} is not an acquisition leg")
        if leg.transaction_completed_at is not None:
            raise ValueError(f"Trade leg {leg_id} has already recorded a purchase")

        _apply_transaction(
            leg, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee,
            also_stamp_transferred=False,
        )

        # Carry-forward: the sale leg's planned quantity follows what was actually
        # bought, so a pilot who later says "sold it all" sells what was really
        # purchased, not a stale planned amount (docs/todo.md's 614-vs-640 bug). A
        # default for the common case, not a constraint -- the pilot can still record
        # a different sale quantity, including more than was bought (selling from
        # inventory carried over from earlier play is a real, allowed case). Only
        # touches the sale leg's *plan*; update_transaction is the path for correcting
        # an already-recorded sale.
        result = await session.execute(
            select(TradeLeg).where(TradeLeg.run_id == leg.run_id, TradeLeg.id != leg.id)
        )
        sale_leg = result.scalar_one_or_none()
        if sale_leg is not None and sale_leg.transaction_completed_at is None:
            sale_leg.quantity_scu = quantity_scu

        await session.commit()
        return leg


async def record_sale(
        leg_id: UUID, quantity_scu: int, price_per_unit: int,
        cargo_transfer_type: CargoTransferType, cargo_transfer_fee: int,
) -> TradeLeg:
    async with SessionLocal() as session:
        leg = await session.get(TradeLeg, leg_id)
        if leg is None:
            raise ValueError(f"No trade leg with id {leg_id}")
        if leg.leg_type != LegType.SALE:
            raise ValueError(f"Trade leg {leg_id} is not a sale leg")
        if leg.transaction_completed_at is not None:
            raise ValueError(f"Trade leg {leg_id} has already recorded a sale")

        # Manual sale legs already have transferred_at stamped by this point (the
        # dedicated Confirm Unloaded step, advance_leg) — don't clobber that real
        # timestamp. Autoload legs never got an independent unload step, so this call
        # is the only place transferred_at happens; stamp it here alongside the sale.
        _apply_transaction(
            leg, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee,
            also_stamp_transferred=leg.transferred_at is None,
        )
        await session.commit()
        return leg


async def update_transaction(
        leg_id: UUID, quantity_scu: int | None = None, price_per_unit: int | None = None,
        cargo_transfer_fee: int | None = None, cargo_transfer_type: CargoTransferType | None = None,
) -> TradeLeg:
    """Corrects a leg's already-recorded quantity/price/fee/transfer-type, up until its
    run is finalized -- for a Whisper mishearing or any other after-the-fact fix. Doesn't
    touch transaction_completed_at/transferred_at/finalized_at: editing a done leg keeps
    it done, only its values change. No cross-check against the sibling leg's quantity --
    a sale exceeding its own run's acquisition is a real case (cargo sold from inventory
    carried over from earlier play), not something to reject."""
    async with SessionLocal() as session:
        leg = await session.get(TradeLeg, leg_id)
        if leg is None:
            raise ValueError(f"No trade leg with id {leg_id}")
        if leg.transaction_completed_at is None:
            raise ValueError(
                f"Trade leg {leg_id} has no recorded transaction yet -- "
                "use record_purchase/record_sale instead"
            )

        result = await session.execute(select(TradeRun.finalized_at).where(TradeRun.id == leg.run_id))
        if result.scalar_one_or_none() is not None:
            raise ValueError(f"Trade run {leg.run_id} is already finalized")

        for value, label in (
            (quantity_scu, "quantity_scu"), (price_per_unit, "price_per_unit"),
            (cargo_transfer_fee, "cargo_transfer_fee"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{label} can't be negative")

        if quantity_scu is not None:
            leg.quantity_scu = quantity_scu
        if price_per_unit is not None:
            leg.price_per_unit = price_per_unit
        if cargo_transfer_fee is not None:
            leg.cargo_transfer_fee = cargo_transfer_fee
        if cargo_transfer_type is not None:
            leg.cargo_transfer_type = cargo_transfer_type

        await session.commit()
        return leg


async def finalize_run(run_id: UUID) -> TradeRun:
    async with SessionLocal() as session:
        result = await session.execute(
            select(TradeRun).where(TradeRun.id == run_id).options(selectinload(TradeRun.legs))
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise ValueError(f"No trade run with id {run_id}")

        if any(next_unset_field(leg) is not None for leg in run.legs):
            raise ValueError(f"Trade run {run_id} still has unfinished legs")

        run.finalized_at = datetime.now(UTC)
        await session.commit()
        return run


async def delete_run(run_id: UUID) -> None:
    async with SessionLocal() as session:
        run = await session.get(TradeRun, run_id)
        if run is not None:
            await session.delete(run)
            await session.commit()
