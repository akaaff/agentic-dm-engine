"""add undead_servants to characters

Revision ID: e6f0a3b4c5d7
Revises: d5e9f2a3b4c6
Create Date: 2026-10-07 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f0a3b4c5d7"
down_revision: str | Sequence[str] | None = "d5e9f2a3b4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "characters",
        sa.Column("undead_servants", sa.JSON(), server_default="[]", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "undead_servants")
