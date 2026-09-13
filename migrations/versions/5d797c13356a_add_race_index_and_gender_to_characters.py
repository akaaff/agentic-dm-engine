"""add race_index and gender to characters

Revision ID: 5d797c13356a
Revises: 9c7ebc00ac93
Create Date: 2026-09-13 18:14:21.317827

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d797c13356a"
down_revision: str | Sequence[str] | None = "9c7ebc00ac93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("characters", sa.Column("race_index", sa.String(), nullable=True))
    op.add_column("characters", sa.Column("gender", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "gender")
    op.drop_column("characters", "race_index")
