"""Issue #55, the cheap level 0-2 combat spells: Color Spray, Misty Step, Lesser Restoration,
Goodberry, Guidance, Resistance and Expeditious Retreat.

A wizard (Elrond, acting first) and a fighter against the demo encounter's two goblins. The
spells that aren't on a wizard's real list are poked onto `prepared_spells`, the way the other
spell tests do - what is under test is the engine's handling, not who may learn them."""

from __future__ import annotations

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import build_encounter_state
from src.engine.position import BattleMap, Position
from src.engine.resting import apply_long_rest
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import (
    TurnEngineError,
    _apply_damage_and_handle_downing,
    _save_dice,
    resolve_action,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


_SPELLS = ["color-spray", "misty-step", "lesser-restoration", "goodberry", "expeditious-retreat"]


def _state() -> GameState:
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
    wizard.prepared_spells += _SPELLS
    wizard.spell_slots = {1: 3, 2: 2}
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
    state = build_encounter_state(
        build_demo_encounter(),
        [wizard, fighter],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )
    state.battle_map = BattleMap(
        width=14, height=3, terrain=[["floor"] * 14 for _ in range(3)], spawn_points={}
    )
    return state


def _act(
    state: GameState,
    verb: str,
    *,
    spell: str | None = None,
    actor: str = "elrond",
    target: str | None = None,
    targets: list[str] | None = None,
    params: dict[str, object] | None = None,
    text: str = "x",
    rng: list[int] | None = None,
) -> None:
    action = ParsedAction(
        actor=actor,
        verb=verb,  # type: ignore[arg-type]
        item_or_spell=spell,
        target=target,
        targets=targets,
        params=params or {},
        raw_text=text,
    )
    resolve_action(state, action, _FixedRandom(rng or []))  # type: ignore[arg-type]


def _elrond(state: GameState) -> Character:
    return state.characters["elrond"]


# --- Color Spray ---


def test_color_spray_blinds_the_lowest_hp_creatures_the_pool_covers() -> None:
    state = _state()
    state.characters["goblin_1"].hp = 3
    state.characters["goblin_2"].hp = 20
    _act(state, "cast_spell", spell="Color Spray", targets=["goblin_1", "goblin_2"], rng=[1] * 6)
    assert has_condition(state.characters["goblin_1"], "blinded")  # 3 <= pool of 6
    assert not has_condition(state.characters["goblin_2"], "blinded")  # 20 > what's left
    assert _elrond(state).spell_slots[1] == 2


def test_color_spray_blinds_everyone_when_the_pool_is_big_enough() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Color Spray", targets=["goblin_1", "goblin_2"], rng=[10] * 6)
    for goblin in ("goblin_1", "goblin_2"):
        blinded = next(c for c in state.characters[goblin].conditions if c.name == "blinded")
        assert blinded.duration_rounds == 2  # until the end of the caster's next turn


def test_color_spray_skips_the_unconscious_and_the_already_blind() -> None:
    state = _state()
    apply_condition(state.characters["goblin_1"], Condition(name="blinded", duration_rounds=5))
    apply_condition(state.characters["goblin_2"], Condition(name="unconscious", duration_rounds=5))
    _act(state, "cast_spell", spell="Color Spray", targets=["goblin_1", "goblin_2"], rng=[10] * 6)
    blinded = state.characters["goblin_1"].conditions
    assert [c.duration_rounds for c in blinded if c.name == "blinded"] == [5]  # untouched


def test_color_spray_needs_a_target() -> None:
    state = _state()
    with pytest.raises(TurnEngineError, match="requires at least one target"):
        _act(state, "cast_spell", spell="Color Spray")
    assert _elrond(state).spell_slots[1] == 3


# --- Misty Step ---


def test_misty_step_teleports_to_a_square_without_ending_the_turn() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Misty Step", params={"to": {"x": 5, "y": 0}})
    assert _elrond(state).position == Position(x=5, y=0)
    assert _elrond(state).bonus_action_used is True
    assert state.turn_order[state.current_turn] == "elrond"  # a bonus action
    assert _elrond(state).spell_slots[2] == 1


def test_misty_step_provokes_no_opportunity_attack() -> None:
    state = _state()  # elrond (1,2) is next to goblin_2 (2,2)
    _act(state, "cast_spell", spell="Misty Step", params={"to": {"x": 0, "y": 0}})
    assert not any(e.type == "attack_roll" for e in state.events)


def test_misty_step_can_land_beside_a_named_creature() -> None:
    state = _state()
    state.characters["goblin_1"].position = Position(x=6, y=1)
    state.characters["goblin_2"].position = Position(x=13, y=2)
    _act(state, "cast_spell", spell="Misty Step", target="goblin_1")
    land = _elrond(state).position
    assert max(abs(land.x - 6), abs(land.y - 1)) == 1


def test_misty_step_lands_as_near_as_it_can_when_the_creature_is_out_of_reach() -> None:
    state = _state()
    state.characters["goblin_1"].position = Position(x=13, y=1)
    state.characters["goblin_2"].position = Position(x=13, y=2)
    start = _elrond(state).position.x
    _act(state, "cast_spell", spell="Misty Step", target="goblin_1")
    assert _elrond(state).position.x == start + 6  # 30 ft is six squares


@pytest.mark.parametrize(
    ("params", "match"),
    [
        ({"to": {"x": 12, "y": 0}}, "reaches at most 30"),
        ({"to": {"x": 20, "y": 0}}, "off the map"),
        ({"to": {"x": 2, "y": 1}}, "occupied"),
        ({}, "needs a destination"),
    ],
)
def test_a_misty_step_that_cannot_land_is_refused_and_costs_nothing(
    params: dict[str, object], match: str
) -> None:
    state = _state()
    with pytest.raises(TurnEngineError, match=match):
        _act(state, "cast_spell", spell="Misty Step", params=params)
    assert _elrond(state).spell_slots[2] == 2
    assert _elrond(state).bonus_action_used is False


def test_misty_step_refuses_a_wall_and_frees_a_grappled_caster() -> None:
    state = _state()
    assert state.battle_map is not None
    state.battle_map.terrain[0][4] = "wall"
    with pytest.raises(TurnEngineError, match="wall"):
        _act(state, "cast_spell", spell="Misty Step", params={"to": {"x": 4, "y": 0}})
    apply_condition(_elrond(state), Condition(name="grappled", source="goblin_2"))
    _act(state, "cast_spell", spell="Misty Step", params={"to": {"x": 5, "y": 0}})
    assert not has_condition(_elrond(state), "grappled")


# --- Lesser Restoration ---


def test_lesser_restoration_ends_a_condition_on_an_ally() -> None:
    state = _state()
    thorin = state.characters["thorin"]
    thorin.position = Position(x=0, y=2)  # next to elrond
    apply_condition(thorin, Condition(name="poisoned", duration_rounds=10))
    _act(state, "cast_spell", spell="Lesser Restoration", target="thorin")
    assert not has_condition(thorin, "poisoned")
    assert _elrond(state).spell_slots[2] == 1
    assert any(
        e.type == "condition_removed" and e.payload["reason"] == "cured" for e in state.events
    )


def test_the_casters_words_pick_which_condition_goes() -> None:
    state = _state()
    for name in ("poisoned", "blinded"):
        apply_condition(_elrond(state), Condition(name=name, duration_rounds=10))
    _act(state, "cast_spell", spell="Lesser Restoration", text="cure the poison")
    assert not has_condition(_elrond(state), "poisoned")
    assert has_condition(_elrond(state), "blinded")


def test_with_no_hint_the_most_disabling_condition_goes_first() -> None:
    state = _state()
    for name in ("poisoned", "blinded"):
        apply_condition(_elrond(state), Condition(name=name, duration_rounds=10))
    _act(state, "cast_spell", spell="Lesser Restoration")
    assert not has_condition(_elrond(state), "blinded")
    assert has_condition(_elrond(state), "poisoned")


def test_lesser_restoration_with_nothing_to_cure_or_an_enemy_costs_nothing() -> None:
    state = _state()
    with pytest.raises(TurnEngineError, match="no condition"):
        _act(state, "cast_spell", spell="Lesser Restoration")
    with pytest.raises(TurnEngineError, match="not an ally"):
        _act(state, "cast_spell", spell="Lesser Restoration", target="goblin_1")
    assert _elrond(state).spell_slots[2] == 2


# --- Goodberry ---


def test_goodberry_conjures_ten_berries_and_eating_one_heals_a_hit_point() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Goodberry")
    assert _elrond(state).inventory.count("goodberry") == 10
    assert _elrond(state).spell_slots[1] == 2
    _elrond(state).hp = 3
    state.current_turn = state.turn_order.index("elrond")
    _act(state, "use_item", spell="goodberry")
    assert _elrond(state).hp == 4
    assert _elrond(state).inventory.count("goodberry") == 9


def test_a_berry_can_be_handed_to_an_adjacent_ally() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Goodberry")
    thorin = state.characters["thorin"]
    thorin.position = Position(x=0, y=2)
    thorin.hp = thorin.max_hp - 3
    before = thorin.hp
    state.current_turn = state.turn_order.index("elrond")
    _act(state, "use_item", spell="a goodberry", target="thorin")
    assert thorin.hp == before + 1


def test_eating_a_berry_you_do_not_have_is_refused() -> None:
    state = _state()
    with pytest.raises(TurnEngineError, match="no goodberry"):
        _act(state, "use_item", spell="goodberry")


def test_a_long_rest_clears_the_berries() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Goodberry")
    _elrond(state).inventory.append("dagger")
    apply_long_rest([_elrond(state)])
    assert "goodberry" not in _elrond(state).inventory
    assert "dagger" in _elrond(state).inventory


# --- Guidance and Resistance ---


def test_guidance_is_a_cantrip_cast_on_yourself_by_default() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Guidance")
    assert has_condition(_elrond(state), "guidance")
    assert _elrond(state).concentrating_on == "Guidance"
    assert _elrond(state).spell_slots == {1: 3, 2: 2}


def test_guidance_adds_a_d4_to_the_next_ability_check_and_is_then_spent() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Guidance")
    state.current_turn = state.turn_order.index("elrond")
    _act(state, "skill_check", params={"skill": "arcana"}, rng=[3, 12])  # the d4, then the d20
    check = next(e for e in state.events if e.type == "skill_check")
    assert ("guidance (1d4)", 3) in [tuple(x) for x in check.payload["modifier_breakdown"]]
    assert check.payload["roll_total"] == 12 + 3 + 2 + 2  # d20 + d4 + INT +2 + proficiency +2
    assert not has_condition(_elrond(state), "guidance")
    assert _elrond(state).concentrating_on is None  # the spell had nothing left to sustain


def test_a_check_without_guidance_rolls_no_extra_die() -> None:
    state = _state()
    _act(state, "skill_check", params={"skill": "arcana"}, rng=[12])  # a lone d20
    assert next(e for e in state.events if e.type == "skill_check").payload["natural"] == 12


def test_resistance_adds_a_d4_to_a_saving_throw_and_is_then_spent() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Resistance")
    assert has_condition(_elrond(state), "spell_resistance")
    bonus, entries = _save_dice(state, _elrond(state), _FixedRandom([3]))  # type: ignore[arg-type]
    assert bonus == 3 and ("resistance (1d4)", 3) in entries
    assert not has_condition(_elrond(state), "spell_resistance")
    assert _save_dice(state, _elrond(state), _FixedRandom([]))[0] == 0  # type: ignore[arg-type]


def test_resistance_can_save_a_concentration_check() -> None:
    state = _state()
    elrond, goblin = _elrond(state), state.characters["goblin_1"]
    elrond.concentrating_on = "Bless"
    elrond.hp = elrond.max_hp = 100  # survive the hit; this is about the concentration save
    apply_condition(elrond, Condition(name="spell_resistance", duration_rounds=10, source="elrond"))
    # 12 damage -> DC 10. CON +1: a d20 of 7 alone fails (8), with the d4 of 3 it passes (11).
    _apply_damage_and_handle_downing(
        state,
        goblin,
        elrond,
        12,
        "slashing",
        _FixedRandom([3, 7]),  # type: ignore[arg-type]
        load_srd(),
    )
    assert elrond.concentrating_on == "Bless"


# --- Expeditious Retreat ---


def test_expeditious_retreat_makes_dash_a_bonus_action() -> None:
    state = _state()
    _act(state, "cast_spell", spell="Expeditious Retreat")
    assert has_condition(_elrond(state), "expeditious_retreat")
    assert _elrond(state).concentrating_on == "Expeditious Retreat"
    _elrond(state).position = Position(x=0, y=0)
    _elrond(state).bonus_action_used = False  # the next turn
    path = [{"x": x, "y": 0} for x in range(1, 9)]  # 40 ft with 30 ft of speed: a real dash
    state.current_turn = state.turn_order.index("elrond")
    _act(state, "dash", params={"path": path})
    assert _elrond(state).position == Position(x=8, y=0)
    assert _elrond(state).bonus_action_used is True
    assert state.turn_order[state.current_turn] == "elrond"  # the action is still there


def test_without_the_spell_a_dash_still_ends_the_turn() -> None:
    state = _state()
    _elrond(state).position = Position(x=0, y=0)
    path = [{"x": x, "y": 0} for x in range(1, 9)]
    _act(state, "dash", params={"path": path})
    assert state.turn_order[state.current_turn] != "elrond"


def test_a_second_dash_in_the_same_turn_is_a_normal_one() -> None:
    state = _state()
    apply_condition(_elrond(state), Condition(name="expeditious_retreat", duration_rounds=100))
    _elrond(state).position = Position(x=0, y=0)
    state.current_turn = state.turn_order.index("elrond")
    _act(state, "dash", params={"path": [{"x": x, "y": 0} for x in range(1, 9)]})  # 40 ft: a dash
    assert state.turn_order[state.current_turn] == "elrond"
    _act(state, "dash", params={"path": [{"x": x, "y": 0} for x in range(9, 13)]})  # 20 ft more
    assert state.turn_order[state.current_turn] != "elrond"  # the bonus action was already spent


def test_misty_step_prefers_a_named_creature_over_a_stray_square() -> None:
    """The model fills `to` with the named creature's own (occupied) square; the creature wins."""
    state = _state()
    state.characters["goblin_1"].position = Position(x=6, y=1)
    state.characters["goblin_2"].position = Position(x=13, y=2)
    _act(
        state, "cast_spell", spell="Misty Step", target="goblin_1", params={"to": {"x": 6, "y": 1}}
    )
    land = _elrond(state).position
    assert land != Position(x=6, y=1) and max(abs(land.x - 6), abs(land.y - 1)) == 1


def test_a_stray_target_on_a_self_only_spell_is_aimed_back_at_the_caster() -> None:
    from src.graph.nodes.intent_parser import _drop_stray_target_on_self_spell

    state = _state()
    stray = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        item_or_spell="Expeditious Retreat",
        target="thorin",
        raw_text="I cast expeditious retreat",
    )
    fixed = _drop_stray_target_on_self_spell(stray, state, "I cast expeditious retreat")
    assert fixed.target == "elrond"
    # the player really did name Thorin: that is a rules error to report, not to paper over
    named = _drop_stray_target_on_self_spell(stray, state, "I cast expeditious retreat on Thorin")
    assert named.target == "thorin"
    # a touch spell, or one with a real target, is left alone
    mage_armor = stray.model_copy(update={"item_or_spell": "Mage Armor"})
    assert _drop_stray_target_on_self_spell(mage_armor, state, "x").target == "thorin"


def test_only_the_spells_that_need_it_carry_a_usage_hint_in_the_options_block() -> None:
    from src.graph.nodes.actor_options import spell_option_line

    srd = load_srd()
    assert "appear next to" in spell_option_line("misty-step", srd)
    assert "targets" in spell_option_line("color-spray", srd)
    assert ";" not in spell_option_line("goodberry", srd)


def test_a_stray_target_on_guidance_is_aimed_back_at_the_caster_too() -> None:
    from src.graph.nodes.intent_parser import _drop_stray_target_on_self_spell

    state = _state()
    stray = ParsedAction(
        actor="elrond", verb="cast_spell", item_or_spell="Guidance", target="thorin", raw_text="x"
    )
    assert _drop_stray_target_on_self_spell(stray, state, "I cast guidance").target == "elrond"
    assert _drop_stray_target_on_self_spell(stray, state, "guidance on Thorin").target == "thorin"
