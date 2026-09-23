"""add users table, switch trade_run/trade_leg.user_id to a real FK

Revision ID: e2b7d4a19f6c
Revises: c1a4f6e2b9d3
Create Date: 2026-09-25 00:00:00.000000

Follow-up to c1a4f6e2b9d3: that migration stamped a plain Discord-id string directly
onto the ledger. Comparing against two earlier projects with the same Discord-login
shape (both of which use a users table decoupled from the auth provider, specifically
to support adding a second provider later without touching every FK'd table) surfaced
that as a gap worth closing now rather than migrating under pressure later — see
docs/todo.md's Phase 2 section. This creates that users table and swaps user_id on both
ledger tables from string to a FK against it.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2b7d4a19f6c'
down_revision: str | Sequence[str] | None = 'c1a4f6e2b9d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEDGER_TABLES = ('trade_run', 'trade_leg')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'users',
        sa.Column('discord_id', sa.String(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('discord_id', name='uq_users_discord_id'),
    )
    op.create_index(op.f('ix_users_discord_id'), 'users', ['discord_id'])

    # One users row per distinct string already sitting in user_id (today that's just
    # "legacy", from c1a4f6e2b9d3's own backfill) — a one-time bulk insert via Postgres's
    # own gen_random_uuid(), not the ORM's Python-side default.
    op.execute("""
        INSERT INTO users (id, discord_id, created_at)
        SELECT gen_random_uuid(), distinct_id, now()
        FROM (
            SELECT user_id AS distinct_id FROM trade_run
            UNION
            SELECT user_id AS distinct_id FROM trade_leg
        ) AS existing_ids
    """)

    for table in _LEDGER_TABLES:
        op.drop_index(f'ix_{table}_user_id', table_name=table)
        op.add_column(table, sa.Column('user_id_uuid', sa.UUID(), nullable=True))
        op.execute(f"""
            UPDATE {table}
            SET user_id_uuid = users.id
            FROM users
            WHERE users.discord_id = {table}.user_id
        """)
        op.drop_column(table, 'user_id')
        op.alter_column(table, 'user_id_uuid', new_column_name='user_id', nullable=False)
        op.create_index(op.f(f'ix_{table}_user_id'), table, ['user_id'])
        op.create_foreign_key(
            f'{table}_user_id_fkey', table, 'users', ['user_id'], ['id'], ondelete='CASCADE'
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in _LEDGER_TABLES:
        op.drop_constraint(f'{table}_user_id_fkey', table, type_='foreignkey')
        op.drop_index(op.f(f'ix_{table}_user_id'), table_name=table)
        op.add_column(table, sa.Column('user_id_str', sa.String(), nullable=True))
        op.execute(f"""
            UPDATE {table}
            SET user_id_str = users.discord_id
            FROM users
            WHERE users.id = {table}.user_id
        """)
        op.drop_column(table, 'user_id')
        op.alter_column(table, 'user_id_str', new_column_name='user_id', nullable=False)
        op.create_index(op.f(f'ix_{table}_user_id'), table, ['user_id'])

    op.drop_index(op.f('ix_users_discord_id'), table_name='users')
    op.drop_table('users')
