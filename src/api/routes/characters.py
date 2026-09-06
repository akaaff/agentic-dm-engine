"""Character creation and lookup REST API. Wraps character_creation.py
(Day 5) for the actual derivation and persists the result via SQLAlchemy
(Day 8) - this route layer adds no game logic of its own."""

from __future__ import annotations

from typing import Annotated, Any

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
    ability_bonuses: dict[str, int]
    """Ability index (str/dex/con/int/wis/cha, lowercase per the SRD) ->
    flat bonus. Applied automatically server-side in create_character - this
    is exposed purely for the wizard to show *why* final scores differ from
    what the player assigned, not something the player chooses."""


class ClassSummary(BaseModel):
    index: str
    name: str
    hit_die: int


class ClassDetail(ClassSummary):
    skill_choose: int
    skill_options: list[str]
    """SRD skill-proficiency indices (e.g. "skill-athletics") the player may
    choose skill_choose of - the same set create_character's
    _validate_skill_choices enforces server-side."""


class BackgroundSummary(BaseModel):
    index: str
    name: str


class EquipmentSummary(BaseModel):
    index: str
    name: str
    category: str
    """Either 'weapon' or 'armor' - the only two categories exposed here. Optional
    starting gear beyond a class/background's automatic kit is a documented
    simplification (see character_creation.py's module docstring): the
    SRD's full nested starting-equipment-option trees aren't parsed, so this
    is a flat pick-any-number-of-these list, not a real option-tree UI."""


class CreateCharacterRequest(BaseModel):
    character_id: str
    name: str
    race_index: str
    class_index: str
    background_index: str
    base_ability_scores: dict[AbilityScore, int]
    chosen_skills: list[str]
    chosen_equipment: list[str] = []


def _race_ability_bonuses(race: dict[str, Any]) -> dict[str, int]:
    return {b["ability_score"]["index"]: b["bonus"] for b in race.get("ability_bonuses", [])}


@router.get("/races", response_model=list[RaceSummary])
def list_races() -> list[RaceSummary]:
    srd = load_srd()
    return [
        RaceSummary(
            index=r["index"],
            name=r["name"],
            speed=r["speed"],
            ability_bonuses=_race_ability_bonuses(r),
        )
        for r in srd.races.values()
    ]


@router.get("/classes", response_model=list[ClassSummary])
def list_classes() -> list[ClassSummary]:
    srd = load_srd()
    return [
        ClassSummary(index=c["index"], name=c["name"], hit_die=c["hit_die"])
        for c in srd.classes.values()
    ]


@router.get("/classes/{class_index}", response_model=ClassDetail)
def get_class(class_index: str) -> ClassDetail:
    srd = load_srd()
    cls = srd.classes.get(class_index)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"Class {class_index} not found")

    # Mirrors character_creation._validate_skill_choices exactly - summed
    # across every proficiency_choices entry, same as that function does
    # (needed for e.g. Bard's two separate pools, see CLAUDE.md).
    skill_choose = 0
    skill_options: list[str] = []
    for choice in cls.get("proficiency_choices", []):
        skill_choose += choice["choose"]
        skill_options.extend(option["item"]["index"] for option in choice["from"]["options"])

    return ClassDetail(
        index=cls["index"],
        name=cls["name"],
        hit_die=cls["hit_die"],
        skill_choose=skill_choose,
        skill_options=skill_options,
    )


@router.get("/backgrounds", response_model=list[BackgroundSummary])
def list_backgrounds() -> list[BackgroundSummary]:
    srd = load_srd()
    return [BackgroundSummary(index=b["index"], name=b["name"]) for b in srd.backgrounds.values()]


@router.get("/equipment", response_model=list[EquipmentSummary])
def list_equipment() -> list[EquipmentSummary]:
    srd = load_srd()
    result = []
    for item in srd.equipment.values():
        if item.get("weapon_category"):
            result.append(
                EquipmentSummary(index=item["index"], name=item["name"], category="weapon")
            )
        elif item.get("armor_category"):
            result.append(
                EquipmentSummary(index=item["index"], name=item["name"], category="armor")
            )
    return result


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
        class_index=character.class_index,
        hp=character.hp,
        max_hp=character.max_hp,
        ac=character.ac,
        speed=character.speed,
        proficiency_bonus=character.proficiency_bonus,
        stats=dict(character.stats),
        inventory=list(character.inventory),
        skill_proficiencies=list(character.skill_proficiencies),
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
        class_index=record.class_index,
        hp=record.hp,
        max_hp=record.max_hp,
        ac=record.ac,
        speed=record.speed,
        proficiency_bonus=record.proficiency_bonus,
        position=Position(x=0, y=0),
        stats=record.stats,  # type: ignore[arg-type]
        inventory=record.inventory,
        skill_proficiencies=record.skill_proficiencies,
        spell_slots={int(level): count for level, count in record.spell_slots.items()},
        conditions=[Condition.model_validate(c) for c in record.conditions],
    )
