"""add route_by_origin to uex_cache_kind

The existing ROUTE kind caches every route for one *commodity*, which is the shape the
overlay's filter panel searches by. best_route searches by *origin terminal* with the
commodity usually unspecified ("best route from Orison"), so it couldn't reuse that cache
and hit UEX live on every search, once per candidate origin.

This kind caches all routes out of one terminal, unfiltered by commodity, so a
region search warms progressively instead of re-paying the same cost each time.

Revision ID: a7c3e91b4d20
Revises: 115266ea4539
Create Date: 2026-09-18 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e91b4d20'
down_revision: str | Sequence[str] | None = '115266ea4539'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLAlchemy's Enum(UexCacheKind) stores the member NAME, not the value — so the
    # type's labels are COMMODITY/TERMINAL/ROUTE, matching 5dc2eb205b54's 'ROUTE'.
    op.execute("ALTER TYPE uex_cache_kind ADD VALUE IF NOT EXISTS 'ROUTE_BY_ORIGIN'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres has no ALTER TYPE ... DROP VALUE — removing an enum value requires
    # rebuilding the type from scratch, which isn't worth it for a downgrade path.
    raise NotImplementedError("Cannot remove a value from a Postgres enum type")
