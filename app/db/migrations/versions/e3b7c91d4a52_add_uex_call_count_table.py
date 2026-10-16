"""add uex_call_count table

Revision ID: e3b7c91d4a52
Revises: d7a1f3c58e20
Create Date: 2026-10-16 00:00:00.000000

One row per UTC day counting UEX requests, so server/uex_budget.py can stop live calls
before UEX's 7,000-a-day limit, even across restarts.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3b7c91d4a52'
down_revision: str | Sequence[str] | None = 'd7a1f3c58e20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'uex_call_count',
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('calls', sa.Integer(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('day'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('uex_call_count')
