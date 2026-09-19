"""add player_tokens to campaign_progress

Revision ID: a7237fd2c628
Revises: 0cab8bc682f5
Create Date: 2026-09-19 16:01:10.954069

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7237fd2c628"
down_revision: str | Sequence[str] | None = "0cab8bc682f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "campaign_progress",
        sa.Column("player_tokens", sa.JSON(), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("campaign_progress", "player_tokens")
