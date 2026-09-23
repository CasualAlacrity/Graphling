"""add user_id to trade_run and trade_leg

Revision ID: c1a4f6e2b9d3
Revises: a7c3e91b4d20
Create Date: 2026-09-25 00:00:00.000000

Phase 2 multi-tenancy foundation (docs/todo.md) — every trade-ledger row now belongs to
a pilot. Existing rows predate any pilot identity, so they're backfilled to the literal
tenant "legacy" rather than left null; there's no real second pilot yet for that value to
collide with.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1a4f6e2b9d3'
down_revision: str | Sequence[str] | None = 'a7c3e91b4d20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('trade_run', sa.Column('user_id', sa.String(), nullable=False, server_default='legacy'))
    op.alter_column('trade_run', 'user_id', server_default=None)
    op.create_index(op.f('ix_trade_run_user_id'), 'trade_run', ['user_id'])

    op.add_column('trade_leg', sa.Column('user_id', sa.String(), nullable=False, server_default='legacy'))
    op.alter_column('trade_leg', 'user_id', server_default=None)
    op.create_index(op.f('ix_trade_leg_user_id'), 'trade_leg', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_trade_leg_user_id'), table_name='trade_leg')
    op.drop_column('trade_leg', 'user_id')

    op.drop_index(op.f('ix_trade_run_user_id'), table_name='trade_run')
    op.drop_column('trade_run', 'user_id')
