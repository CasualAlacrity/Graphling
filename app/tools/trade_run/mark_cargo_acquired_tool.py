from typing import Any

from pydantic import BaseModel, Field

from db import trade_run_store
from db.models import CargoTransferType, LegMilestone, LegType, TradeLeg
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousLegError
from tools.uplink_tool import UplinkTool


class MarkCargoAcquiredArgs(BaseModel):
    commodity: str | None = Field(
        default=None,
        description="The commodity the pilot is buying, e.g. 'Iron', 'Laranite', 'Agricium' — "
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
        description="How much cargo, in SCU, the pilot actually bought, if they said a number "
                    "that differs from what was originally planned for this leg — e.g. they filled "
                    "less of their hold than intended. Leave unset to use the leg's already-planned "
                    "quantity."
    )

    price_per_unit: int | None = Field(
        default=None,
        description="The price paid per unit, in aUEC, if the pilot stated one that differs from "
                    "the leg's already-known price (prices can shift between planning a route and "
                    "actually buying). Leave unset to use the leg's already-known price."
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


class MarkCargoAcquiredTool(UplinkTool):
    name: str = "mark_cargo_acquired"
    description: str = (
        "Record that the pilot has bought cargo for their current, active acquisition leg — "
        "call this when they report a purchase, e.g. 'I bought the copper', 'picked up 640 SCU "
        "of iron', 'got the laranite for 14 a unit'. Only works once the pilot has already "
        "confirmed arrival (see mark_arrived) — if arrival hasn't been marked yet, this will "
        "fail and tell you so instead of recording the purchase. Quantity, price, transfer type, "
        "and fee are all optional — only pass what the pilot actually stated; anything unstated "
        "falls back to what was already planned for this leg. This only records the transaction; "
        "it does not confirm the cargo has physically finished loading."
    )
    args_schema: type[BaseModel] = MarkCargoAcquiredArgs
    progress_label: str = "Updating trade run, marking that cargo has been acquired."

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
            leg = await resolver.resolve_leg(leg_type=LegType.ACQUISITION, commodity=commodity, terminal=terminal)
            next_step = trade_run_store.next_unset_field(leg)
            quantity_scu = leg.quantity_scu if quantity_scu is None else quantity_scu
            price_per_unit = leg.price_per_unit if price_per_unit is None else price_per_unit
            cargo_transfer_type = leg.cargo_transfer_type if cargo_transfer_type is None else cargo_transfer_type
            cargo_transfer_fee = leg.cargo_transfer_fee if cargo_transfer_fee is None else cargo_transfer_fee

            if next_step is LegMilestone.TRANSACTION_COMPLETED_AT:
                result = await self._safe_run(trade_run_store.record_purchase(
                    leg_id=leg.id,
                    quantity_scu=quantity_scu,
                    price_per_unit=price_per_unit,
                    cargo_transfer_type=cargo_transfer_type,
                    cargo_transfer_fee=cargo_transfer_fee,
                ))
                if isinstance(result, TradeLeg):
                    new_next_step = trade_run_store.next_unset_field(result)
                    return (f"Advanced leg: {result.commodity_name} at {result.terminal_name} "
                            f"from {LegMilestone.TRANSACTION_COMPLETED_AT} to {new_next_step}")
                else:
                    return result
            else:
                return (f"Can't record a purchase yet — this leg's next step is "
                        f"{trade_run_store.current_step_title(leg)}, not buying cargo. "
                        f"Call the right tool for that step first, or check with the pilot.")
        except ValueError as ve:
            return str(ve)
        except AmbiguousLegError as ale:
            return str(ale)
