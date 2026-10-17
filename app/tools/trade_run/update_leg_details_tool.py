from typing import Any

from pydantic import BaseModel, Field

import ledger_client
from db import trade_run_store
from db.models import CargoTransferType, LegMilestone, LegType
from ledger_schemas import TradeLegOut
from tools.trade_run import resolver
from tools.trade_run.resolver import AmbiguousLegError
from tools.uplink_tool import UplinkTool


class UpdateLegDetailsArgs(BaseModel):
    commodity: str | None = Field(
        default=None,
        description="The commodity the pilot means, only to identify which leg — may be "
                    "misspelled or phonetically transcribed from speech, pass through what "
                    "the user said. Leave unset if they didn't name one."
    )
    terminal: str | None = Field(
        default=None,
        description="An exact trading terminal/location name, only if the user named a specific location."
    )
    quantity_scu: int | None = Field(
        default=None, description="Cargo quantity in SCU, if the pilot stated one."
    )
    price_per_unit: int | None = Field(
        default=None, description="Price per unit in aUEC, if the pilot stated one."
    )
    cargo_transfer_type: CargoTransferType | None = Field(
        default=None,
        description="Whether the cargo is loaded/unloaded manually or via the terminal's autoload, "
                    "if the pilot said so."
    )
    cargo_transfer_fee: int | None = Field(
        default=None,
        description="The autoload / cargo transfer fee in aUEC, if the pilot stated one."
    )


class UpdateLegDetailsTool(UplinkTool):
    name: str = "update_leg_details"
    description: str = (
        "Save buy/sell details the pilot calls out on their current leg WITHOUT completing the "
        "transaction — use this whenever they state a fee, quantity, price or loading type but "
        "have not said they actually bought or sold. Typical case: sitting at the commodity "
        "kiosk reading off the numbers a click before confirming, e.g. 'the autoload fee is "
        "19,350', 'it's 600 SCU at 14 a unit'. An 'autoload fee' is money (cargo_transfer_fee in "
        "aUEC), never a duration — a stated time is a separate start_timer. Also use it to "
        "correct a value on a transaction that was already recorded. Only pass what the pilot "
        "actually stated. Never use this to record the purchase or sale itself — "
        "mark_cargo_acquired / mark_cargo_sold do that, and only when the pilot says they "
        "completed it."
    )
    args_schema: type[BaseModel] = UpdateLegDetailsArgs
    progress_label: str = "Updating trade run, saving the details."

    async def _arun(self,
                    commodity: str | None = None,
                    terminal: str | None = None,
                    quantity_scu: int | None = None,
                    price_per_unit: int | None = None,
                    cargo_transfer_type: CargoTransferType | None = None,
                    cargo_transfer_fee: int | None = None,
                    *args: Any,
                    **kwargs: Any) -> Any:
        if all(value is None for value in (quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee)):
            return "Nothing to update — the pilot didn't state a quantity, price, fee or loading type."
        try:
            leg = await resolver.resolve_leg(commodity=commodity, terminal=terminal)
            result = await self._safe_run(ledger_client.update_transaction(
                leg.id,
                quantity_scu=quantity_scu,
                price_per_unit=price_per_unit,
                cargo_transfer_fee=cargo_transfer_fee,
                cargo_transfer_type=cargo_transfer_type,
            ))
        except ValueError as ve:
            return str(ve)
        except AmbiguousLegError as ale:
            return str(ale)
        if not isinstance(result, TradeLegOut):
            return result

        saved = _describe_saved(quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee)
        message = f"Saved {saved} on the {result.commodity_name} leg at {result.terminal_name}."
        if result.transaction_completed_at is None and trade_run_store.next_unset_field(result) in (
                LegMilestone.REACHED_AT, LegMilestone.TRANSACTION_COMPLETED_AT):
            verb = "bought" if result.leg_type == LegType.ACQUISITION else "sold"
            message += (f" The {verb} step has NOT been recorded — ask the pilot whether they've "
                        f"completed it, and only record it if they confirm.")
        return message


def _describe_saved(quantity_scu, price_per_unit, cargo_transfer_type, cargo_transfer_fee) -> str:
    parts = []
    if quantity_scu is not None:
        parts.append(f"quantity {quantity_scu} SCU")
    if price_per_unit is not None:
        parts.append(f"price {price_per_unit:,} aUEC per unit")
    if cargo_transfer_type is not None:
        parts.append(f"{cargo_transfer_type.value} loading")
    if cargo_transfer_fee is not None:
        parts.append(f"fee {cargo_transfer_fee:,} aUEC")
    return ", ".join(parts)
