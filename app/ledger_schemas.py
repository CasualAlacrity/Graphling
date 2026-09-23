from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from db.models import CargoTransferType, LegType
from tools.uexcorp.trade_data import UEXTradeRoute


class TradeLegOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    leg_type: LegType
    terminal_id: int
    terminal_name: str
    commodity_name: str
    quantity_scu: int
    price_per_unit: int
    cargo_transfer_type: CargoTransferType
    cargo_transfer_fee: int
    created_at: datetime
    started_at: datetime | None
    reached_at: datetime | None
    transaction_completed_at: datetime | None
    transferred_at: datetime | None
    finalized_at: datetime | None


class TradeRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ship: str | None
    usable_container_sizes: str
    created_at: datetime
    finalized_at: datetime | None
    legs: list[TradeLegOut]


class CreateRunRequest(BaseModel):
    route: UEXTradeRoute
    quantity_scu: int
    ship: str | None = None


class RecordTransactionRequest(BaseModel):
    quantity_scu: int
    price_per_unit: int
    cargo_transfer_type: CargoTransferType
    cargo_transfer_fee: int
