"""add expertise to characters

Revision ID: c4d8e1f2a3b5
Revises: b3f1c2d4e5a6
Create Date: 2026-10-07 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d8e1f2a3b5"
down_revision: str | Sequence[str] | None = "b3f1c2d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "characters", sa.Column("expertise", sa.JSON(), server_default="[]", nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "expertise")
