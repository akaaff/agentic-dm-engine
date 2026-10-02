"""add session_snapshot to campaign_progress

Revision ID: b3f1c2d4e5a6
Revises: a9c7ddc3736e
Create Date: 2026-10-02 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f1c2d4e5a6"
down_revision: str | Sequence[str] | None = "a9c7ddc3736e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("campaign_progress", sa.Column("session_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("campaign_progress", "session_snapshot")
