"""rename backend 'frankencompute' to 'heket' in llm_usage and tts_usage

Revision ID: 371e7493e3fa
Revises: e3b7c91d4a52
Create Date: 2026-10-16 00:00:00.000000

Data only, no schema change. The compute node's real hostname is heket; "frankencompute"
was its nickname. Rewriting the old rows keeps one value per backend, so usage and cost
queries -- and the daily fallback budget in server/llm_gateway_service.py, which counts
every row whose backend isn't heket -- don't have to know both names.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '371e7493e3fa'
down_revision: str | Sequence[str] | None = 'e3b7c91d4a52'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE llm_usage SET backend = 'heket' WHERE backend = 'frankencompute'")
    op.execute("UPDATE tts_usage SET backend = 'heket' WHERE backend = 'frankencompute'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE llm_usage SET backend = 'frankencompute' WHERE backend = 'heket'")
    op.execute("UPDATE tts_usage SET backend = 'frankencompute' WHERE backend = 'heket'")
