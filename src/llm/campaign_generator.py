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

import random
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from src.engine.battle_map_templates import (
    MONSTER_SPAWN_NAMES,
    PARTY_SPAWN_NAMES,
    build_battle_map,
)
from src.engine.campaign import (
    DEFAULT_CAMPAIGNS_DIR,
    Campaign,
    CampaignSize,
    Scene,
    SkillChallengeDef,
)
from src.engine.encounter import DEFAULT_ENCOUNTERS_DIR, Encounter, MonsterSpawn
from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.state import Character
from src.llm.providers import chat_structured, contains_cjk, load_prompt

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

# Story-adaptive-encounters Phase 3: a live continuation is deliberately
# short - it's answering "what happens next, given this one choice," not
# authoring a whole act. Nothing like _SIZE_RULES' combat-count requirement
# applies here; a continuation may have zero combat scenes at all.
_CONTINUATION_MIN_SCENES = 1
_CONTINUATION_MAX_SCENES = 3


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
        # Battle-map shape (issue-tracked as the "story-adaptive encounters"
        # initiative's first piece) - a closed set of layout/size/terrain-
        # amount choices, matched to this scene's own narrative_intro.
        # Defaults reproduce the old fixed template's own shape, so an
        # older/degenerate response that omits these still resolves to a
        # sane encounter rather than failing validation.
        layout: Literal["open_room", "narrow_corridor", "two_rooms", "cluttered"] = "open_room"
        size: Literal["small", "medium", "large"] = "medium"
        difficult_terrain: Literal["none", "light", "heavy"] = "none"
        hazard: Literal["none", "light", "heavy"] = "none"

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

    # Issue #16: qwen2.5 (of Chinese origin) can drift into Chinese despite
    # campaign_gen.md's explicit English-only instruction - feeds back into
    # this same retry loop (with a problem description the model can act
    # on) rather than a separate mechanism, since every generated-text field
    # already flows through here.
    if contains_cjk(generated.title) or contains_cjk(generated.description):  # type: ignore[attr-defined]
        problems.append("title/description contains non-English text")
    for i, scene in enumerate(scenes):
        if contains_cjk(scene.narrative_intro):
            problems.append(f"scene {i}'s narrative_intro contains non-English text")
        if scene.skill_challenge is not None and (
            contains_cjk(scene.skill_challenge.success_text)
            or contains_cjk(scene.skill_challenge.failure_text)
        ):
            problems.append(f"scene {i}'s skill_challenge text contains non-English text")

    return problems


def _build_encounter(
    encounter_id: str,
    gen_combat: BaseModel,
    srd: SrdIndex,
    rng: random.Random,
) -> Encounter:
    """`gen_combat` is a `GeneratedCombatBeat` instance (the dynamically-
    built nested class from `_build_schema` - not imported/typed directly
    since it's rebuilt fresh per call with that call's own monster_index
    enum). Battle-map shape comes from `battle_map_templates.build_battle_
    map`, driven entirely by `gen_combat`'s own closed-set layout/size/
    terrain fields - never a raw grid the model produced itself."""
    monster_index: str = gen_combat.monster_index  # type: ignore[attr-defined]
    monster_count = max(
        _MONSTER_COUNT_RANGE[0],
        min(_MONSTER_COUNT_RANGE[1], gen_combat.monster_count),  # type: ignore[attr-defined]
    )
    monsters = [
        MonsterSpawn(
            monster_index=monster_index,
            character_id=f"{monster_index}_{i + 1}",
            spawn_point=MONSTER_SPAWN_NAMES[i],
        )
        for i in range(monster_count)
    ]
    monster_name = srd.monsters[monster_index]["name"]
    battle_map = build_battle_map(
        layout=gen_combat.layout,  # type: ignore[attr-defined]
        size=gen_combat.size,  # type: ignore[attr-defined]
        difficult_terrain=gen_combat.difficult_terrain,  # type: ignore[attr-defined]
        hazard=gen_combat.hazard,  # type: ignore[attr-defined]
        rng=rng,
    )
    return Encounter(
        id=encounter_id,
        name=f"{monster_name} Encounter",
        battle_map=battle_map,
        monsters=monsters,
        party_spawn_points=PARTY_SPAWN_NAMES,
    )


def _build_continuation_schema(
    monster_choices: tuple[str, ...], skill_choices: tuple[str, ...]
) -> type[BaseModel]:
    """Same GeneratedCombatBeat/GeneratedSkillBeat shape as _build_schema's
    own nested classes (rebuilt fresh here too, for the same reason - the
    monster/skill Literal enums are dynamic per call) - the one real
    difference is GeneratedScene's own type enum gains "party_choice", the
    mechanism that lets a continuation itself end in another open choice
    (see generate_continuation's own next_scene_id wiring) rather than only
    ever the four types a whole-campaign generation produces."""
    monster_index_type = Literal[tuple(monster_choices)]  # type: ignore[valid-type]
    skill_type = Literal[tuple(skill_choices)]  # type: ignore[valid-type]

    class GeneratedCombatBeat(BaseModel):
        monster_index: monster_index_type  # type: ignore[valid-type]
        monster_count: int
        layout: Literal["open_room", "narrow_corridor", "two_rooms", "cluttered"] = "open_room"
        size: Literal["small", "medium", "large"] = "medium"
        difficult_terrain: Literal["none", "light", "heavy"] = "none"
        hazard: Literal["none", "light", "heavy"] = "none"

    class GeneratedSkillBeat(BaseModel):
        skill: skill_type  # type: ignore[valid-type]
        dc: int
        success_text: str
        failure_text: str

    class GeneratedContinuationScene(BaseModel):
        type: Literal["narrative_beat", "combat", "skill_challenge", "party_choice"]
        narrative_intro: str
        combat: GeneratedCombatBeat | None = None
        skill_challenge: GeneratedSkillBeat | None = None

    class GeneratedContinuation(BaseModel):
        scenes: list[GeneratedContinuationScene]

    return GeneratedContinuation


def _validate_continuation(generated: BaseModel, force_ending: bool) -> list[str]:
    """Same shape as _validate_generated - the cross-field rules a JSON
    schema enum can't express - plus two continuation-specific rules: a
    party_choice can only end the batch (there's no way to collect two
    simultaneous rounds of party input, and next_scene_id chaining assumes
    a single linear sequence) and there's at most one, and - when
    force_ending is set - there's none at all. force_ending's own prompt
    wording already asks the model not to end on a party_choice, but this
    project's own established stance is to make a hard constraint
    structurally enforced rather than trust a prompt alone (same reasoning
    as monster_index/skill being closed enums, not free text asked nicely
    to stay in bounds) - a session's generation cap (config.MAX_ADAPTIVE_
    GENERATIONS) has to actually hold, not just usually hold.

    Confirmed live: force_ending also needs its own, tighter scene-count
    rule (exactly 1, not the usual 1-3) - "bring the story to a genuine,
    satisfying conclusion" reliably reads as license to spend MORE scenes
    wrapping things up, not fewer, even with the same 1-3 limit restated
    right next to it (3 straight validation failures on the first live
    test of this path, all for exceeding the max). A single wrap-up scene
    is also simply the right shape for an ending - it's what a hand-
    authored campaign's own final scene already looks like."""
    problems: list[str] = []
    scenes = generated.scenes  # type: ignore[attr-defined]

    if force_ending:
        if len(scenes) != 1:
            problems.append(f"a final continuation must be exactly 1 scene, got {len(scenes)}")
    elif not (_CONTINUATION_MIN_SCENES <= len(scenes) <= _CONTINUATION_MAX_SCENES):
        problems.append(
            f"expected {_CONTINUATION_MIN_SCENES}-{_CONTINUATION_MAX_SCENES} scenes, "
            f"got {len(scenes)}"
        )

    for i, scene in enumerate(scenes):
        if scene.type == "combat" and scene.combat is None:
            problems.append(f"scene {i} is type=combat but is missing its combat payload")
        if scene.type == "skill_challenge" and scene.skill_challenge is None:
            problems.append(
                f"scene {i} is type=skill_challenge but is missing its skill_challenge payload"
            )
        if contains_cjk(scene.narrative_intro):
            problems.append(f"scene {i}'s narrative_intro contains non-English text")
        if scene.skill_challenge is not None and (
            contains_cjk(scene.skill_challenge.success_text)
            or contains_cjk(scene.skill_challenge.failure_text)
        ):
            problems.append(f"scene {i}'s skill_challenge text contains non-English text")

    party_choice_indices = [i for i, s in enumerate(scenes) if s.type == "party_choice"]
    if force_ending and party_choice_indices:
        problems.append("this continuation must end the story - no party_choice scene is allowed")
    elif len(party_choice_indices) > 1:
        problems.append("at most one party_choice scene is allowed per continuation")
    elif party_choice_indices and party_choice_indices[0] != len(scenes) - 1:
        problems.append("a party_choice scene must be the last scene in the continuation")

    return problems


def generate_continuation(
    situation: str,
    responses: dict[str, str],
    party: list[Character],
    campaign_id: str,
    generation_index: int,
    force_ending: bool,
    srd: SrdIndex | None = None,
    encounters_dir: Path = DEFAULT_ENCOUNTERS_DIR,
    rng: random.Random | None = None,
) -> list[Scene]:
    """Story-adaptive-encounters Phase 3: generates 1-3 new scenes live,
    continuing the story from a party_choice's own resolved situation and
    what the party actually said/chose to do - reuses generate_campaign's
    whole schema/validate-retry/encounter-building machinery (see
    _build_continuation_schema/_validate_continuation/_build_encounter),
    just seeded from a live moment's context instead of a whole-campaign
    premise, and without the size-based scene/combat-count rules that don't
    apply to answering one choice.

    Returns fresh Scene objects, chained to each other via next_scene_id in
    the order generated - does not mutate anything; the caller (api/ws/
    session.py's _resolve_party_choice) splices them into the live
    Campaign's own scene list and continues the chain walk from the first
    one. A generated combat scene's Encounter is written to disk under
    encounters_dir exactly like generate_campaign's own combat scenes are,
    since encounter.load_encounter only ever reads from disk - there's no
    in-memory-only encounter path to use instead.

    force_ending=True (the caller's own per-session generation cap, see
    config.MAX_ADAPTIVE_GENERATIONS) tells the model this must be the LAST
    continuation - no party_choice scene allowed, so the story reaches a
    real conclusion instead of a session being able to keep this looping
    indefinitely. The last scene's own next_scene_id is left unset either
    way: if it's type=party_choice, that's the same "open" marker this
    whole mechanism triggers off of, so the next time IT resolves, another
    continuation generates automatically - self-similar by construction, no
    special-casing needed here or in _resolve_party_choice."""
    srd = srd or load_srd()
    rng = rng or random.Random()
    monster_choices = tuple(m for m in _ALLOWED_MONSTERS if m in srd.monsters)
    skill_choices = tuple(sorted(srd.skills))
    schema = _build_continuation_schema(monster_choices, skill_choices)

    monster_list = "\n".join(
        f"- {idx}: {srd.monsters[idx]['name']} (CR {srd.monsters[idx]['challenge_rating']}, "
        f"{srd.monsters[idx]['hit_points']} HP)"
        for idx in monster_choices
    )
    names = {c.id: c.name for c in party}
    responses_summary = "\n".join(
        f"- {names.get(character_id, character_id)}: {text}"
        for character_id, text in responses.items()
    )
    # Confirmed live: asking for "a genuine, satisfying conclusion" within
    # the usual 1-3 scenes reliably reads as license to spend MORE scenes
    # wrapping things up, not fewer - 3 straight validation failures for
    # exceeding the max, even with the same numeric limit restated right
    # next to the ending instruction. Asking for exactly ONE scene removes
    # the ambiguity entirely - a single wrap-up beat is also simply the
    # right shape for an ending (a hand-authored campaign's own final scene
    # already looks like this), not a compromise.
    scene_count_text = (
        "exactly 1 scene: a short epilogue that brings the story to a close"
        if force_ending
        else f"{_CONTINUATION_MIN_SCENES}-{_CONTINUATION_MAX_SCENES} scenes continuing the story"
    )
    ending_guidance = (
        "This must be the FINAL continuation of the story - do not end with a "
        "party_choice scene. Resolve everything in this one scene; do not leave anything "
        "hanging for a scene that will never come."
        if force_ending
        else (
            "If the story reaches a natural resolution, end with a narrative_beat and no "
            "further party_choice scene. Otherwise, you may end your final scene with "
            'type="party_choice" to let the party decide what happens next.'
        )
    )
    base_prompt = load_prompt("party_choice_continuation").format(
        situation=situation,
        responses_summary=responses_summary,
        scene_count_text=scene_count_text,
        ending_guidance=ending_guidance,
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
        problems = _validate_continuation(generated, force_ending)
        if not problems:
            break
    else:
        raise GenerationError(
            f"Story continuation generation failed validation after {_MAX_ATTEMPTS} "
            f"attempts: {'; '.join(problems)}"
        )

    assert generated is not None
    gen_scenes = generated.scenes  # type: ignore[attr-defined]

    scenes: list[Scene] = []
    for i, gen_scene in enumerate(gen_scenes):
        scene_id = f"{campaign_id}__adaptive{generation_index}_{i + 1}"
        next_scene_id = (
            f"{campaign_id}__adaptive{generation_index}_{i + 2}"
            if i + 1 < len(gen_scenes)
            else None
        )

        encounter_ref: str | None = None
        skill_challenge_def: SkillChallengeDef | None = None
        if gen_scene.type == "combat":
            # Unlike generate_campaign's own scene_id ("scene_N", needing
            # campaign_id prefixed on for a globally-unique ref), scene_id
            # here already has campaign_id baked in (see above, for cross-
            # generation uniqueness within one campaign's scene list) - so
            # it's already a fine encounter_ref on its own; prefixing again
            # would just double it up (caught live: a real generated
            # encounter file landed as
            # "live_verify__live_verify__adaptive1_2.yaml").
            encounter_ref = scene_id
            encounter = _build_encounter(encounter_ref, gen_scene.combat, srd, rng)
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

    return scenes


def generate_campaign(
    campaign_id: str,
    size: CampaignSize,
    srd: SrdIndex | None = None,
    campaigns_dir: Path = DEFAULT_CAMPAIGNS_DIR,
    encounters_dir: Path = DEFAULT_ENCOUNTERS_DIR,
    rng: random.Random | None = None,
) -> Campaign:
    """Generates, validates, and saves a new campaign (plus one Encounter
    YAML per combat scene) under `campaigns_dir`/`encounters_dir` - same
    file layout and same pydantic models a hand-authored campaign uses, so
    `load_campaign(campaign_id)` afterward can't tell the difference.
    `rng` (this project's usual injected-randomness convention, so a test
    can pass a seeded one) only ever decides battle_map_templates' own
    texture scatter - never anything the LLM itself produces."""
    srd = srd or load_srd()
    rng = rng or random.Random()
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
            encounter = _build_encounter(encounter_ref, gen_scene.combat, srd, rng)
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
