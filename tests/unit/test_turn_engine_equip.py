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


def test_equip_rejects_an_item_thats_neither_weapon_nor_armor() -> None:
    # Armor became a legal equip target with issue #13 - this now tests the
    # genuinely-wrong-category case instead (general adventuring gear;
    # clothes-common is already in every fighter's starting kit).
    thorin = _fighter()
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["clothes-common"]},
        raw_text="I equip my clothes",
    )
    with pytest.raises(TurnEngineError, match="not a weapon or armor"):
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


def test_equip_matches_a_real_weapon_with_an_invented_adjective() -> None:
    # Same fuzzy-name-matching lesson as attack's "silvered longbow" fix
    # (Day 14/Phase 9H) - the intent parser passes whatever phrase the
    # player used, not necessarily an exact SRD index.
    thorin = _fighter()
    thorin.inventory.append("dagger")
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["my trusty dagger"]},
        raw_text="I draw my trusty dagger",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert thorin.equipped_weapons == ["dagger"]


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


def test_attack_naming_ammunition_falls_back_to_the_equipped_ranged_weapon() -> None:
    # Found live: a companion's own free-text turn ("I nock an arrow and
    # fire") named the ammunition, not the bow - "arrow" is a real SRD item
    # (Adventuring Gear/Ammunition, not a weapon), so it isn't actually
    # naming a different weapon the way "longsword" does in the sibling
    # test above. Should resolve against the equipped Longbow instead of
    # rejecting with "use equip first".
    archer = create_character(
        character_id="silvana",
        name="Silvana",
        race_index="elf",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 10, "DEX": 15, "CON": 13, "INT": 12, "WIS": 14, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longbow"],
        position=Position(x=0, y=0),
    )
    goblin = _goblin("goblin_1", Position(x=6, y=0))
    state = _make_state(archer, goblin)

    action = ParsedAction(
        actor="silvana",
        verb="attack",
        target="goblin_1",
        item_or_spell="arrow",
        raw_text="I nock an arrow and fire",
    )
    resolve_action(state, action, _FixedRandom([10, 3]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["source"] == "Longbow"


def test_attack_naming_gibberish_still_rejects() -> None:
    # Contrast with the ammunition case above - "my fireproof toaster"
    # isn't a real SRD item at all, so it's a genuinely confused
    # declaration rather than a same-hand-different-noun case, and should
    # still surface a clear error rather than silently guessing.
    thorin = _fighter()
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="my fireproof toaster",
        raw_text="I attack with my fireproof toaster",
    )
    with pytest.raises(TurnEngineError, match="isn't in thorin's equipped weapon set"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


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


# ------------------------------------------------------- offhand_attack (#12)


def _dual_dagger_fighter(position: Position | None = None) -> Character:
    thorin = _fighter(position)
    thorin.inventory += ["dagger", "dagger"]
    thorin.equipped_weapons = ["dagger", "dagger"]
    return thorin


def test_offhand_attack_rejects_with_fewer_than_two_equipped_weapons() -> None:
    thorin = _fighter()  # only the longsword equipped
    state = _make_state(thorin, _goblin("goblin_1", Position(x=0, y=0)))
    action = ParsedAction(
        actor="thorin",
        verb="offhand_attack",
        target="goblin_1",
        raw_text="I stab with my other blade",
    )
    with pytest.raises(TurnEngineError, match="needs two light weapons equipped"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_offhand_attack_rejects_when_bonus_action_already_used() -> None:
    thorin = _dual_dagger_fighter()
    thorin.bonus_action_used = True
    state = _make_state(thorin, _goblin("goblin_1", Position(x=0, y=0)))
    action = ParsedAction(
        actor="thorin",
        verb="offhand_attack",
        target="goblin_1",
        raw_text="I stab with my other blade",
    )
    with pytest.raises(TurnEngineError, match="already used their bonus action"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_offhand_attack_deals_damage_without_the_ability_modifier() -> None:
    # Thorin: base STR15/DEX14, Human's +1-to-all racial bonus -> STR16/
    # DEX15, mods 3/2. Dagger is finesse so ability_mod = max(3, 2) = 3.
    # Attack bonus = 3 + proficiency(2) = 5, natural 15 -> total 20, beats
    # the goblin's AC15 cleanly (not a crit - natural isn't 20). Damage die
    # natural 3 both times: a normal attack deals 3 + ability_mod(3) = 6;
    # the off-hand attack deals 3 + 0 = 3 - the ability modifier is the
    # only thing that should differ.
    thorin = _dual_dagger_fighter()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)

    # Off-hand (bonus action) first, so the turn is still thorin's for the
    # main-action attack right after - matches the real SRD play order.
    offhand_attack = ParsedAction(
        actor="thorin",
        verb="offhand_attack",
        target="goblin_1",
        raw_text="I stab with my off-hand dagger",
    )
    resolve_action(state, offhand_attack, _FixedRandom([15, 3]))  # type: ignore[arg-type]
    offhand_damage = next(e for e in state.events if e.type == "damage_dealt")
    assert offhand_damage.payload["amount"] == 3

    normal_attack = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="dagger",
        raw_text="I follow up with my main-hand dagger",
    )
    resolve_action(state, normal_attack, _FixedRandom([15, 3]))  # type: ignore[arg-type]
    normal_damage = [e for e in state.events if e.type == "damage_dealt"][-1]
    assert normal_damage.payload["amount"] == 6


def test_offhand_attack_does_not_end_the_turn() -> None:
    thorin = _dual_dagger_fighter()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)

    offhand_attack = ParsedAction(
        actor="thorin",
        verb="offhand_attack",
        target="goblin_1",
        raw_text="I follow up with my other dagger",
    )
    resolve_action(state, offhand_attack, _FixedRandom([15, 3]))  # type: ignore[arg-type]
    assert state.turn_order[state.current_turn] == "thorin"

    # The actor's real main action still works afterward, same turn.
    normal_attack = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="dagger",
        raw_text="I finish with my main-hand dagger",
    )
    resolve_action(state, normal_attack, _FixedRandom([15, 3]))  # type: ignore[arg-type]
    assert len([e for e in state.events if e.type == "attack_roll"]) == 2


# --------------------------------------------- armor/shield equip (#13)


def test_equip_armor_changes_equipped_armor_and_recomputes_ac() -> None:
    # Thorin: base AC 12 (unarmored, dex_mod 2). Leather Armor: base 11,
    # full (uncapped) dex bonus -> 11 + 2 = 13.
    thorin = _fighter()
    assert thorin.ac == 12
    thorin.inventory.append("leather-armor")
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["leather-armor"]}, raw_text="I don my armor"
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert thorin.equipped_armor == "leather-armor"
    assert thorin.ac == 13


def test_equip_shield_alone_recomputes_ac() -> None:
    thorin = _fighter()
    thorin.inventory.append("shield")
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["shield"]}, raw_text="I raise my shield"
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert thorin.equipped_shield == "shield"
    assert thorin.ac == 14  # 10 + dex_mod(2) + shield(2)


def test_equip_armor_does_not_clear_equipped_weapons() -> None:
    thorin = _fighter()  # longsword already equipped by auto-populate
    assert thorin.equipped_weapons == ["longsword"]
    thorin.inventory.append("leather-armor")
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["leather-armor"]}, raw_text="I don my armor"
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert thorin.equipped_weapons == ["longsword"]
    assert thorin.equipped_armor == "leather-armor"


def test_equip_weapon_and_armor_together_in_one_action() -> None:
    thorin = _fighter()
    thorin.inventory += ["dagger", "dagger", "leather-armor"]
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["dagger", "dagger", "leather-armor"]},
        raw_text="I draw two daggers and don my armor",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert thorin.equipped_weapons == ["dagger", "dagger"]
    assert thorin.equipped_armor == "leather-armor"
    assert thorin.ac == 13


def test_equip_rejects_two_suits_of_armor_at_once() -> None:
    thorin = _fighter()
    thorin.inventory += ["leather-armor", "chain-mail"]
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["leather-armor", "chain-mail"]},
        raw_text="I put on both",
    )
    with pytest.raises(TurnEngineError, match="Cannot equip two suits of armor"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# ------------------------------------------- hand-occupancy: weapons + shield (#26)


def test_equip_shield_rejected_when_two_light_weapons_already_equipped() -> None:
    # The exact bug found live: 2 one-handed weapons already equipped, then
    # a shield equipped on its own - params["items"] = ["shield"] never
    # names any weapon, so the old code path had zero cross-check against
    # the weapons already worn.
    thorin = _fighter()
    thorin.inventory += ["dagger", "dagger", "shield"]
    thorin.equipped_weapons = ["dagger", "dagger"]
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["shield"]}, raw_text="I raise my shield"
    )
    with pytest.raises(TurnEngineError, match="Cannot equip"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert thorin.equipped_shield is None  # rejected outright, not partially applied


def test_equip_shield_succeeds_with_only_one_weapon_equipped() -> None:
    thorin = _fighter()  # longsword already equipped (1 hand) - a shield fits (2 hands total)
    thorin.inventory.append("shield")
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["shield"]}, raw_text="I raise my shield"
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert thorin.equipped_shield == "shield"
    assert thorin.equipped_weapons == ["longsword"]


def test_equip_two_weapons_rejected_when_a_shield_is_already_equipped() -> None:
    thorin = _fighter()
    thorin.inventory += ["dagger", "dagger", "shield"]
    thorin.equipped_shield = "shield"
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["dagger", "dagger"]},
        raw_text="I draw two daggers",
    )
    with pytest.raises(TurnEngineError, match="Cannot equip"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert thorin.equipped_weapons == ["longsword"]  # unchanged, not partially applied


def test_equip_two_handed_weapon_rejected_when_a_shield_is_already_equipped() -> None:
    thorin = _fighter()
    thorin.inventory += ["greataxe", "shield"]
    thorin.equipped_shield = "shield"
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin", verb="equip", params={"items": ["greataxe"]}, raw_text="I heft my greataxe"
    )
    with pytest.raises(TurnEngineError, match="Cannot equip"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_equip_two_weapons_and_a_shield_together_in_one_action_rejected() -> None:
    # The exact loadout from the live bug report: dagger + handaxe +
    # shield, all named in one equip call.
    thorin = _fighter()
    thorin.inventory += ["dagger", "handaxe", "shield"]
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(
        actor="thorin",
        verb="equip",
        params={"items": ["dagger", "handaxe", "shield"]},
        raw_text="I draw a dagger and handaxe and raise my shield",
    )
    with pytest.raises(TurnEngineError, match="Cannot equip"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
