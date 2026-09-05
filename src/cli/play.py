"""CLI entrypoints for running an encounter.

`--scripted` drives a small hardcoded 2-PC-vs-2-goblin skirmish through a
fixed action list with zero LLM calls anywhere - the MVP gate proving the
engine (dice/rules/movement/turn order/victory detection/event log) is
trustworthy before any model or UI touches it. This is a purpose-built
compact scenario (not the full goblin_ambush campaign content, which Day 8's
--autoplay exercises) sized so every roll and consequence can be hand-traced.
"""

from __future__ import annotations

import argparse

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import Encounter, MonsterSpawn, build_encounter_state
from src.engine.position import BattleMap, Position
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action

# 5x3 grid: a single difficult-terrain square between the two starting sides.
#   y=0: floor floor    floor floor floor
#   y=1: floor difficult floor floor floor
#   y=2: floor floor    floor floor floor
_DEMO_TERRAIN = [
    ["floor", "floor", "floor", "floor", "floor"],
    ["floor", "difficult", "floor", "floor", "floor"],
    ["floor", "floor", "floor", "floor", "floor"],
]


def build_demo_encounter() -> Encounter:
    return Encounter(
        id="demo_skirmish",
        name="Demo Skirmish",
        battle_map=BattleMap(
            width=5,
            height=3,
            terrain=_DEMO_TERRAIN,  # type: ignore[arg-type]
            spawn_points={
                "party_1": Position(x=0, y=1),
                "party_2": Position(x=1, y=2),
                "goblin_1": Position(x=2, y=1),
                "goblin_2": Position(x=2, y=2),
            },
        ),
        monsters=[
            MonsterSpawn(monster_index="goblin", character_id="goblin_1", spawn_point="goblin_1"),
            MonsterSpawn(monster_index="goblin", character_id="goblin_2", spawn_point="goblin_2"),
        ],
        party_spawn_points=["party_1", "party_2"],
    )


def build_demo_party() -> list[Character]:
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    elrond = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


class _FixedRandom:
    """random.Random stand-in returning a preset queue of values - used to
    make every attack/damage roll in the demo script deterministic and
    hand-verifiable, the same pattern used throughout this project's tests."""

    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def demo_initiative_rng() -> _FixedRandom:
    """A fixed queue producing, in dict-insertion order (thorin, elrond,
    goblin_1, goblin_2), a clean initiative order with no ties:
    thorin 18(+2)=20, elrond 10(+3)=13, goblin_1 8(+2)=10, goblin_2 3(+2)=5."""
    return _FixedRandom([18, 10, 8, 3])


def demo_action_rng() -> _FixedRandom:
    """Every d20/damage roll consumed by SCRIPTED_ACTIONS below, in order.
    See tests/unit/test_turn_engine_scripted.py for the full hand-computed
    trace this queue produces."""
    return _FixedRandom([16, 3, 11, 4, 2, 14, 5, 5, 10, 3, 13, 1])


SCRIPTED_ACTIONS: list[ParsedAction] = [
    # Round 1
    ParsedAction(
        actor="thorin",
        verb="move",
        params={"path": [{"x": 1, "y": 1}]},
        raw_text="Thorin advances toward the nearest goblin.",
    ),
    ParsedAction(
        actor="elrond",
        verb="attack",
        target="goblin_2",
        item_or_spell="dagger",
        raw_text="Elrond stabs the goblin in front of him.",
    ),
    ParsedAction(
        actor="goblin_1",
        verb="attack",
        target="thorin",
        raw_text="The goblin slashes at Thorin.",
    ),
    ParsedAction(
        actor="goblin_2",
        verb="attack",
        target="elrond",
        raw_text="The wounded goblin strikes back at Elrond.",
    ),
    # Round 2
    ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="Thorin swings his longsword at the goblin.",
    ),
    ParsedAction(
        actor="elrond",
        verb="attack",
        target="goblin_2",
        item_or_spell="dagger",
        raw_text="Elrond stabs again.",
    ),
    # goblin_1's round-2 turn is skipped by the engine - it died on Thorin's turn above.
    ParsedAction(
        actor="goblin_2",
        verb="attack",
        target="elrond",
        raw_text="The goblin strikes back.",
    ),
    # Round 3
    ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_2",
        item_or_spell="longsword",
        raw_text="Thorin finishes off the last goblin.",
    ),
]


def run_scripted(verbose: bool = True) -> GameState:
    encounter = build_demo_encounter()
    party = build_demo_party()
    state = build_encounter_state(encounter, party, demo_initiative_rng())  # type: ignore[arg-type]
    action_rng = demo_action_rng()

    if verbose:
        print(f"Turn order: {state.turn_order}")

    for action in SCRIPTED_ACTIONS:
        if state.status != "in_progress":
            break
        current_actor = state.turn_order[state.current_turn]
        if action.actor != current_actor:
            raise TurnEngineError(
                f"Script out of sync: expected {current_actor}'s turn, script has {action.actor}"
            )
        round_before = state.round
        events_before = len(state.events)
        resolve_action(state, action, action_rng)  # type: ignore[arg-type]
        if verbose:
            print(f"\n[Round {round_before}] {action.raw_text}")
            for event in state.events[events_before:]:
                print(f"  {event.type}: {event.payload}")

    if verbose:
        print(f"\n=== Combat ended: {state.status} (round {state.round}) ===")
        for cid, character in state.characters.items():
            print(f"  {cid}: hp={character.hp}/{character.max_hp} pos={character.position}")

    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scripted", action="store_true")
    args = parser.parse_args()

    if args.scripted:
        run_scripted()
    else:
        parser.error("only --scripted is supported so far")


if __name__ == "__main__":
    main()
