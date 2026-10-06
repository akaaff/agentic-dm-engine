"""Issue #84: the Hide action and the "hidden" condition it grants - a Stealth
check against the best passive Perception among hostiles that could notice,
advantage (and Sneak Attack) on the hidden creature's first attack, and
disadvantage on attacks against it. The dice are fixed, so every total is
hand-computable: Fenwick is a Halfling Rogue (DEX 15+2 -> +3) proficient in
Stealth (+2), so his Stealth modifier is +5; a goblin's passive Perception is
9 and a wolf's 13 (the SRD stat blocks' own figures)."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _make_state(*characters: Character) -> GameState:
    return GameState(
        encounter_id="hide_test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10,
            height=10,
            terrain=[["floor"] * 10 for _ in range(10)],
            spawn_points={},
        ),
    )


def _rogue(level: int = 1) -> Character:
    rogue = create_character(
        character_id="fenwick",
        name="Fenwick",
        race_index="halfling",
        class_index="rogue",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 15, "CON": 12, "INT": 10, "WIS": 13, "CHA": 14},
        chosen_skills=[
            "skill-stealth",
            "skill-sleight-of-hand",
            "skill-acrobatics",
            "skill-deception",
        ],
        chosen_equipment=["shortsword"],
        position=Position(x=0, y=0),
    )
    rogue.level = level
    return rogue


def _monster(index: str, char_id: str, position: Position) -> Character:
    monster = monster_to_character(load_srd().monsters[index], char_id, position)
    monster.hp = monster.max_hp = 100
    return monster


def _hide(*, via_cunning_action: bool = False) -> ParsedAction:
    if via_cunning_action:
        return ParsedAction(
            actor="fenwick",
            verb="cunning_action",
            params={"action": "hide"},
            raw_text="I duck into the shadows",
        )
    return ParsedAction(actor="fenwick", verb="hide", raw_text="I hide")


def test_a_successful_stealth_check_makes_the_actor_hidden_and_ends_the_turn() -> None:
    # d20 10 + 5 = 15 against the goblin's passive Perception 9.
    fenwick = _rogue()
    goblin = _monster("goblin", "goblin_1", Position(x=0, y=4))
    state = _make_state(fenwick, goblin)
    resolve_action(state, _hide(), _FixedRandom([10]))  # type: ignore[arg-type]

    check = next(e for e in state.events if e.type == "skill_check")
    assert check.payload["skill"] == "stealth"
    assert check.payload["dc"] == 9
    assert check.payload["roll_total"] == 15
    assert check.payload["success"] is True
    assert has_condition(fenwick, "hidden")
    assert any(
        e.type == "condition_applied" and e.payload["condition"] == "hidden" for e in state.events
    )
    assert state.turn_order[state.current_turn] == "goblin_1"  # Hide is the action


def test_a_failed_stealth_check_leaves_the_actor_visible() -> None:
    # d20 3 + 5 = 8 against passive Perception 9.
    fenwick = _rogue()
    state = _make_state(fenwick, _monster("goblin", "goblin_1", Position(x=0, y=4)))
    resolve_action(state, _hide(), _FixedRandom([3]))  # type: ignore[arg-type]

    check = next(e for e in state.events if e.type == "skill_check")
    assert check.payload["success"] is False
    assert not has_condition(fenwick, "hidden")


def test_the_dc_is_the_best_passive_perception_among_the_hostiles() -> None:
    # The wolf's 13 beats the goblin's 9: a total of 12 now fails.
    fenwick = _rogue()
    state = _make_state(
        fenwick,
        _monster("goblin", "goblin_1", Position(x=0, y=4)),
        _monster("wolf", "wolf_1", Position(x=4, y=4)),
    )
    resolve_action(state, _hide(), _FixedRandom([7]))  # type: ignore[arg-type]

    check = next(e for e in state.events if e.type == "skill_check")
    assert check.payload["dc"] == 13
    assert check.payload["success"] is False


def test_a_hostile_that_cannot_notice_does_not_raise_the_dc() -> None:
    fenwick = _rogue()
    wolf = _monster("wolf", "wolf_1", Position(x=4, y=4))
    apply_condition(wolf, Condition(name="unconscious", source="sleep"))
    state = _make_state(fenwick, _monster("goblin", "goblin_1", Position(x=0, y=4)), wolf)
    resolve_action(state, _hide(), _FixedRandom([10]))  # type: ignore[arg-type]

    assert next(e for e in state.events if e.type == "skill_check").payload["dc"] == 9


def test_cannot_hide_from_a_hostile_standing_next_to_you() -> None:
    fenwick = _rogue()
    state = _make_state(fenwick, _monster("goblin", "goblin_1", Position(x=0, y=1)))
    with pytest.raises(TurnEngineError, match="goblin_1 is right next to them"):
        resolve_action(state, _hide(), _FixedRandom([]))  # type: ignore[arg-type]
    assert state.events == []  # nothing rolled, nothing changed


def test_armor_with_stealth_disadvantage_rolls_two_dice() -> None:
    # Padded armor has stealth disadvantage even for a proficient wearer:
    # [18, 2] keeps the 2 -> 7 against 9.
    fenwick = _rogue()
    fenwick.inventory.append("padded-armor")
    fenwick.equipped_armor = "padded-armor"
    state = _make_state(fenwick, _monster("goblin", "goblin_1", Position(x=0, y=4)))
    resolve_action(state, _hide(), _FixedRandom([18, 2]))  # type: ignore[arg-type]

    check = next(e for e in state.events if e.type == "skill_check")
    assert check.payload["natural"] == 2
    assert check.payload["success"] is False


def test_cunning_action_hide_is_a_bonus_action_that_keeps_the_turn_open() -> None:
    fenwick = _rogue(level=2)
    state = _make_state(fenwick, _monster("goblin", "goblin_1", Position(x=0, y=4)))
    resolve_action(state, _hide(via_cunning_action=True), _FixedRandom([10]))  # type: ignore[arg-type]

    assert has_condition(fenwick, "hidden")
    assert fenwick.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "fenwick"  # still his turn


def test_cunning_action_hide_needs_rogue_level_two() -> None:
    fenwick = _rogue(level=1)
    state = _make_state(fenwick, _monster("goblin", "goblin_1", Position(x=0, y=4)))
    with pytest.raises(TurnEngineError, match="doesn't have Cunning Action"):
        resolve_action(state, _hide(via_cunning_action=True), _FixedRandom([]))  # type: ignore[arg-type]


def test_a_hidden_attacker_has_advantage_triggers_sneak_attack_and_is_revealed() -> None:
    # Two d20s [15, 3] -> keeps 15 (advantage); shortsword die 4 + DEX 3 = 7,
    # plus the Sneak Attack die 5 = 12 - the same numbers as the Help-
    # advantage fixture in test_turn_engine_class_features.
    fenwick = _rogue()
    apply_condition(fenwick, Condition(name="hidden", source="hide"))
    goblin = _monster("goblin", "goblin_1", Position(x=0, y=1))
    state = _make_state(fenwick, goblin)
    attack = ParsedAction(
        actor="fenwick",
        verb="attack",
        target="goblin_1",
        item_or_spell="shortsword",
        raw_text="I stab the goblin from the shadows",
    )
    resolve_action(state, attack, _FixedRandom([15, 3, 4, 5]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 15
    assert attack_event.payload.get("sneak_attack_damage") == 5
    assert not has_condition(fenwick, "hidden")
    removed = next(e for e in state.events if e.type == "condition_removed")
    assert removed.payload["condition"] == "hidden"
    assert state.events.index(removed) < state.events.index(attack_event)


def test_an_attack_against_a_hidden_target_has_disadvantage() -> None:
    # The goblin (to act first) rolls two d20s, [18, 3], and keeps the 3.
    fenwick = _rogue()
    apply_condition(fenwick, Condition(name="hidden", source="hide"))
    goblin = _monster("goblin", "goblin_1", Position(x=0, y=1))
    state = _make_state(goblin, fenwick)
    attack = ParsedAction(
        actor="goblin_1",
        verb="attack",
        target="fenwick",
        item_or_spell="Scimitar",
        raw_text="the goblin lunges at where it heard him",
    )
    resolve_action(state, attack, _FixedRandom([18, 3]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 3
    assert has_condition(fenwick, "hidden")  # being attacked doesn't reveal you
