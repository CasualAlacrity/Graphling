"""Pure, DB-free helpers for TradeRun/TradeLeg — operate only on already-loaded objects.
Shared by both server/ledger_service.py (which fetches those objects from Postgres) and
the client's ledger_client.py (which deserializes them from the server's JSON) — neither
needs its own copy of this logic. The DB-touching functions that used to live here moved
to server/ledger_service.py once a real server, not a single-pilot-per-process desktop
client, became the only thing that talks to Postgres (docs/todo.md Phase 2).
"""
from datetime import timedelta

from db.models import CargoTransferType, LegMilestone, LegType, TradeLeg, TradeRun

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
_ACQUISITION_SEQUENCE = [
    LegMilestone.REACHED_AT, LegMilestone.TRANSACTION_COMPLETED_AT,
    LegMilestone.TRANSFERRED_AT, LegMilestone.FINALIZED_AT,
]
_SALE_MANUAL_SEQUENCE = [
    LegMilestone.REACHED_AT, LegMilestone.TRANSFERRED_AT,
    LegMilestone.TRANSACTION_COMPLETED_AT, LegMilestone.FINALIZED_AT,
]
_SALE_AUTOLOAD_SEQUENCE = [LegMilestone.REACHED_AT, LegMilestone.TRANSACTION_COMPLETED_AT, LegMilestone.FINALIZED_AT]

_STEP_TITLES = {
    LegType.ACQUISITION: {
        LegMilestone.REACHED_AT: "Mark arrived",
        LegMilestone.TRANSACTION_COMPLETED_AT: "Buy cargo",
        LegMilestone.TRANSFERRED_AT: "Confirm loaded",
        LegMilestone.FINALIZED_AT: "Finalize",
    },
    LegType.SALE: {
        LegMilestone.REACHED_AT: "Mark arrived",
        LegMilestone.TRANSFERRED_AT: "Confirm unloaded",
        LegMilestone.TRANSACTION_COMPLETED_AT: "Sell cargo",
        LegMilestone.FINALIZED_AT: "Finalize",
    },
}


def _milestone_sequence(leg: TradeLeg) -> list[LegMilestone]:
    if leg.leg_type == LegType.ACQUISITION:
        return _ACQUISITION_SEQUENCE
    if leg.cargo_transfer_type == CargoTransferType.MANUAL:
        return _SALE_MANUAL_SEQUENCE
    return _SALE_AUTOLOAD_SEQUENCE


def next_unset_field(leg: TradeLeg) -> LegMilestone | None:
    return next((field for field in _milestone_sequence(leg) if getattr(leg, field) is None), None)


def current_step_title(leg: TradeLeg) -> str:
    field = next_unset_field(leg)
    if field is None:
        return "Leg finalized"
    if field == LegMilestone.FINALIZED_AT:
        return "Mark leg finalized"
    return _STEP_TITLES[leg.leg_type][field]


# Describes what the pilot hasn't done yet, not the action's name — trade_run_info feeds
# this to the LLM, and _STEP_TITLES' phrasing ("next step: Mark arrived") reads as an
# instruction to call the mark_arrived tool rather than a status description, since it's
# nearly identical wording to the tool itself. _STEP_TITLES stays as-is for the overlay UI,
# where it labels an actual button the pilot clicks — imperative phrasing is correct there.
_PENDING_STATE_DESCRIPTIONS = {
    LegType.ACQUISITION: {
        LegMilestone.REACHED_AT: "not yet arrived",
        LegMilestone.TRANSACTION_COMPLETED_AT: "arrived, cargo not yet purchased",
        LegMilestone.TRANSFERRED_AT: "purchased, cargo not yet loaded",
        LegMilestone.FINALIZED_AT: "loaded, leg not yet finalized",
    },
    LegType.SALE: {
        LegMilestone.REACHED_AT: "not yet arrived",
        LegMilestone.TRANSFERRED_AT: "arrived, cargo not yet unloaded",
        LegMilestone.TRANSACTION_COMPLETED_AT: "unloaded, cargo not yet sold",
        LegMilestone.FINALIZED_AT: "sold, leg not yet finalized",
    },
}


def current_step_description(leg: TradeLeg) -> str:
    field = next_unset_field(leg)
    if field is None:
        return "leg finalized"
    return _PENDING_STATE_DESCRIPTIONS[leg.leg_type][field]


def trade_run_info(run: TradeRun) -> str:
    """Describes every leg of a run, not just the current one, so a question like "what's
    my destination" is answerable regardless of which leg is currently active — the
    current leg additionally reports its next milestone via current_step_title."""
    active_leg = current_leg(run)
    lines = [f"Trade run status — ship: {run.ship or 'unspecified'}"]
    for leg in ordered_legs(run):
        leg_label = "Acquisition" if leg.leg_type == LegType.ACQUISITION else "Sale"
        detail = (f"{leg_label} — {leg.quantity_scu} SCU {leg.commodity_name} at "
                  f"{leg.terminal_name}, {leg.price_per_unit} aUEC/unit")
        if leg.finalized_at is not None:
            status = "finalized"
        elif leg is active_leg:
            status = f"current — {current_step_description(leg)}"
        else:
            status = "pending"
        lines.append(f"{detail} ({status})")
    return "\n".join(lines)


def breadcrumb_steps(leg: TradeLeg) -> list[tuple[LegMilestone, str]]:
    """(field, label) pairs for the leg's remaining milestones, skipping reached_at — the
    UI renders that one as its own combined Travel node instead."""
    return [
        (field, _STEP_TITLES[leg.leg_type][field])
        for field in _milestone_sequence(leg)
        if field != LegMilestone.REACHED_AT
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


def ordered_legs(run) -> list[TradeLeg]:
    acquisitions = sorted(
        (leg for leg in run.legs if leg.leg_type == LegType.ACQUISITION), key=lambda leg: leg.created_at
    )
    sales = sorted((leg for leg in run.legs if leg.leg_type == LegType.SALE), key=lambda leg: leg.created_at)
    return acquisitions + sales


def current_leg(run) -> TradeLeg | None:
    return next((leg for leg in ordered_legs(run) if leg.finalized_at is None), None)
