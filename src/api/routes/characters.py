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
from src.engine.character_creation import (
    CharacterCreationError,
    class_skill_choice_pool,
    create_character,
)
from src.engine.position import Position
from src.engine.rules import class_equipment_options
from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.state import AbilityScore, Character, Condition, WildShapeSnapshot

router = APIRouter(prefix="/characters", tags=["characters"])

DbSession = Annotated[Session, Depends(get_db)]


class RaceTrait(BaseModel):
    index: str
    name: str
    desc: str


class RaceSummary(BaseModel):
    index: str
    name: str
    speed: int
    ability_bonuses: dict[str, int]
    """Ability index (str/dex/con/int/wis/cha, lowercase per the SRD) ->
    flat bonus. Applied automatically server-side in create_character - this
    is exposed purely for the wizard to show *why* final scores differ from
    what the player assigned, not something the player chooses."""
    traits: list[RaceTrait]
    """Base-race traits only (e.g. Darkvision, Dwarven Resilience) - display
    only, same "subraces not applied" simplification as the ability bonuses
    above (Day 5). Not mechanically enforced anywhere in the engine."""


class ClassSummary(BaseModel):
    index: str
    name: str
    hit_die: int


class SpellSummary(BaseModel):
    index: str
    name: str
    desc: str


class ClassDetail(ClassSummary):
    skill_choose: int
    skill_options: list[str]
    """SRD skill-proficiency indices (e.g. "skill-athletics") the player may
    choose skill_choose of - the same set create_character's
    _validate_skill_choices enforces server-side."""
    equipment_options: list[str]
    """Weapon/armor equipment indices this class is SRD-proficient with -
    the same set create_character's chosen_equipment validation enforces
    server-side, exposed so the wizard's optional-gear step only offers
    legal choices instead of erroring after submission."""
    cantrips: list[SpellSummary]
    """Level-0 SRD spells this class's `classes` list includes it in - empty
    for non-casters (e.g. Fighter). The character sheet's "what can I cast"
    list reads this directly rather than the engine tracking known spells
    per character - see character_creation.py's module docstring for why
    this project doesn't otherwise restrict cast_spell to a known-spell list."""


class SkillSummary(BaseModel):
    index: str
    name: str
    ability: str
    desc: str


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
    damage_dice: str | None = None
    damage_type: str | None = None
    properties: list[str] = []
    """Weapon-only fields (issue #15) - the same SRD data turn_engine.py's
    _pc_attack_params already reads for real damage/attack math, just never
    surfaced to the frontend. `properties` is property names only (e.g.
    "finesse", "light"), not the full {index,name,url} objects."""
    ac_base: int | None = None
    ac_dex_bonus: bool = False
    ac_max_bonus: int | None = None
    stealth_disadvantage: bool = False
    """Armor-only fields (issue #15) - same shape rules.armor_ac already
    reads. `ac_base` is a shield's flat bonus, not a full AC, when
    category == "armor" but the SRD item's own armor_category is "Shield"
    (there's no separate shield category here - shields are folded into
    "armor" like everywhere else this endpoint's category split already
    was, before this feature)."""


class CreateCharacterRequest(BaseModel):
    character_id: str
    name: str
    race_index: str
    class_index: str
    background_index: str
    base_ability_scores: dict[AbilityScore, int]
    chosen_skills: list[str]
    chosen_equipment: list[str] = []
    gender: str | None = None
    fighting_style: str | None = None
    """create_character has accepted fighting_style since Phase 9I, but this
    request model never exposed it - a pre-existing gap, closed here while
    the wizard is being touched anyway for the portrait-selection fields."""
    chosen_racial_skills: list[str] | None = None
    """Only meaningful (and required) for a Half-Elf - Skill Versatility
    (issue #23), 2 skills of the player's choice. create_character itself
    rejects it for any other race."""


def _race_ability_bonuses(race: dict[str, Any]) -> dict[str, int]:
    return {b["ability_score"]["index"]: b["bonus"] for b in race.get("ability_bonuses", [])}


def _race_traits(race: dict[str, Any], srd: SrdIndex) -> list[RaceTrait]:
    result = []
    for ref in race.get("traits", []):
        entry = srd.traits.get(ref["index"])
        if entry is None:
            continue
        desc = entry.get("desc", [])
        result.append(
            RaceTrait(
                index=entry["index"],
                name=entry["name"],
                desc=" ".join(desc) if isinstance(desc, list) else desc,
            )
        )
    return result


@router.get("/races", response_model=list[RaceSummary])
def list_races() -> list[RaceSummary]:
    srd = load_srd()
    return [
        RaceSummary(
            index=r["index"],
            name=r["name"],
            speed=r["speed"],
            ability_bonuses=_race_ability_bonuses(r),
            traits=_race_traits(r, srd),
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

    # Shares the exact same pool character_creation._validate_skill_choices
    # enforces server-side - was duplicated inline here and had drifted into
    # a real bug (crashed on Monk, whose tool/instrument choice isn't a flat
    # reference list - see that function's docstring for the full story).
    skill_choose, skill_options = class_skill_choice_pool(cls)

    cantrips = [
        SpellSummary(
            index=spell["index"],
            name=spell["name"],
            desc=" ".join(spell["desc"]) if isinstance(spell["desc"], list) else spell["desc"],
        )
        for spell in srd.spells.values()
        if spell.get("level") == 0
        and any(c["index"] == class_index for c in spell.get("classes", []))
    ]

    return ClassDetail(
        index=cls["index"],
        name=cls["name"],
        hit_die=cls["hit_die"],
        skill_choose=skill_choose,
        skill_options=sorted(skill_options),
        equipment_options=class_equipment_options(cls, srd),
        cantrips=sorted(cantrips, key=lambda s: s.name),
    )


@router.get("/skills", response_model=list[SkillSummary])
def list_skills() -> list[SkillSummary]:
    """Real SRD skill descriptions (not hand-written) - the wizard's skill
    hints use these directly rather than duplicating flavor text client-side.
    `index` is the "skill-<name>" form used everywhere else (skill_options,
    Character.skill_proficiencies), not srd.skills' own bare-index keys."""
    srd = load_srd()
    return [
        SkillSummary(
            index=f"skill-{s['index']}",
            name=s["name"],
            ability=s["ability_score"]["name"],
            desc=" ".join(s["desc"]) if isinstance(s["desc"], list) else s["desc"],
        )
        for s in srd.skills.values()
    ]


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
            damage = item.get("damage") or {}
            result.append(
                EquipmentSummary(
                    index=item["index"],
                    name=item["name"],
                    category="weapon",
                    damage_dice=damage.get("damage_dice"),
                    damage_type=(damage.get("damage_type") or {}).get("index"),
                    properties=[p["index"] for p in (item.get("properties") or [])],
                )
            )
        elif item.get("armor_category"):
            ac_info = item.get("armor_class") or {}
            result.append(
                EquipmentSummary(
                    index=item["index"],
                    name=item["name"],
                    category="armor",
                    ac_base=ac_info.get("base"),
                    ac_dex_bonus=bool(ac_info.get("dex_bonus")),
                    ac_max_bonus=ac_info.get("max_bonus"),
                    stealth_disadvantage=bool(item.get("stealth_disadvantage")),
                )
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
            gender=body.gender,
            fighting_style=body.fighting_style,
            chosen_racial_skills=body.chosen_racial_skills,
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
        race_index=character.race_index,
        gender=character.gender,
        equipped_weapons=list(character.equipped_weapons),
        equipped_armor=character.equipped_armor,
        equipped_shield=character.equipped_shield,
        level=character.level,
        hit_die_sides=character.hit_die_sides,
        hit_dice_remaining=character.hit_dice_remaining,
        saving_throw_proficiencies=list(character.saving_throw_proficiencies),
        fighting_style=character.fighting_style,
        class_resources=dict(character.class_resources),
        used_relentless_endurance_this_rest=character.used_relentless_endurance_this_rest,
        wild_shape_beast_index=character.wild_shape_beast_index,
        pre_wild_shape_snapshot=(
            character.pre_wild_shape_snapshot.model_dump()
            if character.pre_wild_shape_snapshot
            else None
        ),
        bardic_inspiration_die=character.bardic_inspiration_die,
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
        race_index=record.race_index,
        gender=record.gender,
        equipped_weapons=record.equipped_weapons,
        equipped_armor=record.equipped_armor,
        equipped_shield=record.equipped_shield,
        level=record.level,
        hit_die_sides=record.hit_die_sides,
        hit_dice_remaining=record.hit_dice_remaining,
        saving_throw_proficiencies=record.saving_throw_proficiencies,  # type: ignore[arg-type]
        fighting_style=record.fighting_style,
        class_resources=record.class_resources,
        used_relentless_endurance_this_rest=record.used_relentless_endurance_this_rest,
        wild_shape_beast_index=record.wild_shape_beast_index,
        pre_wild_shape_snapshot=(
            WildShapeSnapshot.model_validate(record.pre_wild_shape_snapshot)
            if record.pre_wild_shape_snapshot
            else None
        ),
        bardic_inspiration_die=record.bardic_inspiration_die,
    )
