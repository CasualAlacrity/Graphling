from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.models import CargoTransferType, LegType, TradeLeg, TradeRun
from db.session import SessionLocal
from tools.cargo_packing import format_container_sizes, usable_container_sizes
from tools.uexcorp.trade_data import UEXTradeRoute

# started_at is deliberately not in these sequences — a leg is considered "already
# traveling" from the moment it exists (see create_run_from_route / advance_leg's
# finalized_at branch, which stamp it automatically), not something a pilot ever
# advances through manually. Only reached_at is a real, waited-on milestone.
#
# ACQUISITION always stops independently at transferred_at (Confirm Loaded) — loading is
# real separate time regardless of transfer type. SALE only does the same for MANUAL:
# unloading cargo by hand genuinely takes time before the sale can be recorded at the
# kiosk. AUTOLOAD unloading has no real time cost — record_sale stamps transferred_at in
# the same call as the sale, so it's never independently reachable for that case.
_ACQUISITION_SEQUENCE = ["reached_at", "transaction_completed_at", "transferred_at", "finalized_at"]
_SALE_MANUAL_SEQUENCE = ["reached_at", "transferred_at", "transaction_completed_at", "finalized_at"]
_SALE_AUTOLOAD_SEQUENCE = ["reached_at", "transaction_completed_at", "finalized_at"]

_STEP_TITLES = {
    LegType.ACQUISITION: {
        "reached_at": "Mark arrived",
        "transaction_completed_at": "Buy cargo",
        "transferred_at": "Confirm loaded",
        "finalized_at": "Finalize",
    },
    LegType.SALE: {
        "reached_at": "Mark arrived",
        "transferred_at": "Confirm unloaded",
        "transaction_completed_at": "Sell cargo",
        "finalized_at": "Finalize",
    },
}


def _milestone_sequence(leg: TradeLeg) -> list[str]:
    if leg.leg_type == LegType.ACQUISITION:
        return _ACQUISITION_SEQUENCE
    if leg.cargo_transfer_type == CargoTransferType.MANUAL:
        return _SALE_MANUAL_SEQUENCE
    return _SALE_AUTOLOAD_SEQUENCE


def next_unset_field(leg: TradeLeg) -> str | None:
    return next((field for field in _milestone_sequence(leg) if getattr(leg, field) is None), None)


def current_step_title(leg: TradeLeg) -> str:
    field = next_unset_field(leg)
    if field is None:
        return "Leg finalized"
    if field == "finalized_at":
        return "Mark leg finalized"
    return _STEP_TITLES[leg.leg_type][field]


def breadcrumb_steps(leg: TradeLeg) -> list[tuple[str, str]]:
    """(field, label) pairs for the leg's remaining milestones, skipping reached_at — the
    UI renders that one as its own combined Travel node instead."""
    return [
        (field, _STEP_TITLES[leg.leg_type][field])
        for field in _milestone_sequence(leg)
        if field != "reached_at"
    ]


def run_investment(run: TradeRun) -> int:
    return sum(leg.quantity_scu * leg.price_per_unit for leg in run.legs if leg.leg_type == LegType.ACQUISITION)


def run_revenue(run: TradeRun) -> int:
    return sum(leg.quantity_scu * leg.price_per_unit for leg in run.legs if leg.leg_type == LegType.SALE)


def run_fees(run: TradeRun) -> int:
    return sum(leg.cargo_transfer_fee for leg in run.legs)


def run_profit(run: TradeRun) -> int:
    return run_revenue(run) - run_investment(run) - run_fees(run)


def run_acquired_scu(run: TradeRun) -> int:
    return sum(leg.quantity_scu for leg in run.legs if leg.leg_type == LegType.ACQUISITION)


def run_sold_scu(run: TradeRun) -> int:
    return sum(leg.quantity_scu for leg in run.legs if leg.leg_type == LegType.SALE)


def run_duration(run: TradeRun) -> timedelta:
    # Ledger-only (only ever called on finalized runs) — the gap between starting to
    # track the run and finalizing it, not flight time or any in-game clock.
    return run.finalized_at - run.created_at


async def create_run_from_route(route: UEXTradeRoute, quantity_scu: int, ship: str | None) -> TradeRun:
    acquisition = TradeLeg(
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
    run = TradeRun(ship=ship, usable_container_sizes=format_container_sizes(usable_sizes), legs=[acquisition, sale])

    async with SessionLocal() as session:
        session.add(run)
        await session.commit()
        result = await session.execute(
            select(TradeRun).where(TradeRun.id == run.id).options(selectinload(TradeRun.legs))
        )
        return result.scalar_one()


async def get_in_progress_runs() -> list[TradeRun]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(TradeRun)
            .where(TradeRun.finalized_at.is_(None))
            .options(selectinload(TradeRun.legs))
            .order_by(TradeRun.created_at)
        )
        return list(result.scalars().all())


async def get_finalized_runs(limit: int = 50) -> list[TradeRun]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(TradeRun)
            .where(TradeRun.finalized_at.is_not(None))
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
        if field == "transaction_completed_at":
            raise ValueError(
                f"Trade leg {leg_id}'s next step needs purchase/sale data — "
                "use record_purchase/record_sale instead of advance_leg"
            )

        setattr(leg, field, datetime.now(UTC))

        if field == "finalized_at":
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
