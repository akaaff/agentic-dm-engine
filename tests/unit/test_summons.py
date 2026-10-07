"""Issue #56, phase A: adding a creature to - and removing it from - a fight that is
already running, and what makes a summoned creature different from a party member.

The turn-order index arithmetic is the part that is easy to get wrong by one, so each
case is a small hand-built order with the expected `current_turn` written out."""

from __future__ import annotations

import random

import pytest

from src.engine.actions import ParsedAction
from src.engine.encounter import monster_to_character
from src.engine.monster_ai import choose_monster_action
from src.engine.position import BattleMap, Position
from src.engine.rules import is_party_member
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.summons import (
    SummonError,
    add_combatant,
    dismiss_summons,
    find_open_square,
    remove_combatant,
)
from src.engine.turn_engine import _end_concentration, resolve_action


def _person(char_id: str, *, is_pc: bool, x: int = 0, y: int = 0) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=is_pc,
        hp=20,
        max_hp=20,
        ac=12,
        position=Position(x=x, y=y),
        stats={"STR": 12, "DEX": 12, "CON": 12, "INT": 12, "WIS": 12, "CHA": 12},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Wizard",
        background="",
    )


def _wolf(char_id: str, x: int = 0, y: int = 0) -> Character:
    return monster_to_character(load_srd().monsters["wolf"], char_id, Position(x=x, y=y))


def _state(order: list[str], current: int = 0, *, width: int = 8) -> GameState:
    people = {
        "mira": _person("mira", is_pc=True, x=0, y=0),
        "thorin": _person("thorin", is_pc=True, x=1, y=0),
        "goblin_1": _person("goblin_1", is_pc=False, x=6, y=0),
        "goblin_2": _person("goblin_2", is_pc=False, x=7, y=0),
    }
    return GameState(
        encounter_id="summon_test",
        characters={cid: people[cid] for cid in order},
        turn_order=list(order),
        current_turn=current,
        round=3,
        battle_map=BattleMap(
            width=width, height=3, terrain=[["floor"] * width for _ in range(3)], spawn_points={}
        ),
    )


def test_a_summon_acts_right_after_its_summoner() -> None:
    state = _state(["goblin_1", "mira", "thorin", "goblin_2"], current=1)
    add_combatant(state, _wolf("wolf_a", 2, 0), state.characters["mira"], "Conjure Animals")
    assert state.turn_order == ["goblin_1", "mira", "wolf_a", "thorin", "goblin_2"]
    assert state.current_turn == 1  # still mira's turn


def test_a_batch_keeps_the_order_it_was_added_in() -> None:
    state = _state(["mira", "thorin", "goblin_1", "goblin_2"], current=0)
    mira = state.characters["mira"]
    for name in ("wolf_a", "wolf_b", "wolf_c"):
        add_combatant(state, _wolf(name), mira, "Conjure Animals")
    assert state.turn_order == [
        "mira",
        "wolf_a",
        "wolf_b",
        "wolf_c",
        "thorin",
        "goblin_1",
        "goblin_2",
    ]


def test_summoning_when_the_summoner_is_earlier_in_the_order_shifts_the_current_turn() -> None:
    state = _state(["mira", "thorin", "goblin_1", "goblin_2"], current=2)  # goblin_1 acting
    add_combatant(state, _wolf("wolf_a"), state.characters["mira"], "Conjure Animals")
    assert state.turn_order == ["mira", "wolf_a", "thorin", "goblin_1", "goblin_2"]
    assert state.turn_order[state.current_turn] == "goblin_1"


def test_a_summon_inherits_its_summoners_side_and_is_stamped() -> None:
    state = _state(["mira", "goblin_1"], current=0)
    wolf = _wolf("wolf_a")
    assert wolf.is_pc is False
    add_combatant(state, wolf, state.characters["mira"], "Conjure Animals")
    assert wolf.is_pc is True
    assert (wolf.summoned_by, wolf.summon_spell) == ("mira", "Conjure Animals")
    assert not is_party_member(wolf)
    assert is_party_member(state.characters["mira"])
    assert any(e.type == "summoned" and e.payload["summoned"] == "wolf_a" for e in state.events)


def test_a_monsters_summon_stays_on_the_monsters_side() -> None:
    state = _state(["mira", "goblin_1"], current=1)
    wolf = _wolf("wolf_a")
    add_combatant(state, wolf, state.characters["goblin_1"], "Animate Dead")
    assert wolf.is_pc is False and wolf.summoned_by == "goblin_1"


def test_adding_a_duplicate_id_or_an_unknown_summoner_is_rejected() -> None:
    state = _state(["mira", "goblin_1"], current=0)
    mira = state.characters["mira"]
    add_combatant(state, _wolf("wolf_a"), mira, "Conjure Animals")
    with pytest.raises(SummonError, match="already in this encounter"):
        add_combatant(state, _wolf("wolf_a"), mira, "Conjure Animals")
    with pytest.raises(SummonError, match="not in the turn order"):
        add_combatant(state, _wolf("wolf_b"), _person("ghost", is_pc=True), "Conjure Animals")


def test_removing_a_creature_before_the_current_actor_keeps_the_same_actor() -> None:
    state = _state(["mira", "thorin", "goblin_1", "goblin_2"], current=3)
    remove_combatant(state, "thorin")
    assert state.turn_order == ["mira", "goblin_1", "goblin_2"]
    assert state.turn_order[state.current_turn] == "goblin_2"
    assert "thorin" not in state.characters


def test_removing_a_creature_after_the_current_actor_changes_nothing_else() -> None:
    state = _state(["mira", "thorin", "goblin_1", "goblin_2"], current=1)
    remove_combatant(state, "goblin_2")
    assert state.turn_order == ["mira", "thorin", "goblin_1"]
    assert state.current_turn == 1


def test_removing_the_acting_creature_lets_the_next_advance_land_on_whoever_followed() -> None:
    state = _state(["mira", "thorin", "goblin_1", "goblin_2"], current=2)
    remove_combatant(state, "goblin_1")
    # park on thorin; advancing one slot lands on goblin_2, who was next
    assert state.turn_order[state.current_turn] == "thorin"
    assert state.turn_order[(state.current_turn + 1) % len(state.turn_order)] == "goblin_2"
    assert state.round == 3


def test_removing_the_first_creature_when_it_is_acting_does_not_count_a_round_twice() -> None:
    state = _state(["mira", "thorin", "goblin_1"], current=0)
    remove_combatant(state, "mira")
    # Parked on the last slot with the round given back: the next advance wraps to slot 0
    # and is the round's real start, so the round number ends up unchanged.
    assert state.turn_order == ["thorin", "goblin_1"]
    assert (state.current_turn, state.round) == (1, 2)


def test_removing_an_unknown_id_is_harmless() -> None:
    state = _state(["mira", "goblin_1"], current=0)
    remove_combatant(state, "nobody")
    assert state.turn_order == ["mira", "goblin_1"]


def test_dismissing_a_spells_summons_leaves_everyone_else() -> None:
    state = _state(["mira", "thorin", "goblin_1"], current=0)
    mira, thorin = state.characters["mira"], state.characters["thorin"]
    add_combatant(state, _wolf("wolf_a"), mira, "Conjure Animals")
    add_combatant(state, _wolf("wolf_b"), mira, "Conjure Animals")
    add_combatant(state, _wolf("wolf_c"), thorin, "Find Familiar")
    gone = dismiss_summons(state, "mira", "Conjure Animals")
    assert gone == ["wolf_a", "wolf_b"]
    assert "wolf_c" in state.characters and "wolf_a" not in state.characters
    assert state.turn_order == ["mira", "thorin", "wolf_c", "goblin_1"]
    assert sum(e.type == "summon_ended" for e in state.events) == 2


def test_losing_concentration_dismisses_what_it_conjured() -> None:
    state = _state(["mira", "thorin", "goblin_1"], current=0)
    mira = state.characters["mira"]
    mira.concentrating_on = "Conjure Animals"
    add_combatant(state, _wolf("wolf_a"), mira, "Conjure Animals")
    _end_concentration(state, mira, load_srd())
    assert "wolf_a" not in state.characters
    assert "wolf_a" not in state.turn_order
    assert mira.concentrating_on is None


def test_a_summoned_creature_dies_outright_at_zero_hp_with_no_death_saves() -> None:
    state = _state(["mira", "goblin_1"], current=1)
    wolf = _wolf("wolf_a", 5, 0)
    add_combatant(state, wolf, state.characters["mira"], "Conjure Animals")
    wolf.hp = 1
    goblin = state.characters["goblin_1"]
    goblin.position = Position(x=6, y=0)
    from src.engine.turn_engine import _apply_damage_and_handle_downing

    _apply_damage_and_handle_downing(
        state, goblin, wolf, 5, "slashing", random.Random(1), load_srd()
    )
    assert wolf.is_dead and wolf.hp == 0
    assert not any(c.name == "unconscious" for c in wolf.conditions)


def test_the_party_is_defeated_even_if_a_summon_is_still_standing() -> None:
    state = _state(["mira", "goblin_1"], current=0)
    mira = state.characters["mira"]
    add_combatant(state, _wolf("wolf_a"), mira, "Conjure Animals")
    mira.is_dead = True
    from src.engine.turn_engine import _check_victory_defeat

    _check_victory_defeat(state)
    assert state.status == "defeat"


def test_killing_every_monster_wins_even_with_the_party_summon_alive() -> None:
    state = _state(["mira", "goblin_1"], current=0)
    add_combatant(state, _wolf("wolf_a"), state.characters["mira"], "Conjure Animals")
    state.characters["goblin_1"].is_dead = True
    from src.engine.turn_engine import _check_victory_defeat

    _check_victory_defeat(state)
    assert state.status == "victory"


def test_a_monsters_conjured_creature_still_has_to_be_killed_for_victory() -> None:
    state = _state(["mira", "goblin_1"], current=1)
    add_combatant(state, _wolf("wolf_a"), state.characters["goblin_1"], "Animate Dead")
    state.characters["goblin_1"].is_dead = True
    from src.engine.turn_engine import _check_victory_defeat

    _check_victory_defeat(state)
    assert state.status == "in_progress"


def test_a_party_summon_is_driven_like_a_monster_and_attacks_the_nearest_enemy() -> None:
    state = _state(["mira", "goblin_1", "goblin_2"], current=0)
    wolf = _wolf("wolf_a", 5, 0)  # right next to goblin_1 (6,0), one square from goblin_2
    add_combatant(state, wolf, state.characters["mira"], "Conjure Animals")
    action = choose_monster_action(state, wolf)
    assert action.verb == "attack"
    assert action.target == "goblin_1"


def test_a_party_summon_never_targets_its_own_side_and_a_monster_can_target_it() -> None:
    state = _state(["mira", "thorin", "goblin_1"], current=0)
    wolf = _wolf("wolf_a", 5, 0)
    add_combatant(state, wolf, state.characters["mira"], "Conjure Animals")
    assert choose_monster_action(state, wolf).target == "goblin_1"
    goblin = state.characters["goblin_1"]
    # the wolf is the closest member of the party's side to the goblin
    assert choose_monster_action(state, goblin).target == "wolf_a"


def test_a_summoned_creature_really_resolves_its_attack() -> None:
    state = _state(["mira", "goblin_1"], current=0)
    wolf = _wolf("wolf_a", 5, 0)
    add_combatant(state, wolf, state.characters["mira"], "Conjure Animals")
    state.current_turn = state.turn_order.index("wolf_a")
    action = choose_monster_action(state, wolf)
    resolve_action(state, action, random.Random(3))
    assert any(e.type == "attack_roll" and e.actor == "wolf_a" for e in state.events)


def test_it_cannot_attack_a_party_member() -> None:
    state = _state(["mira", "goblin_1"], current=0)
    wolf = _wolf("wolf_a", 1, 0)
    add_combatant(state, wolf, state.characters["mira"], "Conjure Animals")
    state.current_turn = state.turn_order.index("wolf_a")
    bad = ParsedAction(actor="wolf_a", verb="attack", target="mira", raw_text="x")
    with pytest.raises(Exception, match="same side|friendly|ally"):
        resolve_action(state, bad, random.Random(1))


def test_find_open_square_picks_the_nearest_free_floor_square() -> None:
    state = _state(["mira", "goblin_1"], current=0)
    # ties on distance go to the lowest row, then the lowest column
    assert find_open_square(state, Position(x=0, y=0), set()) == Position(x=1, y=0)
    assert find_open_square(state, Position(x=0, y=0), {(1, 0)}) == Position(x=0, y=1)


def test_find_open_square_skips_walls_and_reports_a_full_map() -> None:
    state = _state(["mira"], current=0, width=1)
    assert state.battle_map is not None
    state.battle_map.terrain = [["floor"], ["wall"], ["floor"]]
    assert find_open_square(state, Position(x=0, y=0), set()) == Position(x=0, y=2)
    with pytest.raises(SummonError, match="no free square"):
        find_open_square(state, Position(x=0, y=0), {(0, 2)})
