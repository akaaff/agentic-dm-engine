"""add known_spells to characters

Revision ID: 0cab8bc682f5
Revises: ac48025c80e6
Create Date: 2026-09-16 22:08:07.140353

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0cab8bc682f5"
down_revision: str | Sequence[str] | None = "ac48025c80e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Issue #30 - populated at creation for "Spells Known" casters (Bard/
    # Sorcerer). server_default required - SQLite can't add a NOT NULL
    # column to a table with existing rows without one (same lesson as
    # equipped_weapons's own migration).
    op.add_column(
        "characters", sa.Column("known_spells", sa.JSON(), nullable=False, server_default="[]")
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "known_spells")
