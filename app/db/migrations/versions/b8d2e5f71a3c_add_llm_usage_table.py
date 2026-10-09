"""add llm_usage table

Revision ID: b8d2e5f71a3c
Revises: f1a2b3c4d5e6
Create Date: 2026-10-09 00:00:00.000000

One row per model call relayed by the LLM gateway (docs/llm-gateway.md) -- per-user cost
and fallback frequency. Plain String columns for role/backend/status, not Postgres enums,
so adding a role or backend from config never needs a migration.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8d2e5f71a3c'
down_revision: str | Sequence[str] | None = 'f1a2b3c4d5e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'llm_usage',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('backend', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),
        sa.Column('latency_ms', sa.Float(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('fallback_reason', sa.String(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_llm_usage_user_id'), 'llm_usage', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_llm_usage_user_id'), table_name='llm_usage')
    op.drop_table('llm_usage')
