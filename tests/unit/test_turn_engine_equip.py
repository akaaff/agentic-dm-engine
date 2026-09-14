"""Equipped-weapon tracking (Phase C, character-sheet-feature follow-up):
attack resolution only ever matches a weapon from Character.equipped_weapons,
not the whole inventory - "own it" and "have it equipped" are deliberately
different things. A new "equip" verb (doesn't end the turn, one free
object-interaction per turn) switches the active set, gated by
rules.weapon_combo_is_legal (at most 2 weapons; a two-handed weapon must be
alone; 2 together must both be light).
"""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _open_map(width: int, height: int) -> BattleMap:
    return BattleMap(
        width=width,
        height=height,
        terrain=[["floor"] * width for _ in range(height)],
        spawn_points={},
    )


def _make_state(*characters: Character) -> GameState:
    return GameState(
        encounter_id="equip_test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
        battle_map=_open_map(10, 10),
    )


def _goblin(char_id: str, position: Position) -> Character:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], char_id, position)
    goblin.hp = goblin.max_hp = 100
    return goblin


def _fighter(position: Position | None = None) -> Character:
    position = position or Position(x=0, y=0)
    return create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=position,
    )


def test_equip_rejects_an_unowned_item() -> None:
    thorin = _fighter()
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["greataxe"]}, raw_text="I draw a greataxe"
    )
    with pytest.raises(TurnEngineError, match="doesn't own"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_equip_rejects_a_non_weapon_item() -> None:
    thorin = _fighter()
    thorin.inventory.append("leather-armor")
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["leather-armor"]},
        raw_text="I equip my armor",
    )
    with pytest.raises(TurnEngineError, match="not a weapon"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_equip_rejects_a_two_handed_weapon_combined_with_anything() -> None:
    thorin = _fighter()
    thorin.inventory += ["greataxe", "dagger"]
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["greataxe", "dagger"]},
        raw_text="I heft my greataxe and draw a dagger",
    )
    with pytest.raises(TurnEngineError, match="Cannot equip"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_equip_rejects_two_non_light_weapons_together() -> None:
    thorin = _fighter()
    # already owns longsword; shortsword IS light, longsword isn't
    thorin.inventory.append("shortsword")
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["longsword", "shortsword"]},
        raw_text="I wield both",
    )
    with pytest.raises(TurnEngineError, match="Cannot equip"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_equip_accepts_two_light_weapons() -> None:
    thorin = _fighter()
    thorin.inventory += ["dagger", "dagger"]
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["dagger", "dagger"]},
        raw_text="I draw two daggers",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert thorin.equipped_weapons == ["dagger", "dagger"]
    assert state.events[-1].type == "equip"
    assert state.turn_order[state.current_turn] == "thorin"  # doesn't end the turn


def test_equip_changes_what_attack_resolves_against() -> None:
    thorin = _fighter()
    thorin.inventory.append("dagger")
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)

    equip_action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["dagger"]}, raw_text="I draw my dagger"
    )
    resolve_action(state, equip_action, _FixedRandom([]))  # type: ignore[arg-type]

    # The newly-equipped dagger now resolves fine...
    attack_with_dagger = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="dagger",
        raw_text="I stab with my dagger",
    )
    resolve_action(state, attack_with_dagger, _FixedRandom([15, 3]))  # type: ignore[arg-type]
    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["source"] == "Dagger"


def test_attack_rejected_with_a_weapon_no_longer_equipped() -> None:
    thorin = _fighter()
    thorin.inventory.append("dagger")
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)

    equip_action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["dagger"]}, raw_text="I draw my dagger"
    )
    resolve_action(state, equip_action, _FixedRandom([]))  # type: ignore[arg-type]

    # ...but the longsword, no longer equipped (even though still owned), is rejected.
    attack_with_longsword = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I swing my longsword",
    )
    with pytest.raises(TurnEngineError, match="isn't in thorin's equipped weapon set"):
        resolve_action(state, attack_with_longsword, _FixedRandom([]))  # type: ignore[arg-type]


def test_equip_rejected_a_second_time_in_the_same_turn() -> None:
    thorin = _fighter()
    thorin.inventory += ["dagger", "greataxe"]
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))

    first = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["dagger"]}, raw_text="I draw a dagger"
    )
    resolve_action(state, first, _FixedRandom([]))  # type: ignore[arg-type]

    second = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["greataxe"]}, raw_text="I switch to my axe"
    )
    with pytest.raises(TurnEngineError, match="already equipped something this turn"):
        resolve_action(state, second, _FixedRandom([]))  # type: ignore[arg-type]


def test_equip_succeeds_again_once_the_turn_has_advanced_back() -> None:
    thorin = _fighter()
    thorin.inventory += ["dagger", "greataxe"]
    goblin = _goblin("goblin_1", Position(x=5, y=5))
    state = _make_state(thorin, goblin)

    first = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["dagger"]}, raw_text="I draw a dagger"
    )
    resolve_action(state, first, _FixedRandom([]))  # type: ignore[arg-type]

    # End thorin's turn, let the goblin end its own turn too, so a real
    # turn advance lands back on thorin (equip_used_this_turn resets in
    # _advance_turn_skipping_dead, same schedule as bonus_action_used).
    resolve_action(
        state,
        ParsedAction(actor="thorin", verb="end_turn", raw_text="done"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    resolve_action(
        state,
        ParsedAction(actor="goblin_1", verb="end_turn", raw_text="done"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    assert state.turn_order[state.current_turn] == "thorin"

    second = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["greataxe"]}, raw_text="I switch to my axe"
    )
    resolve_action(state, second, _FixedRandom([]))  # type: ignore[arg-type]
    assert thorin.equipped_weapons == ["greataxe"]
