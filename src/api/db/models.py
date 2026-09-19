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
    """Added for the portrait feature - both new Character fields it
    introduces, round-tripped correctly here (race_index wasn't in the
    original plan's DB bullet, which only named gender, but skipping it
    would silently break portraitUrl the moment a character is reloaded via
    GET /characters/{id} rather than read straight from the create response
    - same bug shape as class_index's Day 17 fix). Nullable with no
    rows-to-backfill concern (SQLite only needs a server_default for a NOT
    NULL column added to a table that already has rows - see class_index
    above, which predates this reasoning). This project already has a
    pre-existing gap where ~20 other Character fields (class_resources,
    spell_slots-adjacent Phase-9 additions, etc.) silently drop on a DB
    round-trip (see the class_index/skill_proficiencies entries above) -
    not fixed here, out of scope for this feature."""
    equipped_weapons: Mapped[list[str]] = mapped_column(JSON, default=list)
    """Added for equipped-weapon tracking - unlike the ~20-field gap noted
    above, this one had to be fixed immediately rather than flagged as a
    follow-up: caught live the moment a freshly-created character (whose
    in-memory response correctly showed an auto-populated equipped set) was
    re-fetched and came back with an empty list - the same class of bug,
    but this field is core to the feature actually working at all (a live
    WS session builds its GameState from a *reloaded* character, so an
    always-empty equipped set would force every real attack through the
    unarmed-strike fallback, silently, regardless of what was equipped)."""
    equipped_armor: Mapped[str | None] = mapped_column(default=None)
    equipped_shield: Mapped[str | None] = mapped_column(default=None)
    """Added for armor swapping (issue #13) - same reasoning as
    equipped_weapons above, fixed immediately rather than deferred: a live
    WS session's GameState comes from a *reloaded* character, so these
    always resetting to None would silently make every equip-armor action
    pointless the moment a real session starts."""
    level: Mapped[int] = mapped_column(default=1, server_default="1")
    hit_die_sides: Mapped[int] = mapped_column(default=8, server_default="8")
    """No universal safe default across classes (d6 Wizard through d12
    Barbarian) - 8 is an arbitrary fallback for the handful of pre-migration
    rows from this session's own dev testing, not a real game value; every
    row created after this migration gets its real class's die from
    create_character, same as before."""
    hit_dice_remaining: Mapped[int] = mapped_column(default=1, server_default="1")
    saving_throw_proficiencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    known_spells: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    """Issue #30 - populated at creation for "Spells Known" casters
    (Bard/Sorcerer). Same "a live WS session builds its GameState from a
    *reloaded* character" reasoning as class_resources/equipped_weapons
    above - added with its server_default from the start this time, not
    found live after the fact."""
    fighting_style: Mapped[str | None] = mapped_column(default=None)
    class_resources: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    used_relentless_endurance_this_rest: Mapped[bool] = mapped_column(default=False)
    wild_shape_beast_index: Mapped[str | None] = mapped_column(default=None)
    pre_wild_shape_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    bardic_inspiration_die: Mapped[int | None] = mapped_column(default=None)
    """Issue #29: found live - none of these 10 fields were ever persisted,
    despite ~6 of them (level, hit_die_sides, hit_dice_remaining,
    saving_throw_proficiencies, fighting_style, class_resources) being
    populated immediately at creation and needed by *every* live session
    from the moment it starts (the exact same "a live WS session builds its
    GameState from a *reloaded* character" reasoning already documented
    above for equipped_weapons/equipped_armor/equipped_shield, just never
    applied to this batch) - class_resources silently dropping to `{}` on
    every reload meant Rage/Second Wind/Ki/Wild Shape/Arcane Recovery/
    Bardic Inspiration were all unusable ("no rage uses remaining" on a
    fresh Barbarian who'd never raged) the moment a real WS session loaded
    a character rather than using the in-memory create response directly -
    caught by live manual testing, not a unit test (pytest builds Character
    objects directly, never round-tripping through the DB, so this was
    invisible to the entire test suite). The other 4 (used_relentless_
    endurance_this_rest, wild_shape_beast_index, pre_wild_shape_snapshot,
    bardic_inspiration_die) are genuinely rarer mid-transformation/
    mid-rest-cycle state, but cost nothing extra to fix in the same pass
    rather than leaving a second copy of this exact bug for later. Every
    other ~15-field gap the class_index-era comment above still describes
    (is_dodging, bonus_action_used, death_save_successes, position, etc.)
    is genuinely combat-turn-scoped and correctly *should* reset to its
    Pydantic default for a fresh encounter build - not fixed here, and not
    the same bug shape as this batch."""
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CampaignProgress(Base):
    __tablename__ = "campaign_progress"

    id: Mapped[str] = mapped_column(primary_key=True)
    """Session id - also the shareable lobby code (issue #44): no separate
    short-code generation, the existing uuid4().hex session id doubles as
    both, an explicit scope cut to keep this issue's backend surface to
    exactly the lobby/join/resume mechanism, not also a code-shortening
    scheme #45's frontend has no stated need for yet."""
    campaign_id: Mapped[str]
    current_scene_id: Mapped[str]
    party_character_ids: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="in_progress")
    """"open" (issue #44): a lobby still accepting new players via POST
    /sessions/{id}/join, not yet playable - POST /sessions/{id}/start flips
    it to "in_progress" once the leader is ready, filling any unclaimed
    seats with the chosen companions first. The legacy POST /sessions
    (single-shot: character + companions all chosen upfront) skips the
    lobby phase entirely and defaults straight to "in_progress", matching
    its own pre-#44 behavior exactly - no lobby-waiting step existed for it
    and none is added now."""
    player_tokens: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, server_default="{}")
    """Issue #44: personal per-player token -> character id, populated by
    POST /sessions/{id}/join. Empty for a session created via the legacy
    POST /sessions (single human, no lobby/join step ever happened) - see
    api/ws/session.py's _build_real_session_setup for how an empty dict
    here falls back to the original single-human-at-party_character_ids[0]
    assumption, keeping every pre-#44 session/test working unchanged."""
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
