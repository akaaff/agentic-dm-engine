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

_PC_NAMES = [
    "Thorin",
    "Elrond",
    "Grom",
    "Silvana",
    "Fenwick",
    "Mira",
    "Cassian",
    "Pip",
    "Orin",
    "Bryn",
    "Kael",
    "Ysolde",
    "Dain",
    "Rowan",
    "Teodora",
    "Aldric",
]
_MONSTER_NAMES = [
    "Goblin",
    "Wolf",
    "Kobold",
    "Bandit",
    "Skeleton",
    "Orc",
    "Zombie",
    "Cultist",
    "Bugbear",
    "Hobgoblin",
    "Giant Rat",
    "Ogre",
]
_WEAPONS = [
    "sword",
    "longsword",
    "dagger",
    "battleaxe",
    "shortbow",
    "mace",
    "warhammer",
    "rapier",
    "spear",
    "handaxe",
]
_SPELLS = [
    "fireball",
    "magic missile",
    "firebolt",
    "guiding bolt",
    "eldritch blast",
    "scorching ray",
    "ray of frost",
    "sacred flame",
]
_ITEMS = [
    "healing potion",
    "torch",
    "rope",
    "antitoxin",
    "smoke bomb",
    "grappling hook",
    "scroll",
    "elixir",
]
_ABILITIES: tuple[AbilityScore, ...] = ("STR", "DEX", "CON", "INT", "WIS", "CHA")

_SKILL_UTTERANCES = {
    "stealth": [
        "I try to sneak past unnoticed",
        "I move quietly, staying to the shadows",
        "I attempt to slip by without being seen",
        "I creep forward as quietly as I can",
        "I try to stay hidden while I move",
    ],
    "perception": [
        "I look around carefully for anything unusual",
        "I scan the area for hidden dangers",
        "I listen closely for any sound of movement",
        "I take a careful look around the room",
        "I check for anything out of place",
    ],
    "athletics": [
        "I try to climb the wall",
        "I attempt to force the door open",
        "I try to leap across the gap",
        "I try to push the boulder out of the way",
        "I attempt to swim against the current",
    ],
    "persuasion": [
        "I try to talk them out of fighting",
        "I attempt to convince them to let us pass",
        "I try to reason with them calmly",
        "I plead with them to reconsider",
        "I try to strike a deal instead of fighting",
    ],
    "intimidation": [
        "I try to intimidate them into backing down",
        "I threaten them to make them back off",
        "I try to scare them off with a show of force",
        "I glare menacingly and demand they leave",
        "I try to bully them into surrendering",
    ],
    "investigation": [
        "I search the room for clues",
        "I examine the strange markings on the wall",
        "I look for any hidden mechanisms",
        "I try to piece together what happened here",
        "I search the body for anything useful",
    ],
    "survival": [
        "I try to track the creature's trail",
        "I look for signs of which way they went",
        "I try to find a safe path through the wilderness",
        "I attempt to forage for something edible",
        "I try to read the tracks in the mud",
    ],
}

_INVALID_UTTERANCES = [
    "I'd like to order a large pepperoni pizza",
    "What's the weather like today?",
    "I check my phone for messages",
    "asdkjfh qwioeur",
    "I sing a song about my adventures so far",
    "Can you recommend a good restaurant nearby?",
    "I wonder what's for dinner tonight",
    "Let's talk about something else entirely",
    "I start telling a joke to lighten the mood",
    "blahblahblah nonsense words here",
]

_DODGE_UTTERANCES = [
    "I dodge incoming attacks",
    "I focus entirely on defense this turn",
    "I stay light on my feet, ready to evade",
    "I keep my guard up and watch for attacks",
    "I brace myself and try to avoid getting hit",
    "I duck and weave, avoiding anything thrown my way",
]
_DISENGAGE_UTTERANCES = [
    "I disengage and back away from combat",
    "I carefully withdraw",
    "I step back out of melee range",
    "I retreat without turning my back",
    "I pull back from the fight cautiously",
    "I break off and put some distance between us",
]
_END_TURN_UTTERANCES = [
    "I'm done",
    "I pass my turn",
    "Nothing else, end my turn",
    "That's all for me this turn",
    "I have nothing more to do right now",
    "I'll hold here, end my turn",
]

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
    encounter (no battle_map/SRD stat blocks needed for prompt-building).

    The acting character's id is a lowercased name (e.g. "thorin"), not the
    literal word "actor" - found live (Day 25) that the generic placeholder
    id confused the teacher into echoing back a different id ~49% of the
    time (it would invent "actor_0"/"actor_1" or substitute another visible
    character's id), which resolve_action's own actor-mismatch check would
    reject as "It is X's turn, not Y's". Not a production bug - the real
    game never names a character literally "actor" - but bad training
    signal for a field the model otherwise gets right whenever the id looks
    like an actual name."""
    actor_id = rng.choice(_PC_NAMES).lower()
    characters: dict[str, Character] = {actor_id: _random_character(actor_id, is_pc=True, rng=rng)}
    for i in range(rng.randint(0, 2)):
        cid = f"ally_{i + 1}"
        characters[cid] = _random_character(cid, is_pc=True, rng=rng)
    for i in range(rng.randint(1, 3)):
        cid = f"enemy_{i + 1}"
        characters[cid] = _random_character(cid, is_pc=False, rng=rng)

    turn_order = list(characters)
    rng.shuffle(turn_order)
    current_turn = turn_order.index(actor_id)

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
                f"I lunge at {target.id} with my {weapon}",
                f"I go after {target.id}",
                f"I hit {target.id} with everything I've got",
                f"I take a swing at {target.id}",
                f"I charge {target.id} and attack",
                f"Time to finish {target.id} off with my {weapon}",
                f"I bring my {weapon} down on {target.id}",
                f"I attack {target.id}",
                f"I aim my {weapon} at {target.id} and strike",
            ]
        )
    if category == "skill_check":
        phrasings = rng.choice(list(_SKILL_UTTERANCES.values()))
        return rng.choice(phrasings)
    if category == "cast_spell":
        enemies = [c for c in scenario.characters.values() if not c.is_pc]
        target = rng.choice(enemies)
        spell = rng.choice(_SPELLS)
        return rng.choice(
            [
                f"I cast {spell} at {target.id}",
                f"I hurl a {spell} at {target.id}",
                f"I unleash {spell} on {target.id}",
                f"I channel {spell} toward {target.id}",
                f"I cast {spell}, targeting {target.id}",
                f"Time to use {spell} on {target.id}",
            ]
        )
    if category == "move":
        dx, dy = rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)])
        dest = Position(x=actor.position.x + dx, y=actor.position.y + dy)
        return rng.choice(
            [
                f"I move to ({dest.x}, {dest.y})",
                f"I step over to ({dest.x}, {dest.y})",
                f"I shift my position to ({dest.x}, {dest.y})",
                f"I head toward ({dest.x}, {dest.y})",
                f"I reposition to ({dest.x}, {dest.y})",
                f"I carefully move to ({dest.x}, {dest.y})",
            ]
        )
    if category == "use_item":
        item = rng.choice(_ITEMS)
        phrasings = [
            f"I use my {item}",
            f"I reach for my {item} and use it",
            f"I quickly use my {item}",
            f"I pull out my {item} and use it",
            f"Time to use my {item}",
        ]
        if "potion" in item or "elixir" in item:
            phrasings.append(f"I drink my {item}")
        return rng.choice(phrasings)
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
