"""add usable_container_sizes to trade_run

Revision ID: 115266ea4539
Revises: 21642675ae3f
Create Date: 2026-07-21 09:51:17.369384

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '115266ea4539'
down_revision: str | Sequence[str] | None = '21642675ae3f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'trade_run',
        sa.Column('usable_container_sizes', sa.String(), nullable=False, server_default=''),
    )
    op.alter_column('trade_run', 'usable_container_sizes', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('trade_run', 'usable_container_sizes')
