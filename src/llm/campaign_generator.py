"""LLM-assisted campaign generator (Day 23).

The teacher model composes narrative content and picks from two closed,
schema-enforced sets (monster_index, skill) - a dynamically-built `Literal`
per generation call turns into a JSON-schema `enum`, so Ollama's grammar-
constrained decoding cannot produce a monster/skill that doesn't exist in
the vendored SRD (the same "make it structurally impossible to hallucinate"
technique Day 12's `ParsedAction.verb` already relies on, applied to a
dynamic rather than fixed set of choices).

Everything else - scene ids, the next_scene_id chain, battle maps, monster
spawn placement - is assembled by plain Python afterward, never trusted to
the model: a generated campaign is validated against the exact same
Campaign/Scene/Encounter pydantic models a hand-authored one is, by
construction, not by hoping the LLM's raw JSON happens to match. Only the
handful of cross-field business rules a JSON schema can't express (scene
count and combat-scene count for the requested size, type/payload pairing)
need a real validate-and-retry loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from src.engine.campaign import (
    DEFAULT_CAMPAIGNS_DIR,
    Campaign,
    CampaignSize,
    Scene,
    SkillChallengeDef,
)
from src.engine.encounter import DEFAULT_ENCOUNTERS_DIR, Encounter, MonsterSpawn
from src.engine.position import BattleMap, Position, TerrainType
from src.engine.srd_loader import SrdIndex, load_srd
from src.llm.providers import chat_structured, load_prompt

# Curated, not "every CR<=0.5 SRD monster" (110 of those, including deer and
# housecats) - kept to monsters comparable in toughness (2-13 HP) to what's
# already been hand-authored and live-verified (Day 6/22), so a generated
# encounter doesn't risk the kind of unusually long death-save slugfest a
# tankier monster (worg, zombie, 20+ HP) produced in Day 22's own testing.
_ALLOWED_MONSTERS = (
    "goblin",
    "wolf",
    "kobold",
    "bandit",
    "skeleton",
    "giant-rat",
    "stirge",
    "flying-snake",
    "blood-hawk",
    "cultist",
    "giant-centipede",
    "giant-poisonous-snake",
)

# (min_scenes, max_scenes, required combat-scene count) per size - matches
# the shape of this project's own hand-authored campaigns (Day 6's one-shot,
# Day 22's short-arc/full).
_SIZE_RULES: dict[CampaignSize, tuple[int, int, int]] = {
    "one_shot": (3, 3, 1),
    "short_arc": (3, 4, 1),
    "full": (6, 8, 2),
}

_MAX_ATTEMPTS = 3

_MONSTER_COUNT_RANGE = (2, 4)
_DC_RANGE = (10, 15)

_BATTLE_MAP_WIDTH = 8
_BATTLE_MAP_HEIGHT = 5
_PARTY_SPAWN_NAMES = ["party_1", "party_2", "party_3", "party_4"]
_MONSTER_SPAWN_NAMES = ["monster_1", "monster_2", "monster_3", "monster_4"]


class GenerationError(ValueError):
    pass


def _build_schema(
    monster_choices: tuple[str, ...], skill_choices: tuple[str, ...]
) -> type[BaseModel]:
    monster_index_type = Literal[tuple(monster_choices)]  # type: ignore[valid-type]
    skill_type = Literal[tuple(skill_choices)]  # type: ignore[valid-type]

    class GeneratedCombatBeat(BaseModel):
        monster_index: monster_index_type  # type: ignore[valid-type]
        monster_count: int

    class GeneratedSkillBeat(BaseModel):
        skill: skill_type  # type: ignore[valid-type]
        dc: int
        success_text: str
        failure_text: str

    class GeneratedScene(BaseModel):
        type: Literal["narrative_beat", "combat", "skill_challenge", "roleplay"]
        narrative_intro: str
        combat: GeneratedCombatBeat | None = None
        skill_challenge: GeneratedSkillBeat | None = None

    class GeneratedCampaign(BaseModel):
        title: str
        description: str
        scenes: list[GeneratedScene]

    return GeneratedCampaign


def _scene_guidance(size: CampaignSize) -> str:
    min_scenes, max_scenes, combats = _SIZE_RULES[size]
    scene_count_text = (
        f"exactly {min_scenes} scenes total"
        if min_scenes == max_scenes
        else f"{min_scenes}-{max_scenes} scenes total"
    )
    return (
        f'Generate {scene_count_text}, exactly {combats} of type "combat". '
        "You may also include skill_challenge and roleplay scenes to round out the story."
    )


def _validate_generated(generated: BaseModel, size: CampaignSize) -> list[str]:
    """Returns a list of human-readable problems - empty means valid.
    Cross-field business rules a JSON schema enum can't express on its own,
    unlike monster_index/skill (enforced structurally, see module docstring)."""
    problems: list[str] = []
    min_scenes, max_scenes, required_combats = _SIZE_RULES[size]
    scenes = generated.scenes  # type: ignore[attr-defined]

    if not (min_scenes <= len(scenes) <= max_scenes):
        problems.append(f"expected {min_scenes}-{max_scenes} scenes total, got {len(scenes)}")

    combat_scenes = [s for s in scenes if s.type == "combat"]
    if len(combat_scenes) != required_combats:
        problems.append(
            f"expected exactly {required_combats} scene(s) of type=combat, got {len(combat_scenes)}"
        )

    for i, scene in enumerate(scenes):
        if scene.type == "combat" and scene.combat is None:
            problems.append(f"scene {i} is type=combat but is missing its combat payload")
        if scene.type == "skill_challenge" and scene.skill_challenge is None:
            problems.append(
                f"scene {i} is type=skill_challenge but is missing its skill_challenge payload"
            )

    return problems


def _generated_battle_map() -> BattleMap:
    """A fixed template (not derived from monster_count) - same shape as
    every hand-authored encounter in this project: mostly open floor with a
    couple of difficult-terrain squares for texture, no walls (minimizes the
    risk of a procedurally-placed spawn point ending up unreachable, a risk
    a hand-authored map's human author would just notice and avoid)."""
    open_row: list[TerrainType] = ["floor"] * _BATTLE_MAP_WIDTH
    textured_row: list[TerrainType] = [
        "floor",
        "floor",
        "floor",
        "difficult",
        "difficult",
        "floor",
        "floor",
        "floor",
    ]
    terrain = [open_row, textured_row, list(open_row), textured_row, list(open_row)]
    spawn_points = {
        "party_1": Position(x=0, y=1),
        "party_2": Position(x=0, y=2),
        "party_3": Position(x=0, y=3),
        "party_4": Position(x=1, y=2),
        "monster_1": Position(x=7, y=1),
        "monster_2": Position(x=7, y=2),
        "monster_3": Position(x=7, y=3),
        "monster_4": Position(x=6, y=2),
    }
    return BattleMap(
        width=_BATTLE_MAP_WIDTH,
        height=_BATTLE_MAP_HEIGHT,
        terrain=terrain,
        spawn_points=spawn_points,
    )


def _build_encounter(
    encounter_id: str, monster_index: str, monster_count: int, srd: SrdIndex
) -> Encounter:
    monster_count = max(_MONSTER_COUNT_RANGE[0], min(_MONSTER_COUNT_RANGE[1], monster_count))
    monsters = [
        MonsterSpawn(
            monster_index=monster_index,
            character_id=f"{monster_index}_{i + 1}",
            spawn_point=_MONSTER_SPAWN_NAMES[i],
        )
        for i in range(monster_count)
    ]
    monster_name = srd.monsters[monster_index]["name"]
    return Encounter(
        id=encounter_id,
        name=f"{monster_name} Encounter",
        battle_map=_generated_battle_map(),
        monsters=monsters,
        party_spawn_points=_PARTY_SPAWN_NAMES,
    )


def generate_campaign(
    campaign_id: str,
    size: CampaignSize,
    srd: SrdIndex | None = None,
    campaigns_dir: Path = DEFAULT_CAMPAIGNS_DIR,
    encounters_dir: Path = DEFAULT_ENCOUNTERS_DIR,
) -> Campaign:
    """Generates, validates, and saves a new campaign (plus one Encounter
    YAML per combat scene) under `campaigns_dir`/`encounters_dir` - same
    file layout and same pydantic models a hand-authored campaign uses, so
    `load_campaign(campaign_id)` afterward can't tell the difference."""
    srd = srd or load_srd()
    monster_choices = tuple(m for m in _ALLOWED_MONSTERS if m in srd.monsters)
    skill_choices = tuple(sorted(srd.skills))
    schema = _build_schema(monster_choices, skill_choices)

    monster_list = "\n".join(
        f"- {idx}: {srd.monsters[idx]['name']} (CR {srd.monsters[idx]['challenge_rating']}, "
        f"{srd.monsters[idx]['hit_points']} HP)"
        for idx in monster_choices
    )
    base_prompt = load_prompt("campaign_gen").format(
        size=size,
        scene_guidance=_scene_guidance(size),
        monster_list=monster_list,
        skill_list=", ".join(skill_choices),
    )

    problems: list[str] = []
    generated: BaseModel | None = None
    for _attempt in range(_MAX_ATTEMPTS):
        message = base_prompt
        if problems:
            message += (
                "\n\nYour previous attempt was rejected for: "
                + "; ".join(problems)
                + ". Fix this and try again."
            )
        generated = chat_structured(
            messages=[{"role": "user", "content": message}], schema=schema, temperature=0.8
        )
        problems = _validate_generated(generated, size)
        if not problems:
            break
    else:
        raise GenerationError(
            f"Campaign generation for size={size!r} failed validation after "
            f"{_MAX_ATTEMPTS} attempts: {'; '.join(problems)}"
        )

    assert generated is not None
    gen_scenes = generated.scenes  # type: ignore[attr-defined]

    scenes: list[Scene] = []
    for i, gen_scene in enumerate(gen_scenes):
        scene_id = f"scene_{i + 1}"
        next_scene_id = f"scene_{i + 2}" if i + 1 < len(gen_scenes) else None

        encounter_ref: str | None = None
        skill_challenge_def: SkillChallengeDef | None = None
        if gen_scene.type == "combat":
            encounter_ref = f"{campaign_id}__{scene_id}"
            encounter = _build_encounter(
                encounter_ref, gen_scene.combat.monster_index, gen_scene.combat.monster_count, srd
            )
            encounters_dir.mkdir(parents=True, exist_ok=True)
            (encounters_dir / f"{encounter_ref}.yaml").write_text(
                yaml.safe_dump(encounter.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
            )
        elif gen_scene.type == "skill_challenge":
            dc = max(_DC_RANGE[0], min(_DC_RANGE[1], gen_scene.skill_challenge.dc))
            skill_challenge_def = SkillChallengeDef(
                skill=gen_scene.skill_challenge.skill,
                dc=dc,
                success_text=gen_scene.skill_challenge.success_text,
                failure_text=gen_scene.skill_challenge.failure_text,
            )

        scenes.append(
            Scene(
                id=scene_id,
                type=gen_scene.type,
                narrative_intro=gen_scene.narrative_intro,
                encounter_ref=encounter_ref,
                skill_challenge_def=skill_challenge_def,
                next_scene_id=next_scene_id,
            )
        )

    campaign = Campaign(
        id=campaign_id,
        title=generated.title,  # type: ignore[attr-defined]
        size=size,
        description=generated.description,  # type: ignore[attr-defined]
        scenes=scenes,
    )
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    (campaigns_dir / f"{campaign_id}.yaml").write_text(
        yaml.safe_dump(campaign.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    return campaign
