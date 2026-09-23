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


class LegMilestone(enum.StrEnum):
    """The manually-advanced fields on TradeLeg, in no particular order here — the actual
    per-leg-type progression order lives in trade_run_store's sequence lists. started_at
    is deliberately excluded — it's auto-stamped, never advanced through by field name."""

    REACHED_AT = "reached_at"
    TRANSACTION_COMPLETED_AT = "transaction_completed_at"
    TRANSFERRED_AT = "transferred_at"
    FINALIZED_AT = "finalized_at"


class User(Base):
    """One row per pilot who's ever signed into ALICE. discord_id is its own column
    rather than doubling as the primary key, specifically so a second identity provider
    can be added later without touching every table that already FKs to users.id —
    Project Lyra/Uplink's fuller User + UserIdentity split (a provider + provider_user_id
    table alongside this one) is the shape to grow into if/when that actually happens;
    a single-provider table doesn't need that abstraction yet."""

    __tablename__ = "users"

    discord_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)


class TradeRun(Base):
    __tablename__ = "trade_run"

    # FK to users.id, not the raw Discord id directly — see User's docstring. Every row
    # before this column existed got backfilled to a "legacy" placeholder user by the
    # migration that added it (see its docstring) rather than left null.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ship: Mapped[str | None] = mapped_column(String, nullable=True)
    # CSV of SCU sizes (e.g. "1,2,4,8,16,24,32") loadable at the origin AND unloadable at
    # the destination — snapshotted from the route at creation time, same as
    # quantity_scu/price_per_unit on TradeLeg, rather than a live re-lookup. Empty string
    # for runs created before this existed or where the route had no container data.
    usable_container_sizes: Mapped[str] = mapped_column(String, nullable=False, default="")
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    legs: Mapped[list["TradeLeg"]] = relationship(
        "TradeLeg", back_populates="run", cascade="all, delete-orphan"
    )


class TradeLeg(Base):
    __tablename__ = "trade_leg"

    # Copied down from the owning TradeRun at creation time (see
    # trade_run_store.create_run_from_route) rather than only living on the parent —
    # legs are fetched directly by id in most of trade_run_store (advance_leg,
    # record_purchase/sale), so this is what a future ownership check on those paths
    # would filter by without an extra join to trade_run. Same FK-to-users.id shape as
    # TradeRun.user_id — see User's docstring.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
    # Every route for one commodity, any origin — what the overlay's filter panel wants.
    ROUTE = "route"
    # Every route out of one terminal, any commodity — what best_route wants, since a
    # pilot asking "best route from Orison" usually names no commodity.
    ROUTE_BY_ORIGIN = "route_by_origin"


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


class WikiCacheKind(enum.StrEnum):
    SHIP_SPEED = "ship_speed"
    LOCATIONS = "locations"


class WikiCache(Base):
    """Shared server-side cache for star-citizen.wiki data (ship speeds, the locations/
    positions dataset) — same kind/key/payload shape as UexPriceCache above, reused
    rather than inventing a new one. Previously an in-memory-only, per-process cache on
    StarCitizenWikiClient itself (no Postgres involved at all); moved here so pilots
    share fetches against a public API the same way the UEX cache already shares fetches
    against UEX's. `key` is the ship name (lowercased) for SHIP_SPEED, or the literal
    "_all" for LOCATIONS (a singleton, like uex_reference_cache above, but keyed rather
    than a separate table since it's one more row in an already-generic shape)."""

    __tablename__ = "wiki_cache"
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_wiki_cache_kind_key"),)

    kind: Mapped[WikiCacheKind] = mapped_column(Enum(WikiCacheKind, name="wiki_cache_kind"), nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
