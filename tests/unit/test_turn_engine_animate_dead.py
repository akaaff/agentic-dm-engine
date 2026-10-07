"""Issue #56, phase C: Animate Dead.

Raises a dead Small or Medium humanoid as a zombie in the corpse's own square. Unlike Conjure
Animals there is no concentration and no time limit: the servant is recorded on the caster, every
later fight starts with it, and it is gone only when destroyed."""

from __future__ import annotations

import random

import pytest

from src.api.routes.characters import _character_to_record, _record_to_character
from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.monster_ai import choose_monster_action
from src.engine.position import Position
from src.engine.rules import is_party_member
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
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


def _necromancer() -> Character:
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
    wizard.prepared_spells.append("animate-dead")
    wizard.spell_slots = {1: 2, 3: 2}
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
    # Elrond rolls highest, so he acts first. goblin_1 lies dead next to him, goblin_2 is alive.
    state = build_encounter_state(
        build_demo_encounter(),
        [_necromancer(), _fighter()],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )
    corpse = state.characters["goblin_1"]
    corpse.is_dead = True
    corpse.hp = 0
    corpse.position = Position(x=1, y=1)
    state.characters["elrond"].position = Position(x=0, y=1)
    return state


def _cast(state: GameState, target: str | None = "goblin_1") -> None:
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target=target,
        item_or_spell="Animate Dead",
        raw_text="I animate the dead",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def _servants(state: GameState) -> list[Character]:
    return [c for c in state.characters.values() if c.summon_spell == "Animate Dead"]


def test_it_raises_the_corpse_as_a_zombie_in_its_own_square() -> None:
    state = _state()
    _cast(state)
    (zombie,) = _servants(state)
    assert zombie.monster_index == "zombie"
    assert (zombie.position.x, zombie.position.y) == (1, 1)
    assert zombie.summoned_by == "elrond" and zombie.is_pc
    assert not is_party_member(zombie)
    assert zombie.name == "Elrond's zombie 1"
    assert state.turn_order.index(zombie.id) == state.turn_order.index("elrond") + 1


def test_the_corpse_is_used_up_and_the_event_names_it() -> None:
    state = _state()
    _cast(state)
    assert state.characters["goblin_1"].raised is True
    cast = next(e for e in state.events if e.type == "spell_cast")
    assert cast.payload["raised"] == state.characters["goblin_1"].name


def test_it_spends_a_slot_but_needs_no_concentration() -> None:
    state = _state()
    _cast(state)
    elrond = state.characters["elrond"]
    assert elrond.spell_slots[3] == 1
    assert elrond.concentrating_on is None


def test_the_servant_is_recorded_on_the_caster() -> None:
    state = _state()
    _cast(state)
    assert state.characters["elrond"].undead_servants == ["zombie"]


def test_with_no_target_the_nearest_corpse_in_reach_is_raised() -> None:
    state = _state()
    state.characters["goblin_2"].is_dead = True
    state.characters["goblin_2"].position = Position(x=2, y=1)  # farther than goblin_1
    _cast(state, target=None)
    assert state.characters["goblin_1"].raised is True
    assert state.characters["goblin_2"].raised is False


@pytest.mark.parametrize(
    ("setup", "match"),
    [
        ("alive", "isn't one"),
        ("raised", "isn't one"),
        ("far", "out of range"),
        ("party", "isn't one"),
    ],
)
def test_a_cast_that_cant_work_is_refused_and_costs_nothing(setup: str, match: str) -> None:
    state = _state()
    target = "goblin_1"
    if setup == "alive":
        state.characters["goblin_1"].is_dead = False
        state.characters["goblin_1"].hp = 5
    elif setup == "raised":
        state.characters["goblin_1"].raised = True
    elif setup == "far":
        state.characters["goblin_1"].position = Position(x=9, y=2)
    elif setup == "party":
        state.characters["thorin"].is_dead = True
        target = "thorin"
    with pytest.raises(TurnEngineError, match=match):
        _cast(state, target)
    elrond = state.characters["elrond"]
    assert elrond.spell_slots[3] == 2
    assert elrond.undead_servants == []
    assert _servants(state) == []


def test_only_a_small_or_medium_humanoid_can_be_raised() -> None:
    state = _state()
    wolf = monster_to_character(load_srd().monsters["wolf"], "wolf_1", Position(x=1, y=1))
    wolf.is_dead = True
    state.characters["wolf_1"] = wolf
    state.turn_order.append("wolf_1")
    with pytest.raises(TurnEngineError, match="isn't one"):
        _cast(state, "wolf_1")


def test_with_no_corpse_in_reach_there_is_nothing_to_raise() -> None:
    state = _state()
    state.characters["goblin_1"].is_dead = False
    with pytest.raises(TurnEngineError, match="no corpse"):
        _cast(state, target=None)


def test_a_cast_with_no_slot_left_is_refused() -> None:
    state = _state()
    state.characters["elrond"].spell_slots[3] = 0
    with pytest.raises(TurnEngineError, match="no level-3 spell slots"):
        _cast(state)


def test_the_zombie_fights_for_its_caster_on_its_own_turn() -> None:
    state = _state()
    _cast(state)
    (zombie,) = _servants(state)
    goblin = state.characters["goblin_2"]
    goblin.position = Position(x=2, y=1)  # right beside the zombie
    state.current_turn = state.turn_order.index(zombie.id)
    action = choose_monster_action(state, zombie)
    assert (action.verb, action.target) == ("attack", "goblin_2")
    resolve_action(state, action, random.Random(3))
    assert any(e.type == "attack_roll" and e.actor == zombie.id for e in state.events)


def test_a_destroyed_servant_drops_off_the_casters_list() -> None:
    state = _state()
    state.characters["goblin_2"].is_dead = True
    state.characters["goblin_2"].position = Position(x=1, y=2)
    _cast(state, "goblin_1")
    state.current_turn = state.turn_order.index("elrond")
    _cast(state, "goblin_2")
    first, second = sorted(_servants(state), key=lambda c: c.id)
    assert state.characters["elrond"].undead_servants == ["zombie", "zombie"]
    attacker = state.characters["thorin"]
    _apply_damage_and_handle_downing(
        state, attacker, first, 99, "slashing", random.Random(1), load_srd()
    )
    assert first.is_dead and not second.is_dead
    assert state.characters["elrond"].undead_servants == ["zombie"]


def test_every_later_fight_starts_with_the_servants() -> None:
    wizard = _necromancer()
    wizard.undead_servants = ["zombie", "skeleton"]
    state = build_encounter_state(
        build_demo_encounter(),
        [wizard, _fighter()],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )
    first = state.characters["elrond_undead_1"]
    second = state.characters["elrond_undead_2"]
    assert (first.monster_index, second.monster_index) == ("zombie", "skeleton")
    assert first.hp == first.max_hp
    assert state.turn_order.index(first.id) > state.turn_order.index("elrond")


def test_a_caster_with_no_servants_is_unchanged() -> None:
    state = _state()
    assert _servants(state) == []


def test_the_servants_survive_a_save_and_reload() -> None:
    wizard = _necromancer()
    wizard.undead_servants = ["zombie", "zombie"]
    assert _record_to_character(_character_to_record(wizard)).undead_servants == [
        "zombie",
        "zombie",
    ]
