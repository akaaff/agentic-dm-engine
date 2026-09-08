"""Day 25: the real intent-parser plugged into Day 24's generic distillation
toolkit as one `DistillationTask` instance - no changes to the toolkit
itself, exactly the property tests/llm/test_distillation_toolkit_toy_task.py
(Day 24) set out to prove.

`build_intent_parser_task`'s `generate_prompt` reuses the actual production
prompt-builder (`graph.nodes.intent_parser.build_intent_parser_prompt`)
against a randomized scenario + utterance, so every generated training
example's input is byte-identical in format to what the live game sends at
inference time - essential for a Day 26 fine-tuned model to behave like the
teacher it's distilled from once swapped in (Day 27). Utterances are drawn
from small per-category template banks (not teacher-generated) spanning
attack/skill_check/cast_spell/move/use_item/dodge/disengage/end_turn/
invalid - the same category split intent_parser.md itself documents -
kept deterministic and cheap rather than spending an extra live call per
example just to invent phrasing.
"""

from __future__ import annotations

import random

from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import AbilityScore, Character, GameState
from src.graph.nodes.intent_parser import build_intent_parser_prompt
from src.graph.state_schema import GraphState
from src.training.task_spec import DistillationTask

_PC_NAMES = ["Thorin", "Elrond", "Grom", "Silvana", "Fenwick", "Mira", "Cassian", "Pip"]
_MONSTER_NAMES = ["Goblin", "Wolf", "Kobold", "Bandit", "Skeleton", "Orc"]
_WEAPONS = ["sword", "longsword", "dagger", "battleaxe", "shortbow", "mace"]
_SPELLS = ["fireball", "magic missile", "firebolt", "guiding bolt"]
_ITEMS = ["healing potion", "torch", "rope", "antitoxin"]
_ABILITIES: tuple[AbilityScore, ...] = ("STR", "DEX", "CON", "INT", "WIS", "CHA")

_SKILL_UTTERANCES = {
    "stealth": "I try to sneak past unnoticed",
    "perception": "I look around carefully for anything unusual",
    "athletics": "I try to climb the wall",
    "persuasion": "I try to talk them out of fighting",
    "intimidation": "I try to intimidate them into backing down",
    "investigation": "I search the room for clues",
    "survival": "I try to track the creature's trail",
}

_INVALID_UTTERANCES = [
    "I'd like to order a large pepperoni pizza",
    "What's the weather like today?",
    "I check my phone for messages",
    "asdkjfh qwioeur",
    "I sing a song about my adventures so far",
]

_DODGE_UTTERANCES = ["I dodge incoming attacks", "I focus entirely on defense this turn"]
_DISENGAGE_UTTERANCES = ["I disengage and back away from combat", "I carefully withdraw"]
_END_TURN_UTTERANCES = ["I'm done", "I pass my turn", "Nothing else, end my turn"]

_CATEGORIES = (
    "attack",
    "skill_check",
    "cast_spell",
    "move",
    "use_item",
    "dodge",
    "disengage",
    "end_turn",
    "invalid",
)


def _random_character(character_id: str, is_pc: bool, rng: random.Random) -> Character:
    hp = rng.randint(4, 24)
    name = rng.choice(_PC_NAMES) if is_pc else rng.choice(_MONSTER_NAMES)
    return Character(
        id=character_id,
        name=name,
        is_pc=is_pc,
        hp=hp,
        max_hp=hp,
        ac=rng.randint(10, 18),
        position=Position(x=rng.randint(0, 8), y=rng.randint(0, 6)),
        stats={ability: rng.randint(8, 16) for ability in _ABILITIES},
        proficiency_bonus=2,
        speed=30,
        race="human" if is_pc else "monster",
        class_="fighter" if is_pc else "Monster",
        background="",
    )


def _random_scenario(rng: random.Random) -> GameState:
    """A minimal but valid GameState - just enough for build_intent_parser_
    prompt to read (characters, turn_order, current_turn) - not a real
    encounter (no battle_map/SRD stat blocks needed for prompt-building)."""
    characters: dict[str, Character] = {"actor": _random_character("actor", is_pc=True, rng=rng)}
    for i in range(rng.randint(0, 2)):
        cid = f"ally_{i + 1}"
        characters[cid] = _random_character(cid, is_pc=True, rng=rng)
    for i in range(rng.randint(1, 3)):
        cid = f"enemy_{i + 1}"
        characters[cid] = _random_character(cid, is_pc=False, rng=rng)

    turn_order = list(characters)
    rng.shuffle(turn_order)
    current_turn = turn_order.index("actor")

    return GameState(
        encounter_id="synthetic",
        characters=characters,
        turn_order=turn_order,
        current_turn=current_turn,
        round=rng.randint(1, 5),
    )


def _utterance_for(category: str, scenario: GameState, actor: Character, rng: random.Random) -> str:
    if category == "attack":
        enemies = [c for c in scenario.characters.values() if not c.is_pc]
        target = rng.choice(enemies)
        weapon = rng.choice(_WEAPONS)
        return rng.choice(
            [
                f"I attack {target.id} with my {weapon}",
                f"I swing my {weapon} at {target.id}",
                f"I strike {target.id}",
            ]
        )
    if category == "skill_check":
        return rng.choice(list(_SKILL_UTTERANCES.values()))
    if category == "cast_spell":
        enemies = [c for c in scenario.characters.values() if not c.is_pc]
        target = rng.choice(enemies)
        spell = rng.choice(_SPELLS)
        return f"I cast {spell} at {target.id}"
    if category == "move":
        dx, dy = rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)])
        dest = Position(x=actor.position.x + dx, y=actor.position.y + dy)
        return f"I move to ({dest.x}, {dest.y})"
    if category == "use_item":
        return f"I use my {rng.choice(_ITEMS)}"
    if category == "dodge":
        return rng.choice(_DODGE_UTTERANCES)
    if category == "disengage":
        return rng.choice(_DISENGAGE_UTTERANCES)
    if category == "end_turn":
        return rng.choice(_END_TURN_UTTERANCES)
    return rng.choice(_INVALID_UTTERANCES)  # "invalid"


def _generate_prompt(rng: random.Random) -> str:
    scenario = _random_scenario(rng)
    actor = scenario.characters[scenario.turn_order[scenario.current_turn]]
    category = rng.choice(_CATEGORIES)
    utterance = _utterance_for(category, scenario, actor, rng)

    state: GraphState = {
        "game_state": scenario,
        "raw_text": utterance,
        "parsed_action": None,
        "events_before": 0,
        "round_before": scenario.round,
        "narration": None,
        "scene_image_url": None,
    }
    return build_intent_parser_prompt(state)


def build_intent_parser_task(rng: random.Random | None = None) -> DistillationTask:
    rng = rng or random.Random()
    return DistillationTask(
        name="intent_parser",
        output_schema=ParsedAction,
        generate_prompt=lambda: _generate_prompt(rng),
    )
