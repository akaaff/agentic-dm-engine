"""CLI entrypoints for running an encounter.

`--scripted` drives a small hardcoded 2-PC-vs-2-goblin skirmish through a
fixed action list with zero LLM calls anywhere - the MVP gate proving the
engine (dice/rules/movement/turn order/victory detection/event log) is
trustworthy before any model or UI touches it. This is a purpose-built
compact scenario (not the full goblin_ambush campaign content, which
--autoplay exercises).

`--autoplay` (Day 15) plays the real goblin_ambush_oneshot campaign with two
AI companions and zero human input: companion turns go through the full
graph (player_agent -> intent_parser -> rules_engine -> narrator, all real
LLM calls), monster turns are driven by engine.monster_ai's deterministic
heuristic fed straight in as a pre-parsed action. Needs a live Ollama.
"""

from __future__ import annotations

import argparse
import random

from src.engine.actions import ParsedAction
from src.engine.campaign import load_campaign
from src.engine.character_creation import create_character
from src.engine.companions import build_companion, load_companion_spec
from src.engine.encounter import Encounter, MonsterSpawn, build_encounter_state, load_encounter
from src.engine.monster_ai import choose_monster_action
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action
from src.graph.graph_builder import build_graph
from src.graph.nodes.judge import judge_transcript
from src.graph.state_schema import GraphState

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


def run_autoplay(verbose: bool = True) -> tuple[GameState, list[str]]:
    """Day 15's verify gate: full autoplay of the one-shot campaign with two
    AI companions and zero human input. Returns the final GameState and the
    full narration log (for judge_transcript). Real LLM calls throughout -
    not for the default offline test suite, see tests/llm/test_autoplay.py.
    """
    campaign = load_campaign("goblin_ambush_oneshot")
    # Walk the scene chain to the first combat scene - the one-shot's intro
    # is a narrative_beat with no encounter of its own. Multi-scene chaining
    # (narrating the intro/outro beats too) is Day 22's job; this just needs
    # a real encounter to autoplay.
    scene = campaign.first_scene()
    while scene.encounter_ref is None:
        if scene.next_scene_id is None:
            raise TurnEngineError(f"Campaign {campaign.id!r} has no combat scene to autoplay")
        scene = campaign.scene_by_id(scene.next_scene_id)
    encounter = load_encounter(scene.encounter_ref)

    srd = load_srd()
    party = [
        build_companion(load_companion_spec("grom_ironfist"), srd=srd),
        build_companion(load_companion_spec("silvana_wren"), srd=srd),
    ]

    rng = random.Random(42)
    state = build_encounter_state(encounter, party, rng, srd=srd)
    graph = build_graph(rng=rng, srd=srd)

    if verbose:
        print(f"Turn order: {state.turn_order}")

    narration_log: list[str] = []
    # Safety valve against a genuinely stuck engine, not a tight bound on a
    # "normal" combat: a companion's turn that repeatedly fails to parse
    # into a concrete action (rare even with the Day 15 prompt fix, but not
    # eliminated) burns several turns per round via the consecutive_invalid
    # circuit breaker without landing an attack, which can legitimately
    # stretch a slow, unlucky combat well past a tight ceiling - seen live
    # in tests/llm/test_autoplay.py (see CLAUDE.md). Generous on purpose.
    max_turns = 400
    turns = 0
    consecutive_invalid = 0
    while state.status == "in_progress" and turns < max_turns:
        turns += 1
        actor = state.characters[state.turn_order[state.current_turn]]

        if not actor.is_pc:
            # Monsters never reach player_agent/intent_parser at all - the
            # heuristic action is pre-supplied, the same bypass every
            # scripted test since Day 7 relies on.
            parsed_action = choose_monster_action(state, actor)
        elif consecutive_invalid >= 3:
            # Circuit breaker, found live: "invalid" deliberately doesn't
            # cost a turn (Day 14), which is right for a human who can just
            # try again, but a companion's persona-driven free text can loop
            # on pure flavor with no mechanical content forever (a cautious
            # "stays alert, watches the shadows" persona hit this on the
            # very first live autoplay run) - there's no human to unstick
            # it, so force the turn to end after repeated failures.
            parsed_action = ParsedAction(
                actor=actor.id,
                verb="end_turn",
                raw_text="(forced end_turn after repeated invalid actions)",
            )
        else:
            # Empty raw_text/parsed_action so player_agent_node generates
            # the companion's turn via the LLM.
            parsed_action = None

        events_before = len(state.events)
        round_before = state.round
        graph_input: GraphState = {
            "game_state": state,
            "raw_text": "",
            "parsed_action": parsed_action,
            "events_before": events_before,
            "round_before": round_before,
            "narration": None,
            "scene_image_url": None,
        }
        try:
            result = graph.invoke(graph_input)
        except (TurnEngineError, NotImplementedError) as exc:
            # Same rationale as api/ws/session.py's identical catch for
            # human free text (Day 12): resolve_action validates before
            # mutating state, so it's safe to just treat this as a failed
            # attempt and retry, rather than crashing the whole run. Found
            # live: a companion's LLM-declared move can name a destination
            # more than one square away, which intent_parser's own prompt
            # says to reject as "invalid" but doesn't always - the retry
            # circuit breaker below still applies since state didn't change.
            if verbose:
                print(f"[{actor.id}] (action failed: {exc})")
            consecutive_invalid += 1
            continue

        state = result["game_state"]
        narration = result["narration"] or ""
        if narration:
            narration_log.append(narration)
        if verbose:
            print(f"[{actor.id}] {narration}")
            if result["scene_image_url"]:
                print(f"  [scene image] {result['scene_image_url']}")

        new_events = state.events[events_before:]
        if len(new_events) == 1 and new_events[0].type == "action_invalid":
            consecutive_invalid += 1
        else:
            consecutive_invalid = 0

        for character in state.characters.values():
            assert character.hp >= 0, f"invariant violated: {character.id} has negative HP"

    if turns >= max_turns:
        raise RuntimeError("Autoplay did not terminate within max_turns - possible engine bug")

    if verbose:
        print(f"\n=== Autoplay ended: {state.status} (round {state.round}, {turns} turns) ===")
        for cid, character in state.characters.items():
            print(f"  {cid}: hp={character.hp}/{character.max_hp} pos={character.position}")

        judged = judge_transcript(narration_log)
        print(f"\n=== Judge scores: {judged} ===")

    return state, narration_log


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument("--autoplay", action="store_true")
    args = parser.parse_args()

    if args.scripted:
        run_scripted()
    elif args.autoplay:
        run_autoplay()
    else:
        parser.error("one of --scripted or --autoplay is required")


if __name__ == "__main__":
    main()
