from typing import Any

from pydantic import BaseModel, Field

from db import trade_run_store
from db.models import CargoTransferType, LegMilestone, LegType, TradeLeg
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousLegError
from tools.uplink_tool import UplinkTool


class MarkCargoSoldArgs(BaseModel):
    commodity: str | None = Field(
        default=None,
        description="The commodity the pilot is selling, e.g. 'Iron', 'Laranite', 'Agricium' — "
                    "used to identify which trade leg they mean, not to look anything up. May be "
                    "misspelled or phonetically transcribed from speech — pass through what the "
                    "user said, don't try to correct it yourself. Leave unset if the user only "
                    "named a location."
    )
    terminal: str | None = Field(
        default=None,
        description="An exact trading terminal/location name, e.g. 'Area18', 'Port Tressler', "
                    "'Ambitious Dream Refueling'. Only set this if the user named a specific location."
    )

    quantity_scu: int | None = Field(
        default=None,
        description="How much cargo, in SCU, the pilot actually sold, if they said a number "
                    "that differs from what was originally planned for this leg — e.g. a partial "
                    "sale. Leave unset to use the leg's already-planned quantity."
    )

    price_per_unit: int | None = Field(
        default=None,
        description="The price received per unit, in aUEC, if the pilot stated one that differs "
                    "from the leg's already-known price (prices can shift between planning a route "
                    "and actually selling). Leave unset to use the leg's already-known price."
    )

    cargo_transfer_type: CargoTransferType | None = Field(
        default=None,
        description="Whether the cargo was loaded manually (by hand, at a kiosk) or via the "
                    "terminal's automated loading, if the pilot mentioned it. Leave unset to use "
                    "whatever was already recorded for this leg when the run was planned."
    )

    cargo_transfer_fee: int | None = Field(
        default=None,
        description="Any fee paid for the cargo transfer, in aUEC, if the pilot mentioned one. "
                    "Leave unset to use the leg's already-known fee."
    )


class MarkCargoSoldTool(UplinkTool):
    name: str = "mark_cargo_sold"
    description: str = (
        "Record that the pilot has sold cargo for their current, active sale leg — call this "
        "when they report a sale, e.g. 'I sold the copper', 'sold 640 SCU of iron', 'got 14 a "
        "unit for the laranite'. Arrival and unloading don't need to be separately confirmed "
        "first — a completed sale already proves both happened, so this records them too if they "
        "weren't already marked. Quantity, price, transfer type, and fee are all optional — only "
        "pass what the pilot actually stated; anything unstated falls back to what was already "
        "planned for this leg."
    )
    args_schema: type[BaseModel] = MarkCargoSoldArgs
    progress_label: str = "Updating trade run, marking that cargo has been sold."

    async def _arun(self,
                    commodity: str | None,
                    terminal: str | None,
                    quantity_scu: int | None,
                    price_per_unit: int | None,
                    cargo_transfer_type: CargoTransferType | None,
                    cargo_transfer_fee: int | None,
                    *args: Any,
                    **kwargs: Any) -> Any:
        try:
            leg = await resolver.resolve_leg(leg_type=LegType.SALE, commodity=commodity, terminal=terminal)
            next_step = trade_run_store.next_unset_field(leg)
            quantity_scu = leg.quantity_scu if quantity_scu is None else quantity_scu
            price_per_unit = leg.price_per_unit if price_per_unit is None else price_per_unit
            cargo_transfer_type = leg.cargo_transfer_type if cargo_transfer_type is None else cargo_transfer_type
            cargo_transfer_fee = leg.cargo_transfer_fee if cargo_transfer_fee is None else cargo_transfer_fee

            # REACHED_AT (always) or TRANSFERRED_AT (manual transfer only — autoload
            # never has it as an independent step, see _SALE_AUTOLOAD_SEQUENCE) are the
            # only fields that can precede a sale; a completed sale is impossible
            # without both already being true, so it's evidence for them, not a guess.
            # Named separately, not just a bool, because "unloading" isn't a real,
            # separately-caught-up event for an autoload leg — saying so would be wrong.
            caught_up_steps = []
            if next_step is LegMilestone.REACHED_AT:
                caught_up_steps.append("arrival")
                if cargo_transfer_type == CargoTransferType.MANUAL:
                    caught_up_steps.append("unloading")
            elif next_step is LegMilestone.TRANSFERRED_AT:
                caught_up_steps.append("unloading")

            if caught_up_steps:
                leg = await self._safe_run(trade_run_store.catch_up_before_transaction(leg))
                if not isinstance(leg, TradeLeg):
                    return leg
                next_step = trade_run_store.next_unset_field(leg)

            if next_step is LegMilestone.TRANSACTION_COMPLETED_AT:
                result = await self._safe_run(trade_run_store.record_sale(
                    leg_id=leg.id,
                    quantity_scu=quantity_scu,
                    price_per_unit=price_per_unit,
                    cargo_transfer_type=cargo_transfer_type,
                    cargo_transfer_fee=cargo_transfer_fee,
                ))
                if isinstance(result, TradeLeg):
                    # TRANSACTION_COMPLETED_AT is always the sale sequence's second-to-last
                    # field for both cargo_transfer_types (see trade_run_store's
                    # _SALE_MANUAL_SEQUENCE / _SALE_AUTOLOAD_SEQUENCE), so this always lands
                    # on FINALIZED_AT — the one milestone no AI tool is allowed to set (see
                    # docs/ledger-trust-and-corrections.md). Saying the bare enum value here
                    # left the persona with no way to know that meant "tell the pilot to
                    # click Finalize" — see confirm_cargo_loaded's identical fix.
                    new_next_step = trade_run_store.next_unset_field(result)
                    if new_next_step == LegMilestone.FINALIZED_AT:
                        # Only three combinations are ever possible (see caught_up_steps
                        # above), so spelling each out reads more naturally than a
                        # generic join would for the single-item case.
                        if caught_up_steps == ["arrival", "unloading"]:
                            prefix = "Recorded arrival, unloading, and the sale"
                        elif caught_up_steps:
                            prefix = f"Recorded {caught_up_steps[0]} and the sale"
                        else:
                            prefix = "Sale recorded"
                        return f"{prefix} for {result.commodity_name} at {result.terminal_name}."
                    return (f"Advanced leg: {result.commodity_name} at {result.terminal_name} "
                            f"from {LegMilestone.TRANSACTION_COMPLETED_AT} to {new_next_step}")
                else:
                    return result
            else:
                return (f"Can't record a sale yet — this leg's next step is "
                        f"{trade_run_store.current_step_title(leg)}, not selling cargo. "
                        f"Call the right tool for that step first, or check with the pilot.")
        except ValueError as ve:
            return str(ve)
        except AmbiguousLegError as ale:
            return str(ale)
