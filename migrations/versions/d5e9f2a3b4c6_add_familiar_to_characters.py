"""add familiar to characters

Revision ID: d5e9f2a3b4c6
Revises: c4d8e1f2a3b5
Create Date: 2026-10-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e9f2a3b4c6"
down_revision: str | Sequence[str] | None = "c4d8e1f2a3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("characters", sa.Column("familiar", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "familiar")
