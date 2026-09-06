"""Day 14: cast_spell (attack-roll spells), use_item (healing potion), and
real death saves (unconscious vs. dead)."""

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import build_encounter_state
from src.engine.state import Character, Condition
from src.engine.turn_engine import HEALING_POTION_INDEX, TurnEngineError, resolve_action


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


_INITIATIVE = [18, 10, 8, 3]  # turn_order: thorin, elrond, goblin_1, goblin_2


def _end_turn(state, actor_id: str) -> None:  # type: ignore[no-untyped-def]
    resolve_action(
        state,
        ParsedAction(actor=actor_id, verb="end_turn", raw_text="pass"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- cast_spell


def test_cast_cantrip_hits_and_does_not_touch_spell_slots() -> None:
    # Elrond (Wizard, INT15 -> mod2): attack_bonus 2+2=4. Fire Bolt (cantrip)
    # vs goblin_1 AC15: natural 14 -> total 18 -> hit. Damage 1d10, natural
    # 6 -> total 6 (no ability mod added to spell damage). goblin_1 HP 7-6=1.
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    slots_before = dict(state.characters["elrond"].spell_slots)

    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="fire bolt",
        raw_text="I cast fire bolt at the goblin",
    )
    resolve_action(state, action, _FixedRandom([14, 6]))  # type: ignore[arg-type]

    spell_event = next(e for e in state.events if e.type == "spell_cast")
    assert spell_event.payload["hit"] is True
    assert spell_event.payload["spell_level"] == 0
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 6
    assert state.characters["goblin_1"].hp == 1
    assert state.characters["elrond"].spell_slots == slots_before


def test_cast_leveled_spell_consumes_a_slot_and_can_kill() -> None:
    # A dedicated Cleric (Guiding Bolt is cleric-only in the SRD and is the
    # only 1st-level attack-roll spell available for this test - Wizard has
    # none). WIS16 -> mod3, attack_bonus 3+2=5 vs goblin AC15: natural 14 ->
    # total 19 -> hit. Damage 4d6, naturals [3,3,3,3] -> total 12 -> kills
    # the goblin (7 HP) outright (monsters don't make death saves).
    mira = create_character(
        character_id="mira",
        name="Mira",
        race_index="human",
        class_index="cleric",
        background_index="acolyte",
        base_ability_scores={"STR": 13, "DEX": 10, "CON": 14, "INT": 8, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-medicine", "skill-religion"],
    )
    encounter = build_demo_encounter()
    state = build_encounter_state(encounter, [mira], _FixedRandom([20, 5, 5]))  # type: ignore[arg-type]
    assert state.turn_order[0] == "mira"

    action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="guiding bolt",
        raw_text="I cast guiding bolt at the goblin",
    )
    resolve_action(state, action, _FixedRandom([14, 3, 3, 3, 3]))  # type: ignore[arg-type]

    assert state.characters["mira"].spell_slots[1] == 1  # started at 2
    assert state.characters["goblin_1"].is_dead is True
    assert any(e.type == "death" for e in state.events)


def test_cast_spell_with_no_slots_remaining_errors_clearly() -> None:
    mira = create_character(
        character_id="mira",
        name="Mira",
        race_index="human",
        class_index="cleric",
        background_index="acolyte",
        base_ability_scores={"STR": 13, "DEX": 10, "CON": 14, "INT": 8, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-medicine", "skill-religion"],
    )
    encounter = build_demo_encounter()
    state = build_encounter_state(encounter, [mira], _FixedRandom([20, 5, 5]))  # type: ignore[arg-type]
    state.characters["mira"].spell_slots[1] = 0

    action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="guiding bolt",
        raw_text="I cast guiding bolt",
    )
    with pytest.raises(TurnEngineError, match="no level-1 spell slots"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_cast_spell_rejects_save_based_spells() -> None:
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="sacred flame",
        raw_text="I cast sacred flame",
    )
    with pytest.raises(TurnEngineError, match="not supported"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_cast_spell_rejects_unknown_spell_name() -> None:
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="made up spell",
        raw_text="I cast a made-up spell",
    )
    with pytest.raises(TurnEngineError, match="Unknown spell"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_cast_spell_rejects_a_same_side_target() -> None:
    # Same regression class as test_turn_engine.py's attack version, added
    # for the spell path too (Day 15).
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        target="thorin",
        item_or_spell="fire bolt",
        raw_text="I cast fire bolt at Thorin",
    )
    with pytest.raises(TurnEngineError, match="same side"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# ----------------------------------------------------------------- use_item


def test_use_item_heals_and_removes_the_potion() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.hp = 4
    thorin.inventory.append(HEALING_POTION_INDEX)

    # 2d4+2, naturals [3, 2] -> total 7. min(7, max_hp(12)-hp(4)=8) = 7.
    action = ParsedAction(
        actor="thorin",
        verb="use_item",
        item_or_spell="potion of healing",
        raw_text="I drink a healing potion",
    )
    resolve_action(state, action, _FixedRandom([3, 2]))  # type: ignore[arg-type]

    assert thorin.hp == 11
    assert HEALING_POTION_INDEX not in thorin.inventory
    event = next(e for e in state.events if e.type == "hp_change")
    assert event.payload["amount"] == 7


def test_use_item_heal_is_capped_at_max_hp() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.hp = 11  # max_hp 12, only 1 point of room
    thorin.inventory.append(HEALING_POTION_INDEX)

    action = ParsedAction(
        actor="thorin",
        verb="use_item",
        item_or_spell="potion of healing",
        raw_text="I drink a healing potion",
    )
    resolve_action(state, action, _FixedRandom([4, 4]))  # type: ignore[arg-type]

    assert thorin.hp == 12  # capped, not 12 + 10


def test_use_item_accepts_natural_word_order_too() -> None:
    # Regression case (see CLAUDE.md): a live LLM call produced "healing
    # potion" (natural adjective-noun order), not the canonical SRD
    # "potion of healing" - an exact-string match rejected a valid request.
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.hp = 4
    thorin.inventory.append(HEALING_POTION_INDEX)

    action = ParsedAction(
        actor="thorin",
        verb="use_item",
        item_or_spell="healing potion",
        raw_text="I drink my healing potion",
    )
    resolve_action(state, action, _FixedRandom([3, 2]))  # type: ignore[arg-type]
    assert thorin.hp == 11


def test_use_item_rejects_unsupported_items() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin", verb="use_item", item_or_spell="rope", raw_text="I use my rope"
    )
    with pytest.raises(TurnEngineError, match="Don't know how to use"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_use_item_rejects_when_potion_not_in_inventory() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin",
        verb="use_item",
        item_or_spell="potion of healing",
        raw_text="I drink a healing potion",
    )
    with pytest.raises(TurnEngineError, match="has no potion-of-healing"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_character_creation_accepts_the_healing_potion() -> None:
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword", HEALING_POTION_INDEX],
    )
    assert HEALING_POTION_INDEX in thorin.inventory


# -------------------------------------------------------------- death saves


def test_pc_at_zero_hp_goes_unconscious_not_dead() -> None:
    # thorin (AC12) at 3 HP; goblin_1's Scimitar (+4) vs a natural 11 ->
    # total 15 -> hit. Damage 1d6+2, natural 6 -> 8 damage, overkill clamped.
    state = _build_demo_state(_INITIATIVE)
    _end_turn(state, "thorin")
    _end_turn(state, "elrond")
    state.characters["thorin"].hp = 3

    action = ParsedAction(actor="goblin_1", verb="attack", target="thorin", raw_text="attack")
    resolve_action(state, action, _FixedRandom([11, 6]))  # type: ignore[arg-type]

    thorin = state.characters["thorin"]
    assert thorin.hp == 0
    assert thorin.is_dead is False
    assert has_condition(thorin, "unconscious") is True
    assert state.status == "in_progress"  # elrond is still up - not a defeat
    assert any(
        e.type == "condition_applied" and e.payload["condition"] == "unconscious"
        for e in state.events
    )


def test_unconscious_character_can_only_attempt_a_death_save() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious"))
    # Force it to be Thorin's turn regardless of where it actually is.
    state.current_turn = state.turn_order.index("thorin")

    with pytest.raises(TurnEngineError, match="can only attempt a death save"):
        resolve_action(
            state,
            ParsedAction(actor="thorin", verb="attack", target="goblin_1", raw_text="I attack"),
            _FixedRandom([]),  # type: ignore[arg-type]
        )


def test_death_save_three_successes_stabilizes() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious"))
    state.current_turn = state.turn_order.index("thorin")

    action = ParsedAction(actor="thorin", verb="death_save", raw_text="death save")
    for natural_roll in (12, 15, 18):  # all >= 10: three successes
        state.current_turn = state.turn_order.index("thorin")
        resolve_action(state, action, _FixedRandom([natural_roll]))  # type: ignore[arg-type]

    assert thorin.death_save_successes == 3
    assert thorin.is_stable is True
    assert thorin.is_dead is False
    assert thorin.hp == 0
    assert has_condition(thorin, "unconscious") is True  # still unconscious, just not dying


def test_death_save_three_failures_kills() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious"))

    action = ParsedAction(actor="thorin", verb="death_save", raw_text="death save")
    for natural_roll in (5, 8, 3):  # all < 10, none nat-1: three failures
        state.current_turn = state.turn_order.index("thorin")
        resolve_action(state, action, _FixedRandom([natural_roll]))  # type: ignore[arg-type]

    assert thorin.death_save_failures == 3
    assert thorin.is_dead is True
    assert has_condition(thorin, "unconscious") is False


def test_death_save_natural_1_counts_as_two_failures() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious"))

    action = ParsedAction(actor="thorin", verb="death_save", raw_text="death save")
    state.current_turn = state.turn_order.index("thorin")
    resolve_action(state, action, _FixedRandom([1]))  # type: ignore[arg-type]
    assert thorin.death_save_failures == 2
    assert thorin.is_dead is False

    state.current_turn = state.turn_order.index("thorin")
    resolve_action(state, action, _FixedRandom([7]))  # type: ignore[arg-type]
    assert thorin.death_save_failures == 3
    assert thorin.is_dead is True


def test_death_save_natural_20_revives_with_one_hp() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious"))
    state.current_turn = state.turn_order.index("thorin")

    action = ParsedAction(actor="thorin", verb="death_save", raw_text="death save")
    resolve_action(state, action, _FixedRandom([20]))  # type: ignore[arg-type]

    assert thorin.hp == 1
    assert has_condition(thorin, "unconscious") is False
    assert thorin.death_save_successes == 0
    assert thorin.death_save_failures == 0


def test_death_save_rejects_a_conscious_character() -> None:
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(actor="thorin", verb="death_save", raw_text="death save")
    with pytest.raises(TurnEngineError, match="not unconscious"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_dead_pc_cannot_act_and_defeat_is_keyed_off_is_dead_not_hp() -> None:
    state = _build_demo_state(_INITIATIVE)
    thorin = state.characters["thorin"]
    elrond = state.characters["elrond"]
    thorin.hp = 0
    thorin.is_dead = True
    elrond.hp = 0  # unconscious but not dead - should NOT be a defeat yet
    apply_condition(elrond, Condition(name="unconscious"))
    state.current_turn = state.turn_order.index("thorin")

    with pytest.raises(TurnEngineError, match="dead and cannot act"):
        resolve_action(
            state,
            ParsedAction(actor="thorin", verb="death_save", raw_text="death save"),
            _FixedRandom([]),  # type: ignore[arg-type]
        )

    # A defeat check only fires when both are actually dead, not merely at 0 HP.
    from src.engine.turn_engine import _check_victory_defeat

    _check_victory_defeat(state)
    assert state.status == "in_progress"
    elrond.is_dead = True
    _check_victory_defeat(state)
    assert state.status == "defeat"
