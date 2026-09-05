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
    hp: Mapped[int]
    max_hp: Mapped[int]
    ac: Mapped[int]
    speed: Mapped[int]
    proficiency_bonus: Mapped[int]
    stats: Mapped[dict[str, int]] = mapped_column(JSON)
    inventory: Mapped[list[str]] = mapped_column(JSON, default=list)
    spell_slots: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
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
