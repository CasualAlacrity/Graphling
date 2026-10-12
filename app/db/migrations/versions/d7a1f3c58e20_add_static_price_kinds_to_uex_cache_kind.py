"""add item, vehicle purchase/rental and orbit distance kinds to uex_cache_kind

Revision ID: d7a1f3c58e20
Revises: c4e9a7d2f816
Create Date: 2026-10-12 00:00:00.000000

The agent's last direct-to-UEX calls (item prices, vehicle purchase/rental prices, orbit
distances) move behind the server's cache, so a shipped client holds no UEX key
(docs/llm-gateway.md, "UEX: all calls server-side"). They reuse uex_price_cache with
four new kinds. Labels are the member NAMES, as SQLAlchemy's Enum stores them (see
a7c3e91b4d20).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd7a1f3c58e20'
down_revision: str | Sequence[str] | None = 'c4e9a7d2f816'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_KINDS = ("ITEM", "VEHICLE_PURCHASE", "VEHICLE_RENTAL", "ORBIT_DISTANCES")


def upgrade() -> None:
    """Upgrade schema."""
    for kind in NEW_KINDS:
        op.execute(f"ALTER TYPE uex_cache_kind ADD VALUE IF NOT EXISTS '{kind}'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres can't drop a value from an enum type. Clearing the rows that use them is
    # all a downgrade can do; the labels stay, unused and harmless.
    kinds = ", ".join(f"'{kind}'" for kind in NEW_KINDS)
    op.execute(f"DELETE FROM uex_price_cache WHERE kind IN ({kinds})")
