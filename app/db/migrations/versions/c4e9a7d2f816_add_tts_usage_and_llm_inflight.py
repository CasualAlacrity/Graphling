"""add tts_usage table and llm_usage.inflight_at_start

Revision ID: c4e9a7d2f816
Revises: b8d2e5f71a3c
Create Date: 2026-10-12 00:00:00.000000

tts_usage: one row per speech request through the TTS gateway (server/tts_service.py),
the speech counterpart of llm_usage. llm_usage.inflight_at_start: how many requests were
already running on heket when each call arrived -- real concurrency for the
hardware-sizing questions in docs/analytics.md. Nullable, so existing rows stay valid.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e9a7d2f816'
down_revision: str | Sequence[str] | None = 'b8d2e5f71a3c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('llm_usage', sa.Column('inflight_at_start', sa.Integer(), nullable=True))
    op.create_table(
        'tts_usage',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('backend', sa.String(), nullable=False),
        sa.Column('voice', sa.String(), nullable=False),
        sa.Column('characters', sa.Integer(), nullable=False),
        sa.Column('audio_seconds', sa.Float(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('fallback_reason', sa.String(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tts_usage_user_id'), 'tts_usage', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_tts_usage_user_id'), table_name='tts_usage')
    op.drop_table('tts_usage')
    op.drop_column('llm_usage', 'inflight_at_start')
