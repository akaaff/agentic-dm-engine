"""Character creation and lookup REST API. Wraps character_creation.py
(Day 5) for the actual derivation and persists the result via SQLAlchemy
(Day 8) - this route layer adds no game logic of its own."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.db.models import CharacterRecord
from src.api.db.session import get_db
from src.engine.character_creation import CharacterCreationError, create_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import AbilityScore, Character, Condition

router = APIRouter(prefix="/characters", tags=["characters"])

DbSession = Annotated[Session, Depends(get_db)]


class RaceSummary(BaseModel):
    index: str
    name: str
    speed: int


class ClassSummary(BaseModel):
    index: str
    name: str
    hit_die: int


class BackgroundSummary(BaseModel):
    index: str
    name: str


class CreateCharacterRequest(BaseModel):
    character_id: str
    name: str
    race_index: str
    class_index: str
    background_index: str
    base_ability_scores: dict[AbilityScore, int]
    chosen_skills: list[str]
    chosen_equipment: list[str] = []


@router.get("/races", response_model=list[RaceSummary])
def list_races() -> list[RaceSummary]:
    srd = load_srd()
    return [
        RaceSummary(index=r["index"], name=r["name"], speed=r["speed"]) for r in srd.races.values()
    ]


@router.get("/classes", response_model=list[ClassSummary])
def list_classes() -> list[ClassSummary]:
    srd = load_srd()
    return [
        ClassSummary(index=c["index"], name=c["name"], hit_die=c["hit_die"])
        for c in srd.classes.values()
    ]


@router.get("/backgrounds", response_model=list[BackgroundSummary])
def list_backgrounds() -> list[BackgroundSummary]:
    srd = load_srd()
    return [BackgroundSummary(index=b["index"], name=b["name"]) for b in srd.backgrounds.values()]


@router.post("", response_model=Character, status_code=201)
def create_character_endpoint(body: CreateCharacterRequest, db: DbSession) -> Character:
    if db.get(CharacterRecord, body.character_id) is not None:
        raise HTTPException(status_code=409, detail=f"Character {body.character_id} already exists")

    try:
        character = create_character(
            character_id=body.character_id,
            name=body.name,
            race_index=body.race_index,
            class_index=body.class_index,
            background_index=body.background_index,
            base_ability_scores=body.base_ability_scores,
            chosen_skills=body.chosen_skills,
            chosen_equipment=body.chosen_equipment,
        )
    except CharacterCreationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(_character_to_record(character))
    db.commit()
    return character


@router.get("/{character_id}", response_model=Character)
def get_character(character_id: str, db: DbSession) -> Character:
    record = db.get(CharacterRecord, character_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Character {character_id} not found")
    return _record_to_character(record)


def _character_to_record(character: Character) -> CharacterRecord:
    return CharacterRecord(
        id=character.id,
        name=character.name,
        race=character.race,
        class_=character.class_,
        background=character.background,
        is_pc=character.is_pc,
        is_companion=character.is_companion,
        persona=character.persona,
        hp=character.hp,
        max_hp=character.max_hp,
        ac=character.ac,
        speed=character.speed,
        proficiency_bonus=character.proficiency_bonus,
        stats=dict(character.stats),
        inventory=list(character.inventory),
        spell_slots={str(level): count for level, count in character.spell_slots.items()},
        conditions=[c.model_dump() for c in character.conditions],
    )


def _record_to_character(record: CharacterRecord) -> Character:
    return Character(
        id=record.id,
        name=record.name,
        race=record.race,
        class_=record.class_,
        background=record.background,
        is_pc=record.is_pc,
        is_companion=record.is_companion,
        persona=record.persona,
        hp=record.hp,
        max_hp=record.max_hp,
        ac=record.ac,
        speed=record.speed,
        proficiency_bonus=record.proficiency_bonus,
        position=Position(x=0, y=0),
        stats=record.stats,  # type: ignore[arg-type]
        inventory=record.inventory,
        spell_slots={int(level): count for level, count in record.spell_slots.items()},
        conditions=[Condition.model_validate(c) for c in record.conditions],
    )
