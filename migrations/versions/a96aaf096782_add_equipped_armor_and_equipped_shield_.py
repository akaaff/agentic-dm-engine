"""add equipped_armor and equipped_shield to characters

Revision ID: a96aaf096782
Revises: 01fcd9534fcc
Create Date: 2026-09-16 13:17:52.355346

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a96aaf096782"
down_revision: str | Sequence[str] | None = "01fcd9534fcc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no server_default needed (unlike equipped_weapons's own
    # migration) - these are optional single-item slots (None = unarmored/
    # no shield), not a NOT NULL JSON list.
    op.add_column("characters", sa.Column("equipped_armor", sa.String(), nullable=True))
    op.add_column("characters", sa.Column("equipped_shield", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "equipped_shield")
    op.drop_column("characters", "equipped_armor")
