"""add wiki_cache table

Revision ID: f1a2b3c4d5e6
Revises: e2b7d4a19f6c
Create Date: 2026-09-25 00:00:00.000000

Shared server-side cache for star-citizen.wiki data (ship speeds, the locations/
positions dataset) — previously an in-memory-only, per-process cache on
StarCitizenWikiClient with no Postgres involvement at all. Same kind/key/payload shape
as the existing uex_price_cache table, not a new design.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: str | Sequence[str] | None = 'e2b7d4a19f6c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'wiki_cache',
        sa.Column('kind', sa.Enum('SHIP_SPEED', 'LOCATIONS', name='wiki_cache_kind'), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kind', 'key', name='uq_wiki_cache_kind_key'),
    )
    op.create_index(op.f('ix_wiki_cache_key'), 'wiki_cache', ['key'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_wiki_cache_key'), table_name='wiki_cache')
    op.drop_table('wiki_cache')
    # Postgres enum types are independent objects, not dropped along with a table that
    # merely used one — op.create_table's inline sa.Enum(...) implicitly does CREATE
    # TYPE, so without this, upgrade -> downgrade -> upgrade fails on a duplicate type.
    # Verified live: the sibling uex_cache_kind migration skips this and would hit the
    # same failure if ever round-tripped the same way.
    sa.Enum(name='wiki_cache_kind').drop(op.get_bind(), checkfirst=True)
