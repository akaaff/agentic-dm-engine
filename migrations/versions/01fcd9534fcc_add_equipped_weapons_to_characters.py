"""add equipped_weapons to characters

Revision ID: 01fcd9534fcc
Revises: 5d797c13356a
Create Date: 2026-09-14 19:05:31.599656

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "01fcd9534fcc"
down_revision: str | Sequence[str] | None = "5d797c13356a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default needed (unlike the model's plain Python-side
    # default=list) because SQLite can't add a NOT NULL column to a table
    # that already has rows without one - same lesson as
    # skill_proficiencies's own migration.
    op.add_column(
        "characters",
        sa.Column("equipped_weapons", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "equipped_weapons")
