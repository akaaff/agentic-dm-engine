"""Issue #56, phase E: Animate Objects.

Ordinary objects become constructs whose statistics come from their size (the table in the spell
text, built as synthetic stat blocks in engine/synthetic_monsters.py). Size and number come from
the cast - default three Small objects - bounded by the spell's ten object slots, and they last
while the caster concentrates, like Conjure Animals' creatures."""

from __future__ import annotations

import random

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state
from src.engine.monster_ai import choose_monster_action
from src.engine.position import Position
from src.engine.rules import (
    is_party_member,
    monster_damage_multiplier,
    monster_is_immune_to_condition,
)
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.synthetic_monsters import ANIMATED_OBJECT_TABLE, SYNTHETIC_MONSTERS
from src.engine.turn_engine import (
    TurnEngineError,
    _apply_damage_and_handle_downing,
    resolve_action,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _wizard() -> Character:
    wizard = create_character(
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
    wizard.prepared_spells.append("animate-objects")
    wizard.spell_slots = {1: 2, 5: 2}
    return wizard


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


def _state() -> GameState:
    state = build_encounter_state(
        build_demo_encounter(),
        [_wizard(), _fighter()],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )
    for goblin in ("goblin_1", "goblin_2"):
        state.characters[goblin].hp = state.characters[goblin].max_hp = 200
    return state


def _cast(state: GameState, text: str = "I animate objects") -> None:
    action = ParsedAction(
        actor="elrond", verb="cast_spell", item_or_spell="Animate Objects", raw_text=text
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def _objects(state: GameState) -> list[Character]:
    return sorted(
        (c for c in state.characters.values() if c.summoned_by == "elrond"), key=lambda c: c.id
    )


def test_the_default_is_three_small_objects() -> None:
    state = _state()
    _cast(state)
    objects = _objects(state)
    assert len(objects) == 3
    assert {o.monster_index for o in objects} == {"animated-object-small"}
    assert all(o.hp == o.max_hp == 25 and o.ac == 16 for o in objects)


def test_they_fight_for_the_caster_right_after_it() -> None:
    state = _state()
    _cast(state)
    objects = _objects(state)
    assert all(o.is_pc and not is_party_member(o) for o in objects)
    start = state.turn_order.index("elrond")
    assert state.turn_order[start + 1 : start + 4] == [o.id for o in objects]
    assert objects[0].name == "Elrond's animated object 1"


def test_it_spends_a_fifth_level_slot_and_the_caster_concentrates() -> None:
    state = _state()
    _cast(state)
    elrond = state.characters["elrond"]
    assert elrond.spell_slots[5] == 1
    assert elrond.concentrating_on == "Animate Objects"


@pytest.mark.parametrize(
    ("text", "size", "count"),
    [
        ("I animate five tiny objects", "tiny", 5),
        ("animate 2 large objects", "large", 2),
        ("animate one huge statue", "huge", 1),
        ("animate ten small chairs", "small", 10),
        ("animate the medium crates", "medium", 3),
    ],
)
def test_size_and_number_come_from_the_casters_words(text: str, size: str, count: int) -> None:
    state = _state()
    _cast(state, text)
    objects = _objects(state)
    assert len(objects) == count
    assert {o.monster_index for o in objects} == {f"animated-object-{size}"}
    assert objects[0].max_hp == ANIMATED_OBJECT_TABLE[size][0]


def test_with_no_number_a_big_object_defaults_to_what_the_slots_allow() -> None:
    state = _state()
    _cast(state, "animate the large boulders")  # ten slots, four each: at most two
    assert len(_objects(state)) == 2


@pytest.mark.parametrize(
    "text",
    ["animate three large objects", "animate two huge objects", "animate six medium objects"],
)
def test_more_than_the_ten_slots_allow_is_refused_and_costs_nothing(text: str) -> None:
    state = _state()
    with pytest.raises(TurnEngineError, match="at most"):
        _cast(state, text)
    assert state.characters["elrond"].spell_slots[5] == 2
    assert state.characters["elrond"].concentrating_on is None
    assert _objects(state) == []


def test_recasting_replaces_the_old_objects() -> None:
    state = _state()
    _cast(state, "animate two tiny objects")
    state.current_turn = state.turn_order.index("elrond")
    _cast(state, "animate four small objects")
    objects = _objects(state)
    assert len(objects) == 4
    assert {o.monster_index for o in objects} == {"animated-object-small"}
    assert state.characters["elrond"].spell_slots[5] == 0


def test_losing_concentration_ends_them() -> None:
    state = _state()
    _cast(state)
    elrond, goblin = state.characters["elrond"], state.characters["goblin_1"]
    _apply_damage_and_handle_downing(
        state,
        goblin,
        elrond,
        30,
        "slashing",
        _FixedRandom([1]),  # type: ignore[arg-type]
        load_srd(),
    )
    assert elrond.concentrating_on is None
    assert _objects(state) == []


def test_an_object_swings_with_its_tables_attack_bonus() -> None:
    state = _state()
    _cast(state, "animate two tiny objects")
    tiny = _objects(state)[0]
    goblin = state.characters["goblin_1"]
    goblin.position = Position(x=tiny.position.x + 1, y=tiny.position.y)
    state.current_turn = state.turn_order.index(tiny.id)
    action = choose_monster_action(state, tiny)
    assert action.verb == "attack"
    resolve_action(state, action, random.Random(5))
    roll = next(e for e in state.events if e.type == "attack_roll" and e.actor == tiny.id)
    assert roll.payload["attack_bonus_breakdown"] == [("attack bonus (stat block)", 8)]


def test_objects_are_immune_to_poison_psychic_and_the_charm_family() -> None:
    state = _state()
    _cast(state)
    srd = load_srd()
    obj = _objects(state)[0]
    assert monster_damage_multiplier(obj, "poison", srd) == 0
    assert monster_damage_multiplier(obj, "psychic", srd) == 0
    assert monster_damage_multiplier(obj, "slashing", srd) == 1
    for condition in ("charmed", "frightened", "poisoned", "paralyzed"):
        assert monster_is_immune_to_condition(obj, condition, srd)


def test_the_table_matches_the_spell_text() -> None:
    assert ANIMATED_OBJECT_TABLE["huge"][:4] == (80, 10, 8, "2d12+4")
    assert ANIMATED_OBJECT_TABLE["medium"][:4] == (40, 13, 5, "2d6+1")


def test_synthetic_entries_are_flagged_and_real_monsters_are_not() -> None:
    srd = load_srd()
    assert all(m["synthetic"] for m in SYNTHETIC_MONSTERS.values())
    assert "synthetic" not in srd.monsters["goblin"]
    assert srd.monsters["animated-object-small"]["synthetic"] is True
