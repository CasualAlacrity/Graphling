import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class LegType(enum.StrEnum):
    ACQUISITION = "acquisition"
    SALE = "sale"


class CargoTransferType(enum.StrEnum):
    MANUAL = "manual"
    AUTOLOAD = "autoload"


class TradeRun(Base):
    __tablename__ = "trade_run"

    ship: Mapped[str | None] = mapped_column(String, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    legs: Mapped[list["TradeLeg"]] = relationship(
        "TradeLeg", back_populates="run", cascade="all, delete-orphan"
    )


class TradeLeg(Base):
    __tablename__ = "trade_leg"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trade_run.id", ondelete="CASCADE"), nullable=False
    )
    leg_type: Mapped[LegType] = mapped_column(Enum(LegType, name="leg_type"), nullable=False)

    # Matches the UEX reference-cache terminal id (JSONB blob, not a normalized table — same
    # pattern UexPriceCache.entity_id already uses), not a foreign key. Lets the UI look up
    # live terminal details (city/outpost/station name) that aren't worth duplicating onto
    # this row permanently.
    terminal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    terminal_name: Mapped[str] = mapped_column(String, nullable=False)
    commodity_name: Mapped[str] = mapped_column(String, nullable=False)
    quantity_scu: Mapped[int] = mapped_column(Integer, nullable=False)
    price_per_unit: Mapped[int] = mapped_column(Integer, nullable=False)

    cargo_transfer_type: Mapped[CargoTransferType] = mapped_column(
        Enum(CargoTransferType, name="cargo_transfer_type"), nullable=False
    )
    cargo_transfer_fee: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transaction_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["TradeRun"] = relationship("TradeRun", back_populates="legs")


class UexCacheKind(enum.StrEnum):
    COMMODITY = "commodity"
    TERMINAL = "terminal"
    ROUTE = "route"


class UexPriceCache(Base):
    __tablename__ = "uex_price_cache"
    __table_args__ = (UniqueConstraint("kind", "entity_id", name="uq_uex_price_cache_kind_entity"),)

    kind: Mapped[UexCacheKind] = mapped_column(Enum(UexCacheKind, name="uex_cache_kind"), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rows: Mapped[list] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UexReferenceCacheRecord(Base):
    """Singleton row holding the full UexReferenceCache bundle (commodities, terminals,
    vehicles, etc). Structural reference data, not per-entity price data — one row is
    replaced wholesale on every rebuild rather than keyed like uex_price_cache."""

    __tablename__ = "uex_reference_cache"

    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
