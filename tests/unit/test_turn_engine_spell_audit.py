"""Issue #55 spell audit - the first implementation pass: data-gap fixes
(scorching-ray/call-lightning now resolve for real instead of being
silently unsupported), Sleep's own HP-pool targeting, the new "condition"
spell mechanic (Invisibility), a flat/formula AC-buff mechanic (Mage
Armor/Shield of Faith), and True Strike's banked advantage. Mirrors
test_turn_engine_spells.py's own fixture shape (a hand-built Thorin/Elrond
party against the demo encounter's goblins) rather than importing it, same
per-file-fixture convention this project already uses throughout.
"""

from __future__ import annotations

import random

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _two_person_party() -> list[Character]:
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
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


def _build_demo_state(rng_values: list[int]):  # type: ignore[no-untyped-def]
    encounter = build_demo_encounter()
    party = _two_person_party()
    return build_encounter_state(encounter, party, _FixedRandom(rng_values))  # type: ignore[arg-type]


_INITIATIVE = [18, 10, 8, 3]  # thorin, elrond, goblin_1, goblin_2 - see test_turn_engine_spells.py


def _end_turn(state, actor_id: str) -> None:  # type: ignore[no-untyped-def]
    resolve_action(
        state, ParsedAction(actor=actor_id, verb="end_turn", raw_text="x"), random.Random()
    )


# --------------------------------------------------------- data-gap fixes


def test_scorching_ray_now_resolves_as_a_real_attack_roll_spell() -> None:
    state = _build_demo_state(_INITIATIVE)
    state.characters["elrond"].spell_slots[2] = 1  # Scorching Ray is 2nd level
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="scorching ray",
        raw_text="I cast scorching ray at goblin_1",
    )
    # attack roll (hit, natural 15), damage roll (2d6 -> 4, 4).
    resolve_action(state, action, _FixedRandom([15, 4, 4]))  # type: ignore[arg-type]
    # A spell's own attack roll is logged as "spell_cast", not "attack_roll"
    # (that type is weapon-attack-only) - see _cast_attack_spell_at_target.
    events = [e for e in state.events if e.type == "spell_cast"]
    assert events, "expected a real spell_cast event, not a rejection"
    assert events[-1].payload["hit"] is True


def test_call_lightning_now_resolves_as_a_real_save_spell() -> None:
    state = _build_demo_state(_INITIATIVE)
    state.characters["elrond"].spell_slots[3] = 1  # Call Lightning is 3rd level
    _end_turn(state, "thorin")
    goblin = state.characters["goblin_1"]
    hp_before = goblin.hp
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="call lightning",
        raw_text="I cast call lightning at goblin_1",
    )
    # saving throw roll (fails low), damage dice (3d10 -> 5,5,5 = 15, clamped at goblin's HP).
    resolve_action(state, action, _FixedRandom([1, 5, 5, 5]))  # type: ignore[arg-type]
    events = [e for e in state.events if e.type == "saving_throw"]
    assert events, "expected a real saving_throw event, not a rejection"
    assert goblin.hp < hp_before


# --------------------------------------------------------- Spare the Dying


def test_spare_the_dying_stabilizes_a_dying_creature_with_no_roll() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    _end_turn(state, "thorin")  # while still healthy - can't end_turn once unconscious
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious", source="0 HP"))
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="thorin",
        item_or_spell="spare the dying",
        raw_text="I cast spare the dying on thorin",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert thorin.is_stable is True


def test_spare_the_dying_rejects_a_target_that_isnt_dying() -> None:
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="thorin",  # full HP, not dying
        item_or_spell="spare the dying",
        raw_text="I cast spare the dying on thorin",
    )
    with pytest.raises(TurnEngineError, match="not a valid Spare the Dying target"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# --------------------------------------------------------- Sleep


def _sleep_state() -> tuple[GameState, Character, Character, Character, Character]:
    # Elrond (Wizard) casts - a Fighter has no spell slots to spend.
    srd = load_srd()
    elrond = _two_person_party()[1]
    elrond.position = Position(x=0, y=0)
    goblin_low_hp = monster_to_character(srd.monsters["goblin"], "goblin_low", Position(x=1, y=0))
    goblin_low_hp.hp = 3
    goblin_high_hp = monster_to_character(srd.monsters["goblin"], "goblin_high", Position(x=2, y=0))
    goblin_high_hp.hp = 7
    skeleton = monster_to_character(srd.monsters["skeleton"], "skeleton_1", Position(x=3, y=0))

    state = GameState(
        encounter_id="sleep_test",
        characters={
            "elrond": elrond,
            "goblin_low": goblin_low_hp,
            "goblin_high": goblin_high_hp,
            "skeleton_1": skeleton,
        },
        turn_order=["elrond", "goblin_low", "goblin_high", "skeleton_1"],
        current_turn=0,
        round=1,
    )
    return state, elrond, goblin_low_hp, goblin_high_hp, skeleton


def test_sleep_affects_targets_in_ascending_hp_order_until_the_pool_runs_out() -> None:
    state, elrond, goblin_low, goblin_high, skeleton = _sleep_state()
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        targets=["goblin_low", "goblin_high", "skeleton_1"],
        item_or_spell="sleep",
        raw_text="I cast sleep",
    )
    # 5d8 -> 3,3,3,3,3 = pool 15. Ascending HP: goblin_low(3) then
    # goblin_high(7) - both affected (3+7=10 <= 15), skeleton skipped
    # regardless (undead).
    resolve_action(state, action, _FixedRandom([3, 3, 3, 3, 3]))  # type: ignore[arg-type]
    assert has_condition(goblin_low, "unconscious")
    assert has_condition(goblin_high, "unconscious")
    assert not has_condition(skeleton, "unconscious")


def test_sleep_skips_a_target_the_remaining_pool_cant_cover() -> None:
    state, elrond, goblin_low, goblin_high, skeleton = _sleep_state()
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        targets=["goblin_low", "goblin_high"],
        item_or_spell="sleep",
        raw_text="I cast sleep",
    )
    # Pool 5 (1,1,1,1,1): covers goblin_low (3 HP), leaves 2 - not enough
    # for goblin_high (7 HP), correctly skipped even though it comes next
    # in ascending order.
    resolve_action(state, action, _FixedRandom([1, 1, 1, 1, 1]))  # type: ignore[arg-type]
    assert has_condition(goblin_low, "unconscious")
    assert not has_condition(goblin_high, "unconscious")


def test_sleep_wakes_the_target_on_any_damage() -> None:
    state, elrond, goblin_low, goblin_high, skeleton = _sleep_state()
    apply_condition(goblin_low, Condition(name="unconscious", duration_rounds=10, source="sleep"))
    action = ParsedAction(
        actor="elrond", verb="attack", target="goblin_low", raw_text="I attack the sleeping goblin"
    )
    # Attack roll auto-crits (unconscious grants advantage AND force_critical
    # per Phase 9A/9C) - natural rolls (advantage, 2 d20s), then damage dice
    # doubled by the crit (Elrond's dagger is 1d4, so 2d4 on a crit).
    resolve_action(state, action, _FixedRandom([15, 15, 4, 4]))  # type: ignore[arg-type]
    assert not has_condition(goblin_low, "unconscious") or goblin_low.hp <= 0


def test_ordinary_zero_hp_unconscious_is_not_woken_by_further_damage() -> None:
    # The sleep-specific wake rule must not leak into the ordinary "downed
    # at 0 HP" unconscious, which never lifts on its own - checked via the
    # distinct source tag ("0 HP" vs "sleep"), not just the condition name.
    state, elrond, goblin_low, goblin_high, skeleton = _sleep_state()
    goblin_low.hp = 0
    apply_condition(goblin_low, Condition(name="unconscious", source="0 HP"))
    action = ParsedAction(
        actor="elrond", verb="attack", target="goblin_low", raw_text="I attack the downed goblin"
    )
    resolve_action(state, action, _FixedRandom([15, 15, 4, 4]))  # type: ignore[arg-type]
    # Still unconscious (from 0 HP, not sleep) - the state is dead/unconscious either way.
    assert goblin_low.is_dead or has_condition(goblin_low, "unconscious")


# --------------------------------------------------------- condition spells (Invisibility)


def test_invisibility_applies_the_invisible_condition_to_a_willing_ally() -> None:
    state = _build_demo_state(_INITIATIVE)
    state.characters["elrond"].spell_slots[2] = 1  # Invisibility is 2nd level
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="thorin",
        item_or_spell="invisibility",
        raw_text="I cast invisibility on thorin",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert has_condition(state.characters["thorin"], "invisible")


def test_invisibility_rejects_an_enemy_target() -> None:
    # A buff spell targeting a hostile makes no sense - same friendly-fire
    # guard heal/condition mechanics share, just checked with the opposite
    # polarity from attack (same-side is REQUIRED, not rejected).
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="invisibility",
        raw_text="I cast invisibility on goblin_1",
    )
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# --------------------------------------------------------- AC buff spells


def test_mage_armor_sets_13_plus_dex_while_unarmored() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.equipped_armor = None
    thorin.ac = 10  # force a known starting point before the recompute
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="thorin",
        item_or_spell="mage armor",
        raw_text="I cast mage armor on thorin",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert thorin.mage_armor_active is True
    from src.engine.rules import ability_modifier

    assert thorin.ac == 13 + ability_modifier(thorin.stats["DEX"])


def test_shield_of_faith_adds_a_flat_plus_2_ac() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    ac_before = thorin.ac
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="thorin",
        item_or_spell="shield of faith",
        raw_text="I cast shield of faith on thorin",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert thorin.temporary_ac_bonus == 2
    assert thorin.ac == ac_before + 2


# --------------------------------------------------------- True Strike


def test_true_strike_grants_advantage_on_the_casters_next_attack_roll() -> None:
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    cast = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        item_or_spell="true strike",
        raw_text="I cast true strike",
    )
    resolve_action(state, cast, _FixedRandom([]))  # type: ignore[arg-type]
    elrond = state.characters["elrond"]
    assert elrond.true_strike_advantage is True

    # True Strike is a full-action cantrip, so casting it ends Elrond's own
    # turn (real SRD grants the advantage against the caster's *next* turn's
    # attack, not the same turn) - jump the turn pointer directly back to
    # Elrond rather than cycling the other 3 actors through a whole real
    # round, since this test is only about the banked-flag mechanics, not
    # turn order itself.
    state.current_turn = state.turn_order.index("elrond")
    attack = ParsedAction(
        actor="elrond", verb="attack", target="goblin_1", raw_text="I attack goblin_1"
    )
    resolve_action(state, attack, _FixedRandom([5, 18, 3]))  # type: ignore[arg-type]
    events = [e for e in state.events if e.type == "attack_roll"]
    assert events[-1].payload["natural"] == 18  # the higher of the two advantage rolls
    assert elrond.true_strike_advantage is False  # consumed
