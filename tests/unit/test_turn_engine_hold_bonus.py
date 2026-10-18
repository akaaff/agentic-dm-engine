"""A human player's turn stays open after their main action while a bonus action is still
usable (live-reported: the turn passed automatically with the bonus action unspent).

Held only when the caller asks (`hold_for_bonus_action`, set for real player actions), so
scripted, AI and monster turns keep ending the moment their action does."""

from __future__ import annotations

import random

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, bonus_action_options, resolve_action
from src.graph.nodes.rules_engine_node import make_rules_engine_node


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _fighter() -> Character:
    return create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )


def _wizard() -> Character:
    return create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "mage-armor", "sleep"],
        chosen_equipment=["dagger"],
    )


def _rogue() -> Character:
    rogue = create_character(
        character_id="finn",
        name="Finn",
        race_index="human",
        class_index="rogue",
        background_index="acolyte",
        base_ability_scores={"STR": 10, "DEX": 15, "CON": 13, "INT": 12, "WIS": 14, "CHA": 8},
        chosen_skills=["skill-stealth", "skill-acrobatics", "skill-perception", "skill-deception"],
        chosen_equipment=["dagger"],
        chosen_expertise=["skill-stealth", "skill-acrobatics"],
    )
    return rogue


def _state(first: Character, hurt: bool = True) -> GameState:
    """`first` acts first with a goblin next to them; goblins have plenty of HP."""
    other = _fighter() if first.id != "thorin" else _wizard()
    state = build_encounter_state(
        build_demo_encounter(),
        [first, other],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )
    for goblin in ("goblin_1", "goblin_2"):
        state.characters[goblin].hp = state.characters[goblin].max_hp = 200
    state.characters["goblin_1"].position = Position(x=first.position.x + 1, y=first.position.y)
    if hurt:
        first.hp = max(1, first.max_hp - 3)
    return state


def _act(state: GameState, actor: str, verb: str, hold: bool = True, **fields: object) -> None:
    action = ParsedAction(actor=actor, verb=verb, raw_text="x", **fields)  # type: ignore[arg-type]
    resolve_action(
        state,
        action,
        _FixedRandom([15, 5, 4]),  # type: ignore[arg-type]
        hold_for_bonus_action=hold,
    )


def _current(state: GameState) -> str:
    return state.turn_order[state.current_turn]


def test_a_hurt_fighters_turn_is_held_after_their_attack_for_second_wind() -> None:
    state = _state(_fighter())
    _act(state, "thorin", "attack", target="goblin_1", item_or_spell="longsword")
    thorin = state.characters["thorin"]
    assert _current(state) == "thorin"
    assert thorin.turn_held_for_bonus and thorin.action_used_this_turn
    assert bonus_action_options(thorin, load_srd()) == ["Second Wind"]


def test_without_the_flag_the_turn_ends_as_it_always_did() -> None:
    state = _state(_fighter())
    _act(state, "thorin", "attack", hold=False, target="goblin_1", item_or_spell="longsword")
    assert _current(state) != "thorin"
    assert not state.characters["thorin"].turn_held_for_bonus


def test_nothing_is_held_when_there_is_no_bonus_action_worth_using() -> None:
    state = _state(_fighter(), hurt=False)  # full HP: Second Wind would do nothing
    _act(state, "thorin", "attack", target="goblin_1", item_or_spell="longsword")
    assert _current(state) != "thorin"


def test_a_held_turn_refuses_a_second_main_action() -> None:
    state = _state(_fighter())
    _act(state, "thorin", "attack", target="goblin_1", item_or_spell="longsword")
    with pytest.raises(TurnEngineError, match="already used their action"):
        _act(state, "thorin", "attack", target="goblin_1", item_or_spell="longsword")
    with pytest.raises(TurnEngineError, match="already used their action"):
        _act(state, "thorin", "dash", params={"path": []})
    assert _current(state) == "thorin"


def test_the_bonus_action_can_still_be_used_and_then_the_turn_ends_by_itself() -> None:
    state = _state(_fighter())
    hp_before = state.characters["thorin"].hp
    _act(state, "thorin", "attack", target="goblin_1", item_or_spell="longsword")
    _act(state, "thorin", "second_wind")
    assert state.characters["thorin"].hp > hp_before
    assert _current(state) != "thorin"  # nothing left to do: the turn passed


def test_the_flag_is_cleared_when_the_turn_passes() -> None:
    state = _state(_fighter())
    _act(state, "thorin", "attack", target="goblin_1", item_or_spell="longsword")
    _act(state, "thorin", "end_turn")
    assert _current(state) != "thorin"
    assert state.characters["thorin"].turn_held_for_bonus is False


def test_a_player_can_choose_to_skip_the_bonus_action_by_ending_the_turn() -> None:
    state = _state(_fighter())
    _act(state, "thorin", "attack", target="goblin_1", item_or_spell="longsword")
    hp = state.characters["thorin"].hp
    _act(state, "thorin", "end_turn")
    assert state.characters["thorin"].hp == hp  # Second Wind was never used
    assert _current(state) != "thorin"


def test_movement_is_still_allowed_on_a_held_turn() -> None:
    state = _state(_fighter())
    _act(state, "thorin", "attack", target="goblin_1", item_or_spell="longsword")
    start = state.characters["thorin"].position
    _act(state, "thorin", "move", params={"path": [{"x": start.x, "y": start.y + 1}]})
    assert _current(state) == "thorin"
    assert state.characters["thorin"].position.y == start.y + 1


def test_a_bonus_action_spell_is_a_hold_reason_and_a_normal_cast_is_refused_while_held() -> None:
    wizard = _wizard()
    wizard.prepared_spells.append("misty-step")
    wizard.spell_slots = {1: 2, 2: 1}
    state = _state(wizard, hurt=False)
    assert "Misty Step" in bonus_action_options(wizard, load_srd())
    _act(state, "elrond", "attack", target="goblin_1", item_or_spell="dagger")
    assert _current(state) == "elrond"
    with pytest.raises(TurnEngineError, match="only a bonus-action spell"):
        _act(state, "elrond", "cast_spell", item_or_spell="Magic Missile", target="goblin_1")
    _act(
        state,
        "elrond",
        "cast_spell",
        item_or_spell="Misty Step",
        params={"to": {"x": 0, "y": 0}},
    )
    assert _current(state) != "elrond"  # the bonus action was the last thing left


def test_a_dual_wielder_keeps_the_turn_for_the_off_hand_attack() -> None:
    rogue = _rogue()
    rogue.equipped_weapons = ["dagger", "dagger"]
    state = _state(rogue, hurt=False)
    _act(state, "finn", "attack", target="goblin_1", item_or_spell="dagger")
    assert _current(state) == "finn"
    assert "off-hand attack" in bonus_action_options(state.characters["finn"], load_srd())
    _act(state, "finn", "offhand_attack", target="goblin_1")
    assert _current(state) != "finn"


def test_the_off_hand_first_then_the_main_attack_also_works() -> None:
    rogue = _rogue()
    rogue.equipped_weapons = ["dagger", "dagger"]
    state = _state(rogue, hurt=False)
    _act(state, "finn", "offhand_attack", target="goblin_1")
    assert _current(state) == "finn"  # a bonus action leaves the turn open
    _act(state, "finn", "attack", target="goblin_1", item_or_spell="dagger")
    assert _current(state) != "finn"  # the bonus action is spent, so nothing to hold for


def test_a_downed_or_dead_actor_is_never_held() -> None:
    fighter = _fighter()
    fighter.hp = 0
    assert bonus_action_options(fighter, load_srd()) == []


def test_only_human_players_are_held_by_the_rules_engine_node() -> None:
    srd = load_srd()
    node = make_rules_engine_node(random.Random(1), srd)

    def run(companion: bool, flag: bool) -> GameState:
        fighter = _fighter()
        fighter.is_companion = companion
        state = _state(fighter)
        graph_state = {
            "game_state": state,
            "raw_text": "",
            "parsed_action": ParsedAction(
                actor="thorin",
                verb="attack",
                target="goblin_1",
                item_or_spell="longsword",
                raw_text="x",
            ),
            "events_before": len(state.events),
            "round_before": 1,
            "narration": None,
            "scene_image_url": None,
            **({"hold_turn_for_bonus": True} if flag else {}),
        }
        node(graph_state)  # type: ignore[arg-type]
        return state

    assert _current(run(companion=False, flag=True)) == "thorin"  # a human, asked to hold
    assert _current(run(companion=True, flag=True)) != "thorin"  # a companion plays on
    assert _current(run(companion=False, flag=False)) != "thorin"  # no flag: as before
