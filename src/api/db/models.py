"""SQLAlchemy declarative models. Unlike the sibling repos (which hand-write
raw SQL migrations against Postgres, partly for pgvector's exotic column
types), this project uses full ORM models + Alembic autogenerate - SQLite
has no exotic types here, so there's no reason to give up autogenerate.

Three tables: `characters` (created PCs and pregen companions - persisted
independently of any in-progress encounter's GameState), `campaign_progress`
(which scene a session is on), `episodes` (judge/eval history, written
starting Day 13)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CharacterRecord(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    race: Mapped[str]
    class_: Mapped[str]
    background: Mapped[str]
    is_pc: Mapped[bool] = mapped_column(default=True)
    is_companion: Mapped[bool] = mapped_column(default=False)
    persona: Mapped[str | None] = mapped_column(default=None)
    class_index: Mapped[str | None] = mapped_column(default=None)
    """Added Day 17 - was missing since Day 8, before Character.class_index
    (Day 14, needed for spellcasting-ability lookup) existed. Found live via
    the character-creator wizard's own re-fetch-after-create check: a
    created Fighter round-tripped fine (no spellcasting to lose), but the
    field was silently dropped for every character regardless of class."""
    hp: Mapped[int]
    max_hp: Mapped[int]
    ac: Mapped[int]
    speed: Mapped[int]
    proficiency_bonus: Mapped[int]
    stats: Mapped[dict[str, int]] = mapped_column(JSON)
    inventory: Mapped[list[str]] = mapped_column(JSON, default=list)
    skill_proficiencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    """Also added Day 17, same gap as class_index above (predates Day 13,
    when Character.skill_proficiencies was introduced) - found the same way."""
    spell_slots: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    race_index: Mapped[str | None] = mapped_column(default=None)
    gender: Mapped[str | None] = mapped_column(default=None)
    hair_color: Mapped[str | None] = mapped_column(default=None)
    """Added for the portrait feature - all three new Character fields it
    introduces, round-tripped correctly here (race_index wasn't in the
    original plan's DB bullet, which only named gender/hair_color, but
    skipping it would silently break portraitUrl the moment a character is
    reloaded via GET /characters/{id} rather than read straight from the
    create response - same bug shape as class_index's Day 17 fix). Nullable
    with no rows-to-backfill concern (SQLite only needs a server_default
    for a NOT NULL column added to a table that already has rows - see
    class_index above, which predates this reasoning). This project already
    has a pre-existing gap where ~20 other Character fields (class_resources,
    spell_slots-adjacent Phase-9 additions, etc.) silently drop on a DB
    round-trip (see the class_index/skill_proficiencies entries above) -
    not fixed here, out of scope for this feature."""
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CampaignProgress(Base):
    __tablename__ = "campaign_progress"

    id: Mapped[str] = mapped_column(primary_key=True)
    """Session id."""
    campaign_id: Mapped[str]
    current_scene_id: Mapped[str]
    party_character_ids: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="in_progress")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(primary_key=True)
    campaign_id: Mapped[str]
    judge_score: Mapped[float | None] = mapped_column(default=None)
    summary: Mapped[str | None] = mapped_column(default=None)
    transcript: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
