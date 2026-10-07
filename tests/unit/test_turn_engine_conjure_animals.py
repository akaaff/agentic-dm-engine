"""Issue #56, phase B: Conjure Animals - the first spell to use the summoning mechanism.

A level-5 druid (poked: 3rd-level slots come from level-ups, which nothing in the app
triggers yet) casts it with a goblin 30 ft away. Expectations are the fixed option the
engine always picks - two dire wolves (CR 1) - and the lifetime rules: they last while
the caster concentrates and go the moment concentration ends."""

from __future__ import annotations

import random

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.monster_ai import choose_monster_action
from src.engine.position import BattleMap, Position
from src.engine.rules import is_party_member
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import (
    CONJURE_ANIMALS_COUNT,
    TurnEngineError,
    _apply_damage_and_handle_downing,
    resolve_action,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _druid() -> Character:
    druid = create_character(
        character_id="ilsa",
        name="Ilsa",
        race_index="human",
        class_index="druid",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 13, "INT": 10, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-nature", "skill-survival"],
        chosen_prepared_spells=["cure-wounds", "entangle", "goodberry", "faerie-fire"],
        position=Position(x=0, y=0),
    )
    druid.level = 5
    druid.spell_slots = {1: 4, 2: 3, 3: 2}
    druid.prepared_spells.append("conjure-animals")
    return druid


def _state(width: int = 10, height: int = 3) -> GameState:
    druid = _druid()
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=6, y=0))
    goblin.hp = goblin.max_hp = 200
    return GameState(
        encounter_id="conjure_test",
        characters={druid.id: druid, goblin.id: goblin},
        turn_order=[druid.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=width,
            height=height,
            terrain=[["floor"] * width for _ in range(height)],
            spawn_points={},
        ),
    )


def _cast(state: GameState, actor: str = "ilsa") -> None:
    action = ParsedAction(
        actor=actor,
        verb="cast_spell",
        item_or_spell="Conjure Animals",
        raw_text="I conjure animals",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def _wolves(state: GameState) -> list[Character]:
    return [c for c in state.characters.values() if c.summoned_by == "ilsa"]


def test_it_conjures_two_dire_wolves_that_act_right_after_the_druid() -> None:
    state = _state()
    _cast(state)
    wolves = _wolves(state)
    assert len(wolves) == CONJURE_ANIMALS_COUNT == 2
    assert all(w.monster_index == "dire-wolf" for w in wolves)
    assert state.turn_order == ["ilsa", wolves[0].id, wolves[1].id, "goblin_1"]
    # the cast was the druid's action, so the turn has moved on to the first wolf
    assert state.turn_order[state.current_turn] == wolves[0].id


def test_the_cast_spends_a_slot_and_starts_concentration() -> None:
    state = _state()
    _cast(state)
    druid = state.characters["ilsa"]
    assert druid.spell_slots[3] == 1
    assert druid.concentrating_on == "Conjure Animals"


def test_the_wolves_are_on_the_party_side_but_not_party_members() -> None:
    state = _state()
    _cast(state)
    for wolf in _wolves(state):
        assert wolf.is_pc is True
        assert not is_party_member(wolf)
        assert wolf.summon_spell == "Conjure Animals"


def test_they_appear_on_distinct_free_squares_near_the_caster() -> None:
    state = _state()
    _cast(state)
    squares = {(w.position.x, w.position.y) for w in _wolves(state)}
    assert len(squares) == 2
    assert squares.isdisjoint({(0, 0), (6, 0)})
    assert all(max(x, y) <= 1 for x, y in squares)  # adjacent to the druid at (0, 0)


def test_the_cast_is_logged_before_the_creatures_appear() -> None:
    state = _state()
    _cast(state)
    types = [e.type for e in state.events]
    assert types.index("spell_cast") < types.index("summoned")
    assert types.count("summoned") == 2


def test_a_summoned_wolf_hunts_the_goblin_on_its_own_turn() -> None:
    state = _state()
    _cast(state)
    wolf = _wolves(state)[0]
    for _ in range(3):  # a wolf's turn: close the distance, then bite
        if state.turn_order[state.current_turn] != wolf.id:
            break
        resolve_action(state, choose_monster_action(state, wolf), random.Random(2))
    assert any(e.type == "attack_roll" and e.actor == wolf.id for e in state.events)
    assert all(e.payload.get("target") != "ilsa" for e in state.events if e.actor == wolf.id)


def test_losing_concentration_makes_the_wolves_vanish() -> None:
    state = _state()
    _cast(state)
    druid, goblin = state.characters["ilsa"], state.characters["goblin_1"]
    ids = [w.id for w in _wolves(state)]
    # 30 damage means a DC 15 CON save; a natural 1 cannot make it
    _apply_damage_and_handle_downing(
        state,
        goblin,
        druid,
        30,
        "slashing",
        _FixedRandom([1]),  # type: ignore[arg-type]
        load_srd(),
    )
    assert druid.concentrating_on is None
    for wolf_id in ids:
        assert wolf_id not in state.characters
        assert wolf_id not in state.turn_order
    assert sum(e.type == "summon_ended" for e in state.events) == 2


def test_recasting_replaces_the_old_wolves_instead_of_dismissing_the_new_ones() -> None:
    state = _state()
    _cast(state)
    first_objects = _wolves(state)
    state.current_turn = state.turn_order.index("ilsa")
    state.characters["ilsa"].action_used_this_turn = False
    _cast(state)
    second = _wolves(state)
    assert len(second) == 2
    # the first pair was dismissed (ids can be reused) and a fresh pair stands
    assert all(new is not old for new in second for old in first_objects)
    assert sum(e.type == "summon_ended" for e in state.events) == 2
    assert state.turn_order == ["ilsa", second[0].id, second[1].id, "goblin_1"]
    assert state.characters["ilsa"].concentrating_on == "Conjure Animals"
    assert state.characters["ilsa"].spell_slots[3] == 0


def test_a_cast_with_no_room_costs_nothing() -> None:
    state = _state(width=1, height=1)
    druid = state.characters["ilsa"]
    state.characters["goblin_1"].position = Position(x=0, y=0)  # nothing free at all
    druid.position = Position(x=0, y=0)
    with pytest.raises(TurnEngineError, match="nowhere to put the beasts"):
        _cast(state)
    assert druid.spell_slots[3] == 2
    assert druid.concentrating_on is None
    assert state.turn_order == ["ilsa", "goblin_1"]


def test_a_cast_that_fails_keeps_an_existing_concentration() -> None:
    state = _state(width=1, height=1)
    druid = state.characters["ilsa"]
    druid.concentrating_on = "Entangle"
    state.characters["goblin_1"].position = Position(x=0, y=0)
    with pytest.raises(TurnEngineError):
        _cast(state)
    assert druid.concentrating_on == "Entangle"


def test_an_unprepared_conjure_animals_is_refused() -> None:
    state = _state()
    state.characters["ilsa"].prepared_spells.remove("conjure-animals")
    with pytest.raises(TurnEngineError, match="hasn't prepared"):
        _cast(state)
