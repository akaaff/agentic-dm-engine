"""Phase 9D: save-based, no-roll (heal), and multi-target spells, plus
concentration. Extends Day 14's cast_spell (attack-roll spells only, see
test_turn_engine_day14.py, whose existing cases keep passing unchanged -
this is a refactor of shared dispatch code, not just an addition) with the
other two SRD spell mechanics and the ability to hit more than one target
in a single cast.
"""

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character
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
        chosen_prepared_spells=["magic-missile", "burning-hands", "mage-armor"],
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


def _build_demo_state(rng_values: list[int]):  # type: ignore[no-untyped-def]
    encounter = build_demo_encounter()
    party = _two_person_party()
    return build_encounter_state(encounter, party, _FixedRandom(rng_values))  # type: ignore[arg-type]


# turn_order for [18, 10, 8, 3] is always thorin, elrond, goblin_1, goblin_2
# (established in test_turn_engine.py). Elrond (party_2, (1,2)) is exactly
# 5ft (chebyshev 1 square) from both goblin_1 (2,1) and goblin_2 (2,2) - the
# same adjacency test_turn_engine_day14.py's melee tests rely on, handy here
# since Burning Hands' SRD range is "Self" (falls back to 5ft, see
# rules.spell_range_feet).
_INITIATIVE = [18, 10, 8, 3]


# --------------------------------------------------------- save-based spells


def test_save_spell_deals_full_damage_on_a_failed_save() -> None:
    # Elrond (Wizard, INT15 -> mod2, prof2): Burning Hands save DC =
    # 8+2+2=12. goblin_1 (DEX14 -> mod2, no save proficiency): natural 4 ->
    # total 6 < 12 -> fails. Damage 3d6, naturals [5,5,5] -> 15, full
    # (no half) since the save failed - kills the goblin (7 HP) outright.
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("elrond")

    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="burning hands",
        raw_text="I cast burning hands at the goblin",
    )
    resolve_action(state, action, _FixedRandom([4, 5, 5, 5]))  # type: ignore[arg-type]

    save_event = next(e for e in state.events if e.type == "saving_throw")
    assert save_event.payload["kind"] == "spell_save"
    assert save_event.payload["dc"] == 12
    assert save_event.payload["success"] is False
    spell_event = next(e for e in state.events if e.type == "spell_cast")
    assert spell_event.payload["damage"] == 15
    assert state.characters["goblin_1"].is_dead is True
    assert state.characters["elrond"].spell_slots[1] == 1  # started at 2


def test_save_spell_deals_half_damage_rounded_down_on_a_successful_save() -> None:
    # Same DC (12). natural 18 -> total 20 >= 12 -> succeeds. Damage 3d6,
    # naturals [3,3,3] -> 9 (odd) -> half rounded down (plain //) is 4, not
    # 4.5 or 5 - proves the rounding direction, not just "some" halving.
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("elrond")

    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="burning hands",
        raw_text="I cast burning hands at the goblin",
    )
    resolve_action(state, action, _FixedRandom([18, 3, 3, 3]))  # type: ignore[arg-type]

    save_event = next(e for e in state.events if e.type == "saving_throw")
    assert save_event.payload["success"] is True
    spell_event = next(e for e in state.events if e.type == "spell_cast")
    assert spell_event.payload["damage"] == 4
    assert state.characters["goblin_1"].hp == 3  # 7 - 4


# ---------------------------------------------------------------- no-roll heal


def test_heal_spell_restores_the_expected_hp() -> None:
    mira = create_character(
        character_id="mira",
        name="Mira",
        race_index="human",
        class_index="cleric",
        background_index="acolyte",
        base_ability_scores={"STR": 13, "DEX": 10, "CON": 14, "INT": 8, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-medicine", "skill-religion"],
        chosen_prepared_spells=["cure-wounds", "bless", "healing-word", "shield-of-faith"],
    )
    encounter = build_demo_encounter()
    state = build_encounter_state(encounter, [mira], _FixedRandom([20, 5, 5]))  # type: ignore[arg-type]
    state.characters["mira"].hp = 1  # well below max_hp, no clamping expected

    # Cure Wounds (1st level): "1d8 + MOD" where MOD is the caster's
    # spellcasting ability modifier - Mira (human, WIS15 -> 16 after the
    # human racial +1 -> mod3). Natural 6 -> 6+3=9 healed, no roll to hit,
    # no d20 at all.
    action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="mira",
        item_or_spell="cure wounds",
        raw_text="I cast cure wounds on myself",
    )
    resolve_action(state, action, _FixedRandom([6]))  # type: ignore[arg-type]

    assert state.characters["mira"].hp == 10  # 1 + 9
    assert state.characters["mira"].spell_slots[1] == 1  # a leveled spell still costs a slot
    heal_event = next(e for e in state.events if e.type == "hp_change")
    assert heal_event.payload["amount"] == 9
    assert heal_event.payload["target"] == "mira"


def test_heal_spell_is_capped_at_max_hp() -> None:
    mira = create_character(
        character_id="mira",
        name="Mira",
        race_index="human",
        class_index="cleric",
        background_index="acolyte",
        base_ability_scores={"STR": 13, "DEX": 10, "CON": 14, "INT": 8, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-medicine", "skill-religion"],
        chosen_prepared_spells=["cure-wounds", "bless", "healing-word", "shield-of-faith"],
    )
    encounter = build_demo_encounter()
    state = build_encounter_state(encounter, [mira], _FixedRandom([20, 5, 5]))  # type: ignore[arg-type]
    mira = state.characters["mira"]
    mira.hp = mira.max_hp - 2  # only 2 points of room; 6+3=9 would overheal

    action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="mira",
        item_or_spell="cure wounds",
        raw_text="I cast cure wounds on myself",
    )
    resolve_action(state, action, _FixedRandom([6]))  # type: ignore[arg-type]

    assert mira.hp == mira.max_hp  # capped, not max_hp + 7


# ------------------------------------------------------------- multi-target


def test_multi_target_spell_hits_three_targets_independently() -> None:
    # A 3rd goblin, added directly to the demo encounter's state (which only
    # spawns two) so a single cast can hit three independent targets. Placed
    # 5ft from Elrond, same as goblin_1/goblin_2 already are.
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("elrond")
    srd = load_srd()
    goblin_3 = monster_to_character(srd.monsters["goblin"], "goblin_3", Position(x=0, y=2))
    state.characters["goblin_3"] = goblin_3
    state.turn_order.append("goblin_3")

    # DC 12 again (same caster/spell as above). Per-target RNG groups are
    # [save_natural, dmg_natural, dmg_natural, dmg_natural]:
    #   goblin_1: natural 5  -> total 7  < 12 -> fail   -> 3d6=[3,3,3]=9  (full, dies: 7-9)
    #   goblin_2: natural 15 -> total 17 >= 12 -> success -> 3d6=[4,4,4]=12 (half=6: 7-6=1)
    #   goblin_3: natural 9  -> total 11 < 12 -> fail   -> 3d6=[2,2,2]=6  (full: 7-6=1)
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        targets=["goblin_1", "goblin_2", "goblin_3"],
        item_or_spell="burning hands",
        raw_text="I cast burning hands at all three goblins",
    )
    resolve_action(
        state,
        action,
        _FixedRandom([5, 3, 3, 3, 15, 4, 4, 4, 9, 2, 2, 2]),  # type: ignore[arg-type]
    )

    save_events = [e for e in state.events if e.type == "saving_throw"]
    assert len(save_events) == 3
    assert [e.payload["target"] for e in save_events] == ["goblin_1", "goblin_2", "goblin_3"]
    assert [e.payload["success"] for e in save_events] == [False, True, False]

    assert state.characters["goblin_1"].is_dead is True
    assert state.characters["goblin_2"].hp == 1
    assert state.characters["goblin_3"].hp == 1
    # One spell slot total, not one per target.
    assert state.characters["elrond"].spell_slots[1] == 1


# ------------------------------------------------------------- concentration


def test_concentration_breaks_on_a_failed_con_save_after_damage() -> None:
    # Elrond, concentrating on a spell, takes a hit from goblin_1's Scimitar
    # (+4, already adjacent per this file's module docstring). Natural 15 ->
    # total 19 -> hit regardless of AC. Damage 1d6+2, natural 1 -> 3 damage,
    # so the concentration DC is max(10, 3//2)=10 (the SRD floor, not half
    # of a small hit). Elrond's CON12 -> mod1, no CON save proficiency for a
    # Wizard (INT/WIS are their two) -> save_bonus=1. Natural 5 -> total 6
    # < 10 -> fails -> concentration breaks.
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("goblin_1")
    state.characters["elrond"].concentrating_on = "Hold Person"

    action = ParsedAction(actor="goblin_1", verb="attack", target="elrond", raw_text="attack")
    resolve_action(state, action, _FixedRandom([15, 1, 5]))  # type: ignore[arg-type]

    concentration_event = next(
        e
        for e in state.events
        if e.type == "saving_throw" and e.payload.get("kind") == "concentration"
    )
    assert concentration_event.payload["dc"] == 10
    assert concentration_event.payload["success"] is False
    assert state.characters["elrond"].concentrating_on is None


def test_concentration_survives_a_successful_con_save() -> None:
    # Identical setup/damage to the failing case above, but the CON save
    # natural 15 -> total 16 >= 10 -> succeeds -> concentration is kept.
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("goblin_1")
    state.characters["elrond"].concentrating_on = "Hold Person"

    action = ParsedAction(actor="goblin_1", verb="attack", target="elrond", raw_text="attack")
    resolve_action(state, action, _FixedRandom([15, 1, 15]))  # type: ignore[arg-type]

    concentration_event = next(
        e
        for e in state.events
        if e.type == "saving_throw" and e.payload.get("kind") == "concentration"
    )
    assert concentration_event.payload["success"] is True
    assert state.characters["elrond"].concentrating_on == "Hold Person"


def test_casting_a_new_concentration_spell_clears_the_prior_one() -> None:
    # Casting a fresh concentration spell always drops whatever was set
    # before, per SRD - proven directly against cast_spell rather than the
    # damage path above. Hold Person (dc-based, concentration=True) cast at
    # goblin_1: only the concentration bookkeeping matters here, so the save
    # roll's outcome is irrelevant to the assertion.
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("elrond")
    state.characters["elrond"].concentrating_on = "Some Earlier Spell"
    # Hold Person is 2nd level; Elrond only starts with level-1 slots
    # (LEVEL_1_SPELL_SLOTS) - poke one in directly, same "poke state rather
    # than build a whole new fixture" pattern this project already uses
    # elsewhere (e.g. test_cast_spell_with_no_slots_remaining_errors_clearly).
    state.characters["elrond"].spell_slots[2] = 1
    # Same reasoning for the Prepared restriction (issue #30's follow-up
    # phase): Elrond's real chosen_prepared_spells is level-1-only (creation
    # only ever offers a level-1 pool), so a 2nd-level spell has nowhere
    # legitimate to be added at creation time either - poked in directly too.
    state.characters["elrond"].prepared_spells.append("hold-person")

    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="hold person",
        raw_text="I cast hold person at the goblin",
    )
    resolve_action(state, action, _FixedRandom([10]))  # type: ignore[arg-type]

    assert state.characters["elrond"].concentrating_on == "Hold Person"


# ------------------------------------------------- auto-hit spells (issue #35)
# Magic Missile: no roll of any kind (not even a hit check - unlike attack/
# save spells, AC never enters into it), so every RNG value below feeds
# straight into a dart's own 1d4+1 damage roll.


def test_magic_missile_single_target_gets_all_darts() -> None:
    # No explicit `targets` list - all 3 darts (level-1 Magic Missile, see
    # magic_missile_dart_count) go to the one named target, per SRD's "you
    # can direct them to hit one creature" default. Naturals [1,1,1] -> each
    # dart 1+1=2 force damage -> 6 total; goblin_1 (7 HP) survives at 1.
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("elrond")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="magic missile",
        raw_text="I cast magic missile at the goblin",
    )
    resolve_action(state, action, _FixedRandom([1, 1, 1]))  # type: ignore[arg-type]

    spell_events = [e for e in state.events if e.type == "spell_cast"]
    assert len(spell_events) == 3
    assert all(e.payload["target"] == "goblin_1" for e in spell_events)
    assert [e.payload["damage"] for e in spell_events] == [2, 2, 2]
    assert state.characters["goblin_1"].hp == 1  # 7 - 6
    assert state.characters["elrond"].spell_slots[1] == 1  # one slot total, not one per dart


def test_magic_missile_splits_darts_across_named_targets() -> None:
    # action.targets is one entry per dart (issue #35's own design) -
    # repeating "goblin_1" sends 2 darts there, the remaining 1 at goblin_2.
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("elrond")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        targets=["goblin_1", "goblin_1", "goblin_2"],
        item_or_spell="magic missile",
        raw_text="I send two darts at the first goblin and one at the second",
    )
    resolve_action(state, action, _FixedRandom([1, 1, 1]))  # type: ignore[arg-type]

    spell_events = [e for e in state.events if e.type == "spell_cast"]
    assert [e.payload["target"] for e in spell_events] == ["goblin_1", "goblin_1", "goblin_2"]
    assert state.characters["goblin_1"].hp == 3  # 7 - 2 - 2
    assert state.characters["goblin_2"].hp == 5  # 7 - 2


def test_magic_missile_rejects_more_targets_than_available_darts() -> None:
    # A 3rd goblin so there are enough distinct ids to name 4 - level-1
    # Magic Missile only creates 3 darts, so 4 named targets must reject
    # before any dice are rolled (empty RNG list proves this).
    state = _build_demo_state(_INITIATIVE)
    state.current_turn = state.turn_order.index("elrond")
    srd = load_srd()
    goblin_3 = monster_to_character(srd.monsters["goblin"], "goblin_3", Position(x=0, y=2))
    state.characters["goblin_3"] = goblin_3
    state.turn_order.append("goblin_3")

    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        targets=["goblin_1", "goblin_2", "goblin_3", "goblin_1"],
        item_or_spell="magic missile",
        raw_text="I throw darts at all of them and then some",
    )
    with pytest.raises(TurnEngineError, match="only creates 3 dart"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
