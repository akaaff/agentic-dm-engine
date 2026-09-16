"""add level, class_resources, and other missing Character fields to characters

Revision ID: ac48025c80e6
Revises: a96aaf096782
Create Date: 2026-09-16 17:10:21.045844

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ac48025c80e6"
down_revision: str | Sequence[str] | None = "a96aaf096782"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # These 6 (level through class_resources) are populated at creation and
    # needed by every live session from the moment it starts - found live
    # (issue #29): class_resources silently dropping to {} on every reload
    # made Rage/Second Wind/Ki/Wild Shape/Arcane Recovery/Bardic Inspiration
    # all unusable. server_default required on the NOT NULL ones - SQLite
    # can't add a NOT NULL column to a table with existing rows without one
    # (same lesson as equipped_weapons's own migration).
    op.add_column(
        "characters", sa.Column("level", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "characters", sa.Column("hit_die_sides", sa.Integer(), nullable=False, server_default="8")
    )
    op.add_column(
        "characters",
        sa.Column("hit_dice_remaining", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "characters",
        sa.Column("saving_throw_proficiencies", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("characters", sa.Column("fighting_style", sa.String(), nullable=True))
    op.add_column(
        "characters", sa.Column("class_resources", sa.JSON(), nullable=False, server_default="{}")
    )
    # The remaining 4 are rarer mid-transformation/mid-rest-cycle state -
    # nullable/optional, no server_default needed.
    op.add_column(
        "characters",
        sa.Column(
            "used_relentless_endurance_this_rest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("characters", sa.Column("wild_shape_beast_index", sa.String(), nullable=True))
    op.add_column("characters", sa.Column("pre_wild_shape_snapshot", sa.JSON(), nullable=True))
    op.add_column("characters", sa.Column("bardic_inspiration_die", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("characters", "bardic_inspiration_die")
    op.drop_column("characters", "pre_wild_shape_snapshot")
    op.drop_column("characters", "wild_shape_beast_index")
    op.drop_column("characters", "used_relentless_endurance_this_rest")
    op.drop_column("characters", "class_resources")
    op.drop_column("characters", "fighting_style")
    op.drop_column("characters", "saving_throw_proficiencies")
    op.drop_column("characters", "hit_dice_remaining")
    op.drop_column("characters", "hit_die_sides")
    op.drop_column("characters", "level")
