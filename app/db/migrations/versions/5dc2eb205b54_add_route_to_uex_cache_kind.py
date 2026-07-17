"""add route to uex_cache_kind

Revision ID: 5dc2eb205b54
Revises: f3f235d81f18
Create Date: 2026-07-17 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5dc2eb205b54'
down_revision: str | Sequence[str] | None = 'f3f235d81f18'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE uex_cache_kind ADD VALUE 'ROUTE'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres has no ALTER TYPE ... DROP VALUE — removing an enum value requires
    # rebuilding the type from scratch, which isn't worth it for a downgrade path.
    raise NotImplementedError("Cannot remove a value from a Postgres enum type")
