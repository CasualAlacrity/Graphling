"""add terminal_id to trade_leg

Revision ID: 21642675ae3f
Revises: 5dc2eb205b54
Create Date: 2026-07-19 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '21642675ae3f'
down_revision: str | Sequence[str] | None = '5dc2eb205b54'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('trade_leg', sa.Column('terminal_id', sa.Integer(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('trade_leg', 'terminal_id')
