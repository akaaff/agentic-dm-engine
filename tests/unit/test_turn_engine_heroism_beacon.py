"""Issue #62 (two of the buffs it lists): Heroism and Beacon of Hope.

Same fixture shape as test_turn_engine_buff_spells.py (Thorin the fighter and
Elrond the wizard against the demo encounter's goblins). Neither spell is on
the wizard's real list, so the cast tests poke it onto `prepared_spells` the way
the other spell tests poke a slot they need - what is under test is the engine's
handling of the spell, not who may learn it.

Turn order: thorin, elrond, goblin_1, goblin_2. Thorin (0,1) and Elrond (1,2) are
adjacent, so Heroism's touch range is satisfied between them.
"""

from __future__ import annotations

import random

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import build_encounter_state
from src.engine.rules import condition_save_advantage, monster_is_immune_to_condition
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import _end_concentration, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _party() -> list[Character]:
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
        chosen_prepared_spells=["magic-missile", "mage-armor", "sleep"],
        chosen_equipment=["dagger"],
    )
    elrond.prepared_spells += ["heroism", "beacon-of-hope"]
    elrond.spell_slots = {1: 2, 3: 1}
    return [thorin, elrond]


def _state() -> GameState:
    state = build_encounter_state(
        build_demo_encounter(),
        _party(),
        _FixedRandom([18, 10, 8, 3]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("elrond")
    return state


def _cast(state: GameState, spell: str, *, target: str | None = None, targets=None) -> None:  # type: ignore[no-untyped-def]
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target=target,
        targets=targets,
        item_or_spell=spell,
        raw_text=f"I cast {spell}",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def _end_turn(state: GameState, actor_id: str) -> None:
    state.current_turn = state.turn_order.index(actor_id)
    resolve_action(
        state, ParsedAction(actor=actor_id, verb="end_turn", raw_text="x"), random.Random()
    )


def _pass_the_round(state: GameState) -> None:
    """Elrond's turn is over (he just cast) - play out the goblins so the round wraps to
    Thorin."""
    _end_turn(state, "goblin_1")
    _end_turn(state, "goblin_2")


def _heroic(state: GameState) -> None:
    apply_condition(
        state.characters["thorin"],
        Condition(name="heroic", duration_rounds=10, source="elrond", spell="Heroism", detail="2"),
    )


def _beacon(character: Character) -> None:
    apply_condition(
        character,
        Condition(
            name="beacon_of_hope", duration_rounds=10, source="elrond", spell="Beacon of Hope"
        ),
    )


def test_heroism_records_the_casters_modifier_and_makes_the_caster_concentrate() -> None:
    state = _state()
    _cast(state, "heroism", target="thorin")
    condition = next(c for c in state.characters["thorin"].conditions if c.name == "heroic")
    assert condition.detail == "2"  # INT 15 -> +2
    assert condition.duration_rounds == 10
    assert state.characters["elrond"].concentrating_on == "Heroism"


def test_heroism_gives_temp_hp_at_the_start_of_the_creatures_turn() -> None:
    state = _state()
    _cast(state, "heroism", target="thorin")
    _pass_the_round(state)
    assert state.current_turn == state.turn_order.index("thorin")
    assert state.characters["thorin"].temp_hp == 2
    assert any(e.type == "temp_hp" and e.payload["source"] == "Heroism" for e in state.events)


def test_heroism_temp_hp_does_not_stack_turn_over_turn() -> None:
    state = _state()
    _cast(state, "heroism", target="thorin")
    _pass_the_round(state)
    _end_turn(state, "thorin")
    _end_turn(state, "elrond")
    _pass_the_round(state)
    assert state.characters["thorin"].temp_hp == 2


def test_heroism_cures_and_prevents_being_frightened() -> None:
    state = _state()
    thorin = state.characters["thorin"]
    apply_condition(thorin, Condition(name="frightened", duration_rounds=5, source="goblin_1"))
    _cast(state, "heroism", target="thorin")
    assert not has_condition(thorin, "frightened")
    assert monster_is_immune_to_condition(thorin, "frightened", load_srd())
    assert not monster_is_immune_to_condition(thorin, "charmed", load_srd())


def test_a_creature_without_heroism_can_be_frightened() -> None:
    thorin = _party()[0]
    assert not monster_is_immune_to_condition(thorin, "frightened", load_srd())


def test_the_temp_hp_go_when_concentration_ends() -> None:
    state = _state()
    _cast(state, "heroism", target="thorin")
    _pass_the_round(state)
    assert state.characters["thorin"].temp_hp == 2
    _end_concentration(state, state.characters["elrond"], load_srd())
    assert not has_condition(state.characters["thorin"], "heroic")
    assert state.characters["thorin"].temp_hp == 0


def test_the_temp_hp_go_when_heroism_runs_out() -> None:
    state = _state()
    thorin = state.characters["thorin"]
    apply_condition(
        thorin,
        Condition(name="heroic", duration_rounds=1, source="elrond", spell="Heroism", detail="2"),
    )
    thorin.temp_hp = 2
    state.current_turn = state.turn_order.index("goblin_2")  # the round's last turn
    resolve_action(
        state, ParsedAction(actor="goblin_2", verb="end_turn", raw_text="x"), random.Random()
    )
    assert not has_condition(thorin, "heroic")
    assert thorin.temp_hp == 0


def test_beacon_of_hope_can_be_cast_on_several_creatures() -> None:
    state = _state()
    _cast(state, "beacon-of-hope", targets=["thorin", "elrond"])
    for who in ("thorin", "elrond"):
        assert has_condition(state.characters[who], "beacon_of_hope")
    assert state.characters["elrond"].concentrating_on == "Beacon of Hope"
    assert state.characters["elrond"].spell_slots[3] == 0


def test_beacon_of_hope_gives_advantage_on_wisdom_saves_only() -> None:
    thorin = _party()[0]
    assert not condition_save_advantage(thorin, "WIS")
    _beacon(thorin)
    assert condition_save_advantage(thorin, "WIS")
    assert not condition_save_advantage(thorin, "DEX")


def _dying_thorin() -> GameState:
    state = _state()
    thorin = state.characters["thorin"]
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious", duration_rounds=None, source="0 HP"))
    state.current_turn = state.turn_order.index("thorin")
    return state


def _death_save(state: GameState, rolls: list[int]) -> None:
    action = ParsedAction(actor="thorin", verb="death_save", raw_text="x")
    resolve_action(state, action, _FixedRandom(rolls))  # type: ignore[arg-type]


def test_a_death_save_without_beacon_of_hope_rolls_one_die() -> None:
    state = _dying_thorin()
    _death_save(state, [3])
    assert state.characters["thorin"].death_save_failures == 1


def test_beacon_of_hope_gives_advantage_on_death_saves() -> None:
    state = _dying_thorin()
    _beacon(state.characters["thorin"])
    _death_save(state, [3, 15])  # two dice; the better one counts
    assert state.characters["thorin"].death_save_successes == 1
    assert state.characters["thorin"].death_save_failures == 0


def test_beacon_of_hope_maximizes_second_wind() -> None:
    state = _state()
    thorin = state.characters["thorin"]
    thorin.max_hp, thorin.hp = 40, 5
    state.current_turn = state.turn_order.index("thorin")
    _beacon(thorin)
    action = ParsedAction(actor="thorin", verb="second_wind", raw_text="x")
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert thorin.hp == 5 + 10 + thorin.level  # 1d10 + level, at its maximum - no die rolled


def test_second_wind_still_rolls_without_beacon_of_hope() -> None:
    state = _state()
    thorin = state.characters["thorin"]
    thorin.max_hp, thorin.hp = 40, 5
    state.current_turn = state.turn_order.index("thorin")
    action = ParsedAction(actor="thorin", verb="second_wind", raw_text="x")
    resolve_action(state, action, _FixedRandom([3]))  # type: ignore[arg-type]
    assert thorin.hp == 5 + 3 + thorin.level


def test_beacon_of_hope_maximizes_a_healing_potion() -> None:
    state = _state()
    thorin = state.characters["thorin"]
    thorin.max_hp, thorin.hp = 40, 5
    state.current_turn = state.turn_order.index("thorin")
    _beacon(thorin)
    action = ParsedAction(
        actor="thorin", verb="use_item", item_or_spell="healing potion", raw_text="x"
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert thorin.hp == 5 + 2 * 4 + 2  # 2d4+2 at its maximum


def test_beacon_of_hope_maximizes_a_healing_spell() -> None:
    state = _state()
    state.characters["elrond"].prepared_spells.append("cure-wounds")
    thorin = state.characters["thorin"]
    thorin.max_hp, thorin.hp = 40, 5
    _beacon(thorin)
    _cast(state, "cure-wounds", target="thorin")
    mod = 2  # INT 15 -> +2
    assert thorin.hp == 5 + 8 + mod  # 1d8 + modifier, at its maximum
