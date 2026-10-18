"""Attack events say how the d20 was rolled and why: `roll_mode` (advantage / disadvantage /
normal, where having both kinds is normal because they cancel), the reasons behind each, and the
dice actually rolled when there were two.

Prompted by a live question - Wolf 2 was under Vicious Mockery but its next bite looked like an
ordinary roll. The log couldn't show that Pack Tactics' advantage had cancelled the mockery's
disadvantage; now it can.

Attack AC is set to 99 throughout so every attack misses: no damage dice are consumed, and the
_FixedRandom sequences only have to cover the d20(s)."""

from __future__ import annotations

from typing import Any

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.position import Position
from src.engine.rules import (
    condition_attack_advantage,
    condition_attack_advantage_reasons,
    condition_attack_disadvantage,
    condition_attack_disadvantage_reasons,
)
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _fighter() -> Character:
    fighter = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    fighter.position = Position(x=0, y=0)
    fighter.ac = 99
    return fighter


def _wolves_state(second_wolf: Position) -> GameState:
    srd = load_srd()
    wolf_1 = monster_to_character(srd.monsters["wolf"], "wolf_1", Position(x=1, y=0))
    wolf_2 = monster_to_character(srd.monsters["wolf"], "wolf_2", second_wolf)
    return GameState(
        encounter_id="roll_mode_test",
        characters={"thorin": _fighter(), "wolf_1": wolf_1, "wolf_2": wolf_2},
        turn_order=["wolf_1", "wolf_2", "thorin"],
        current_turn=0,
        round=1,
    )


def _bite(state: GameState, dice: list[int]) -> dict[str, Any]:
    action = ParsedAction(actor="wolf_1", verb="attack", target="thorin", raw_text="the wolf bites")
    resolve_action(state, action, _FixedRandom(dice))  # type: ignore[arg-type]
    return next(e for e in state.events if e.type == "attack_roll").payload


def _mock(state: GameState) -> None:
    apply_condition(
        state.characters["wolf_1"], Condition(name="mocked", duration_rounds=1, source="ganth")
    )


ADJACENT = Position(x=0, y=1)  # beside Thorin, so Pack Tactics applies to wolf_1
FAR = Position(x=9, y=9)


def test_a_plain_attack_reports_a_normal_roll_with_no_reasons() -> None:
    payload = _bite(_wolves_state(FAR), [7])
    assert payload["roll_mode"] == "normal"
    assert payload["advantage_reasons"] == [] and payload["disadvantage_reasons"] == []
    assert "dice_rolled" not in payload  # one die, nothing to show


def test_pack_tactics_is_reported_as_the_reason_for_advantage() -> None:
    payload = _bite(_wolves_state(ADJACENT), [5, 18])
    assert payload["roll_mode"] == "advantage"
    assert payload["advantage_reasons"] == ["Pack Tactics (ally next to target)"]
    assert payload["disadvantage_reasons"] == []
    assert payload["dice_rolled"] == [5, 18] and payload["natural"] == 18


def test_a_mocked_attacker_rolls_with_disadvantage_and_says_why() -> None:
    state = _wolves_state(FAR)
    _mock(state)
    payload = _bite(state, [5, 18])
    assert payload["roll_mode"] == "disadvantage"
    assert payload["disadvantage_reasons"] == ["attacker is mocked (Vicious Mockery)"]
    assert payload["dice_rolled"] == [5, 18] and payload["natural"] == 5  # the lower one counts


def test_advantage_and_disadvantage_cancel_into_a_normal_roll_but_both_are_listed() -> None:
    # The live case: a mocked wolf with a packmate next to its target.
    state = _wolves_state(ADJACENT)
    _mock(state)
    payload = _bite(state, [18])  # a single d20 - one value only, or the next draw would fail
    assert payload["roll_mode"] == "normal"
    assert payload["advantage_reasons"] == ["Pack Tactics (ally next to target)"]
    assert payload["disadvantage_reasons"] == ["attacker is mocked (Vicious Mockery)"]
    assert "dice_rolled" not in payload and payload["natural"] == 18


def test_a_spell_attack_reports_its_roll_mode_too() -> None:
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
    state = build_encounter_state(
        build_demo_encounter(),
        [wizard, _fighter()],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("elrond")
    state.characters["goblin_1"].position = Position(x=wizard.position.x + 4, y=wizard.position.y)
    state.characters["goblin_1"].ac = 99
    state.characters["elrond"].true_strike_advantage = True
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        item_or_spell="Fire Bolt",
        target="goblin_1",
        raw_text="I cast fire bolt",
    )
    resolve_action(state, action, _FixedRandom([4, 16]))  # type: ignore[arg-type]
    payload = next(e for e in state.events if e.type == "spell_cast").payload
    assert payload["roll_mode"] == "advantage"
    assert payload["advantage_reasons"] == ["True Strike"]
    assert payload["dice_rolled"] == [4, 16]


def test_the_boolean_condition_helpers_agree_with_their_reason_lists() -> None:
    attacker, target = _fighter(), _fighter()
    target.id = "dummy"
    for name in ("poisoned", "mocked"):
        apply_condition(attacker, Condition(name=name, duration_rounds=3, source="x"))
    apply_condition(target, Condition(name="prone", duration_rounds=3, source="x"))
    for distance, expected_adv, expected_dis in (
        (
            5,
            ["target is prone (melee range)"],
            ["attacker is poisoned", "attacker is mocked (Vicious Mockery)"],
        ),
        (
            30,
            [],
            [
                "attacker is poisoned",
                "attacker is mocked (Vicious Mockery)",
                "target is prone (ranged attack)",
            ],
        ),
    ):
        assert condition_attack_advantage_reasons(attacker, target, distance) == expected_adv
        assert condition_attack_disadvantage_reasons(attacker, target, distance) == expected_dis
        assert condition_attack_advantage(attacker, target, distance) is bool(expected_adv)
        assert condition_attack_disadvantage(attacker, target, distance) is bool(expected_dis)
