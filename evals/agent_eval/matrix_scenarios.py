"""Scenarios for the response matrix: the state a conversation starts from. A fake, in-memory
ledger (the pilot's trade runs) plus ALICE's timers, so rows like "Landed." or "How much am
I carrying?" are asked with an active run in place instead of against an empty account
(Jeff's matrix notes, 2026-10-16: "this needs context pieces").

The fake ledger replaces every ledger_client function, reads and writes alike, for every
row, scenario or not: a matrix run never reads or touches the pilot's real runs, and a
write ALICE makes (marking arrived, recording a purchase) works as it would on the server,
so her answer is about the run, not about a blocked write.
"""
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import ledger_client
from db.models import CargoTransferType, LegType
from db.trade_run_store import next_unset_field
from ledger_schemas import TradeLegOut, TradeRunOut
from voice import timer_tool

NO_RUN = "no run"
BUYING = "run: buying Copper at Seraphim"
SELLING = "run: Copper loaded, selling at Baijini"
TWO_RUNS = "two runs: buying Copper and Laranite"
TIMER = "timer: loading, 10 of 15 min left"

SCENARIOS = (NO_RUN, BUYING, SELLING, TWO_RUNS, TIMER)

# Real terminal names, so a pilot naming one ("I'm at Seraphim") matches the leg.
SERAPHIM = (1, "Admin - Seraphim")
BAIJINI = (2, "Admin - Baijini Point")
ARC_L1 = (3, "Admin - ARC-L1")


def _leg(run_id, leg_type, terminal, commodity, quantity, price, started_ago_min, **milestones) -> TradeLegOut:
    now = datetime.now(UTC)
    terminal_id, terminal_name = terminal
    return TradeLegOut(
        id=uuid.uuid4(), run_id=run_id, leg_type=leg_type, terminal_id=terminal_id, terminal_name=terminal_name,
        commodity_name=commodity, quantity_scu=quantity, price_per_unit=price,
        cargo_transfer_type=CargoTransferType.MANUAL, cargo_transfer_fee=0,
        created_at=now - timedelta(minutes=started_ago_min), started_at=now - timedelta(minutes=started_ago_min),
        reached_at=milestones.get("reached_at"), transaction_completed_at=milestones.get("transaction_completed_at"),
        transferred_at=milestones.get("transferred_at"), finalized_at=milestones.get("finalized_at"),
    )


def _run(legs_for) -> TradeRunOut:
    run_id = uuid.uuid4()
    return TradeRunOut(
        id=run_id, ship="Railen", usable_container_sizes="1,2,4,8,16,24,32",
        created_at=datetime.now(UTC) - timedelta(minutes=20), finalized_at=None, legs=legs_for(run_id),
    )


def _copper_buying(run_id) -> list[TradeLegOut]:
    return [
        _leg(run_id, LegType.ACQUISITION, SERAPHIM, "Copper", 640, 3198, 10),
        _leg(run_id, LegType.SALE, BAIJINI, "Copper", 640, 4567, 10),
    ]


def _copper_selling(run_id) -> list[TradeLegOut]:
    earlier = datetime.now(UTC) - timedelta(minutes=15)
    return [
        _leg(run_id, LegType.ACQUISITION, SERAPHIM, "Copper", 640, 3198, 30, reached_at=earlier,
             transaction_completed_at=earlier, transferred_at=earlier, finalized_at=earlier),
        _leg(run_id, LegType.SALE, BAIJINI, "Copper", 640, 4567, 15),
    ]


def _laranite_buying(run_id) -> list[TradeLegOut]:
    return [
        _leg(run_id, LegType.ACQUISITION, ARC_L1, "Laranite", 320, 2900, 5),
        _leg(run_id, LegType.SALE, BAIJINI, "Laranite", 320, 3300, 5),
    ]


class FakeLedger:
    """The ledger_client API over an in-memory list of runs."""

    def __init__(self, runs: list[TradeRunOut]):
        self.runs = runs
        self.writes: list[str] = []

    def _find_leg(self, leg_id) -> TradeLegOut:
        for run in self.runs:
            for leg in run.legs:
                if leg.id == leg_id:
                    return leg
        raise ValueError(f"No trade leg with id {leg_id}")

    async def get_in_progress_runs(self) -> list[TradeRunOut]:
        in_progress = []
        for run in self.runs:
            if run.finalized_at is None:
                in_progress.append(run)
        return in_progress

    async def get_finalized_runs(self, limit: int = 50) -> list[TradeRunOut]:
        return []

    async def advance_leg(self, leg_id) -> TradeLegOut:
        leg = self._find_leg(leg_id)
        field = next_unset_field(leg)
        if field is not None:
            setattr(leg, field.value, datetime.now(UTC))
        self.writes.append(f"advance {leg.commodity_name} {leg.leg_type.value} -> {field}")
        return leg

    async def _record(self, kind, leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        leg = self._find_leg(leg_id)
        leg.quantity_scu = quantity_scu
        leg.price_per_unit = price_per_unit
        leg.cargo_transfer_type = cargo_transfer_type
        leg.cargo_transfer_fee = cargo_transfer_fee
        leg.transaction_completed_at = datetime.now(UTC)
        self.writes.append(f"{kind} {quantity_scu} {leg.commodity_name} @ {price_per_unit}")
        return leg

    async def record_purchase(self, leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        return await self._record("purchase", leg_id, quantity_scu, price_per_unit, cargo_transfer_type,
                                  cargo_transfer_fee)

    async def record_sale(self, leg_id, quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee):
        return await self._record("sale", leg_id, quantity_scu, price_per_unit, cargo_transfer_type,
                                  cargo_transfer_fee)

    async def update_transaction(self, leg_id, quantity_scu=None, price_per_unit=None, cargo_transfer_fee=None,
                                 cargo_transfer_type=None):
        leg = self._find_leg(leg_id)
        if quantity_scu is not None:
            leg.quantity_scu = quantity_scu
        if price_per_unit is not None:
            leg.price_per_unit = price_per_unit
        if cargo_transfer_fee is not None:
            leg.cargo_transfer_fee = cargo_transfer_fee
        if cargo_transfer_type is not None:
            leg.cargo_transfer_type = cargo_transfer_type
        self.writes.append(f"correct {leg.commodity_name}: qty={quantity_scu} price={price_per_unit}")
        return leg

    async def finalize_run(self, run_id) -> TradeRunOut:
        for run in self.runs:
            if run.id == run_id:
                run.finalized_at = datetime.now(UTC)
                self.writes.append("finalize run")
                return run
        raise ValueError(f"No trade run with id {run_id}")

    async def delete_run(self, run_id) -> None:
        kept = []
        for run in self.runs:
            if run.id != run_id:
                kept.append(run)
        self.runs = kept
        self.writes.append("delete run")

    async def create_run_from_route(self, route, quantity_scu: int, ship: str | None) -> TradeRunOut:
        run_id = uuid.uuid4()
        origin = (route.origin_terminal_id, route.origin_terminal_name)
        destination = (route.destination_terminal_id, route.destination_terminal_name)
        run = TradeRunOut(
            id=run_id, ship=ship, usable_container_sizes="1,2,4,8,16,24,32", created_at=datetime.now(UTC),
            finalized_at=None, legs=[
                _leg(run_id, LegType.ACQUISITION, origin, route.commodity_name, quantity_scu,
                     round(route.price_origin), 0),
                _leg(run_id, LegType.SALE, destination, route.commodity_name, quantity_scu,
                     round(route.price_destination), 0),
            ],
        )
        self.runs.append(run)
        self.writes.append(f"start run: {quantity_scu} {route.commodity_name} to {route.destination_terminal_name}")
        return run


def _runs_for(scenario: str) -> list[TradeRunOut]:
    if scenario == BUYING:
        return [_run(_copper_buying)]
    if scenario == SELLING:
        return [_run(_copper_selling)]
    if scenario == TWO_RUNS:
        return [_run(_copper_buying), _run(_laranite_buying)]
    return []


LEDGER_FUNCTIONS = (
    "get_in_progress_runs", "get_finalized_runs", "advance_leg", "record_purchase", "record_sale",
    "update_transaction", "finalize_run", "delete_run", "create_run_from_route",
)


def install(scenario: str, stack: ExitStack) -> FakeLedger:
    """Puts the scenario in place for one conversation, undone when `stack` closes.
    ledger_client.catch_up_before_transaction stays real: it calls advance_leg by its
    module name, so it runs against the fake too."""
    ledger = FakeLedger(_runs_for(scenario))
    for name in LEDGER_FUNCTIONS:
        stack.enter_context(patch.object(ledger_client, name, getattr(ledger, name)))

    timers: dict[str, dict] = {}
    if scenario == TIMER:
        timers["Loading"] = {"started_at": datetime.now(UTC) - timedelta(minutes=5), "duration_seconds": 15 * 60}
    stack.enter_context(patch.object(timer_tool, "_timers", timers))
    return ledger

