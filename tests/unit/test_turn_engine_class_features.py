"""Phase 9I: four representative level-1 class features. Fighting Style's
Archery/Dueling attack-roll/damage effects are tested here (Defense's AC
effect is a creation-time computation, tested in
test_character_creation.py instead). Second Wind and Rage are new
bonus-action verbs sharing Phase 9H's ends_turn=False mechanism. Sneak
Attack is automatic (no verb of its own) on a qualifying hit.
"""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import (
    BardicChoicePending,
    TurnEngineError,
    resolve_action,
    resolve_pending_bardic_choice,
)


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
        encounter_id="class_features_test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
        battle_map=_open_map(10, 10),
    )


def _goblin(char_id: str, position: Position) -> Character:
    # HP boosted well past the SRD's real 7 - several fixtures here deal
    # more than that in one hit, and a real goblin's HP would clamp
    # apply_damage's reported amount, hiding the exact number a test wants
    # to check (e.g. a Dueling/Rage/Sneak-Attack bonus actually landing).
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], char_id, position)
    goblin.hp = goblin.max_hp = 100
    return goblin


# --- Fighting Style: Archery / Dueling (attack-resolution effects) ---------


def test_archery_fighting_style_adds_two_to_ranged_attack_rolls() -> None:
    # Silvana (Elf Ranger, DEX15->17 after elf +2 -> mod+3), longbow,
    # proficient (+2). Without Archery: attack_bonus 5, roll 9 -> total 14,
    # misses goblin's AC15. With Archery (+2): attack_bonus 7, same roll 9
    # -> total 16, hits - the +2 is the deciding factor, not incidental.
    silvana = create_character(
        character_id="silvana",
        name="Silvana",
        race_index="elf",
        class_index="ranger",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 15, "CON": 12, "INT": 10, "WIS": 14, "CHA": 13},
        chosen_skills=["skill-survival", "skill-perception", "skill-nature"],
        chosen_equipment=["longbow"],
        fighting_style="archery",
        position=Position(x=0, y=0),
    )
    goblin = _goblin("goblin_1", Position(x=6, y=0))  # 30ft - within longbow's 150ft normal range
    state = _make_state(silvana, goblin)
    action = ParsedAction(
        actor="silvana",
        verb="attack",
        target="goblin_1",
        item_or_spell="longbow",
        raw_text="I loose an arrow",
    )
    resolve_action(state, action, _FixedRandom([9, 4]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["roll_total"] == 16
    assert attack_event.payload["hit"] is True


def test_dueling_fighting_style_adds_damage_with_one_melee_weapon_and_no_other() -> None:
    # Thorin (Human Fighter, STR15->16 -> mod+3), longsword only in
    # inventory. Damage without Dueling: STR mod(3) + notation bonus(0) = 3
    # (die roll 5 -> total 8). With Dueling: +2 -> total 10.
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        fighting_style="dueling",
        position=Position(x=0, y=0),
    )
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack",
    )
    resolve_action(state, action, _FixedRandom([15, 5]))  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 10  # 5 + 3(STR) + 2(Dueling)


def test_dueling_fighting_style_grants_nothing_with_a_second_weapon_carried() -> None:
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword", "dagger"],
        fighting_style="dueling",
        position=Position(x=0, y=0),
    )
    # Phase C: longsword+dagger isn't a legal simultaneous-equip combo
    # (longsword isn't light), so create_character's own auto-populate
    # would only ever equip the longsword alone - directly poke both as
    # equipped to keep testing this test's actual subject (Dueling denied
    # when 2 weapons are equipped, regardless of whether that specific
    # combo could ever be reached through the "equip" verb itself).
    thorin.equipped_weapons = ["longsword", "dagger"]
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack",
    )
    resolve_action(state, action, _FixedRandom([15, 5]))  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 8  # 5 + 3(STR), no Dueling bonus


# --- Second Wind ------------------------------------------------------------


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


def test_second_wind_heals_and_does_not_end_the_turn() -> None:
    thorin = _fighter()
    thorin.hp = 1
    goblin = _goblin("goblin_1", Position(x=5, y=5))
    state = _make_state(thorin, goblin)
    action = ParsedAction(actor="thorin", verb="second_wind", raw_text="I catch my breath")
    resolve_action(state, action, _FixedRandom([6]))  # type: ignore[arg-type]

    assert thorin.hp == 8  # 1 + (6 + level 1)
    assert thorin.class_resources["second_wind"] == 0
    assert thorin.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "thorin"  # still his turn


def test_second_wind_rejected_with_no_uses_remaining() -> None:
    thorin = _fighter()
    thorin.class_resources["second_wind"] = 0
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(actor="thorin", verb="second_wind", raw_text="I catch my breath")
    with pytest.raises(TurnEngineError, match="no second wind uses remaining"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_second_wind_rejected_if_bonus_action_already_used() -> None:
    thorin = _fighter()
    thorin.bonus_action_used = True
    state = _make_state(thorin, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(actor="thorin", verb="second_wind", raw_text="I catch my breath")
    with pytest.raises(TurnEngineError, match="already used their bonus action"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# --- Rage --------------------------------------------------------------------


def _barbarian(position: Position | None = None) -> Character:
    position = position or Position(x=0, y=0)
    return create_character(
        character_id="grom",
        name="Grom",
        race_index="human",
        class_index="barbarian",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-intimidation"],
        chosen_equipment=["battleaxe"],
        position=position,
    )


def test_rage_does_not_end_the_turn_and_grants_a_melee_damage_bonus() -> None:
    # Grom (STR15->16 -> mod+3) attacks with a battleaxe (1d8) after raging.
    # Damage without Rage would be 3 (die) + 3 (STR) = 6; with Rage's +2 ->
    # 8.
    grom = _barbarian()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(grom, goblin)

    rage_action = ParsedAction(actor="grom", verb="rage", raw_text="I fly into a rage")
    resolve_action(state, rage_action, _FixedRandom([]))  # type: ignore[arg-type]
    assert grom.is_raging is True
    assert grom.class_resources["rage"] == 1
    assert state.turn_order[state.current_turn] == "grom"  # still his turn

    attack_action = ParsedAction(
        actor="grom",
        verb="attack",
        target="goblin_1",
        item_or_spell="battleaxe",
        raw_text="I swing my axe",
    )
    resolve_action(state, attack_action, _FixedRandom([15, 3]))  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 8


def test_rage_grants_resistance_to_bludgeoning_piercing_slashing_damage() -> None:
    grom = _barbarian()
    grom.is_raging = True
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(grom, goblin)
    # Goblin's Scimitar (slashing): attack_bonus+4, roll 15 -> total 19,
    # hits Grom's AC (12, no armor chosen); damage 1d6+2, die 4 -> raw 6,
    # halved by resistance (rounded down) -> 3.
    state.current_turn = state.turn_order.index("goblin_1")
    action = ParsedAction(
        actor="goblin_1", verb="attack", target="grom", raw_text="the goblin attacks"
    )
    resolve_action(state, action, _FixedRandom([15, 4]))  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 3


def test_rage_rejected_when_already_raging() -> None:
    grom = _barbarian()
    grom.is_raging = True
    state = _make_state(grom, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(actor="grom", verb="rage", raw_text="I rage again")
    with pytest.raises(TurnEngineError, match="already raging"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_rage_rejected_with_no_uses_remaining() -> None:
    grom = _barbarian()
    grom.class_resources["rage"] = 0
    state = _make_state(grom, _goblin("goblin_1", Position(x=5, y=5)))
    action = ParsedAction(actor="grom", verb="rage", raw_text="I fly into a rage")
    with pytest.raises(TurnEngineError, match="no rage uses remaining"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# --- Sneak Attack ------------------------------------------------------------


def _rogue(position: Position | None = None) -> Character:
    position = position or Position(x=0, y=0)
    return create_character(
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
        position=position,
    )


def test_sneak_attack_adds_1d6_when_the_attack_has_advantage() -> None:
    # Fenwick (Halfling Rogue, DEX15->17 after +2 -> mod+3), shortsword
    # (finesse, 1d6 piercing). Dodging goblin grants Fenwick advantage via
    # has_help_advantage instead (simpler fixture) - two d20s [15, 3], kept
    # 15 (advantage). Base damage: die 4 + DEX mod(3) = 7. Sneak Attack die
    # 5 added on top -> 12 total.
    fenwick = _rogue()
    fenwick.has_help_advantage = True
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(fenwick, goblin)
    action = ParsedAction(
        actor="fenwick",
        verb="attack",
        target="goblin_1",
        item_or_spell="shortsword",
        raw_text="I stab the goblin",
    )
    resolve_action(state, action, _FixedRandom([15, 3, 4, 5]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload.get("sneak_attack_damage") == 5
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 12  # 4 + 3(DEX) + 5(sneak attack)
    assert fenwick.sneak_attack_used_this_turn is True


def test_sneak_attack_triggers_via_an_adjacent_ally_without_advantage() -> None:
    fenwick = _rogue(Position(x=0, y=0))
    goblin = _goblin("goblin_1", Position(x=0, y=1))  # 5ft from Fenwick
    ally = create_character(
        character_id="grom",
        name="Grom",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        position=Position(x=1, y=1),  # 5ft from the goblin too
    )
    state = _make_state(fenwick, goblin, ally)
    action = ParsedAction(
        actor="fenwick",
        verb="attack",
        target="goblin_1",
        item_or_spell="shortsword",
        raw_text="I stab the goblin",
    )
    resolve_action(state, action, _FixedRandom([15, 4, 5]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload.get("sneak_attack_damage") == 5


def test_sneak_attack_does_not_trigger_without_a_finesse_or_ranged_weapon() -> None:
    fenwick = _rogue()
    fenwick.inventory = ["warhammer"]
    fenwick.equipped_weapons = ["warhammer"]
    fenwick.has_help_advantage = True  # advantage present, but wrong weapon type
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(fenwick, goblin)
    action = ParsedAction(
        actor="fenwick",
        verb="attack",
        target="goblin_1",
        item_or_spell="warhammer",
        raw_text="I swing the warhammer",
    )
    resolve_action(state, action, _FixedRandom([15, 3, 4]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert "sneak_attack_damage" not in attack_event.payload


def test_sneak_attack_does_not_trigger_twice_in_one_turn() -> None:
    # resolve_action's own top-of-function reset clears
    # sneak_attack_used_this_turn on every call (safe today - see its
    # docstring - since nothing lets a Rogue's plain attack produce more
    # than one resolve_action call per real turn), so this guard can only
    # be exercised by calling the internal per-roll resolver directly, the
    # way Two-Weapon Fighting or a future Rogue Extra-Attack-like feature
    # would (multiple _resolve_single_attack calls within one
    # resolve_action call, same pattern Multiattack/Extra Attack already
    # use for other classes).
    from src.engine.turn_engine import _pc_attack_params, _resolve_single_attack

    fenwick = _rogue()
    fenwick.has_help_advantage = True
    fenwick.sneak_attack_used_this_turn = True  # already triggered earlier this same call
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(fenwick, goblin)
    params = _pc_attack_params(fenwick, "shortsword", load_srd())

    _resolve_single_attack(state, fenwick, goblin, params, _FixedRandom([15, 3, 4]), load_srd())  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert "sneak_attack_damage" not in attack_event.payload


def test_sneak_attack_does_not_apply_to_a_non_rogue() -> None:
    thorin = _fighter()
    thorin.inventory = ["shortsword"]
    thorin.equipped_weapons = ["shortsword"]
    thorin.has_help_advantage = True
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="shortsword",
        raw_text="I stab the goblin",
    )
    resolve_action(state, action, _FixedRandom([15, 3, 4]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert "sneak_attack_damage" not in attack_event.payload


def _monster(index: str, char_id: str, position: Position, hp: int | None = None) -> Character:
    srd = load_srd()
    monster = monster_to_character(srd.monsters[index], char_id, position)
    if hp is not None:
        monster.hp = monster.max_hp = hp
    return monster


# --- Divine Smite (issue #21) -------------------------------------------------


def _paladin(position: Position | None = None) -> Character:
    position = position or Position(x=0, y=0)
    paladin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="paladin",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-persuasion"],
        chosen_equipment=["longsword"],
        position=position,
    )
    # A level-1 Paladin has zero spell slots at all, per SRD (see
    # character_creation.SPELL_SLOTS_BY_LEVEL's docstring) - bump straight
    # to level 2 with a slot to spend, bypassing level_up's HP/proficiency
    # recompute since these tests are about Divine Smite, not leveling.
    paladin.level = 2
    paladin.spell_slots = {1: 2}
    return paladin


def test_divine_smite_adds_radiant_damage_and_spends_a_slot_on_a_hit() -> None:
    # Thorin (Paladin, STR16->mod3, proficient longsword -> attack_bonus 5)
    # vs a goblin (AC15, HP boosted so the hit doesn't clamp the reported
    # amount). Natural 10 -> total 15 >= AC15 -> hit, not a crit. Weapon
    # damage: die 5 + STR mod 3 = 8. Divine Smite (1st-level slot): 2d8
    # ([4, 6] -> 10) - goblin isn't undead/fiend, no bonus die. Recorded
    # damage = 8 + 10 = 18.
    thorin = _paladin()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        params={"smite_slot_level": 1},
        raw_text="I strike and channel divine power through my blade",
    )
    resolve_action(state, action, _FixedRandom([10, 5, 4, 6]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["hit"] is True
    assert attack_event.payload["critical"] is False
    assert attack_event.payload["divine_smite_damage"] == 10
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 18
    assert thorin.spell_slots[1] == 1


def test_divine_smite_deals_an_extra_die_against_undead_or_fiends() -> None:
    # Same Paladin vs a zombie (undead, AC8, no slashing resistance) -
    # natural 6 -> total 11 >= AC8 -> hit, not a crit. Weapon damage: die 3
    # + STR mod 3 = 6. Smite base dice for a 1st-level slot is 2d8, +1d8 for
    # the undead target -> 3d8 ([2, 3, 4] -> 9). Recorded damage = 6 + 9 = 15.
    thorin = _paladin()
    zombie = _monster("zombie", "zombie_1", Position(x=0, y=0), hp=100)
    state = _make_state(thorin, zombie)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="zombie_1",
        item_or_spell="longsword",
        params={"smite_slot_level": 1},
        raw_text="I strike the undead with divine fury",
    )
    resolve_action(state, action, _FixedRandom([6, 3, 2, 3, 4]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["divine_smite_damage"] == 9
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 15


def test_divine_smite_dice_double_on_a_critical_hit() -> None:
    # Natural 20 always hits and is always a crit, regardless of AC -
    # doubles both the weapon's damage dice (existing engine behavior) and
    # Divine Smite's own dice (issue #21 - it's extra damage on the same
    # weapon attack, not a separate spell attack roll, same precedent as
    # Sneak Attack doubling on a crit above). Weapon (1d8, doubled to 2d8):
    # dice [5, 4] -> 9 + STR mod 3 = 12. Smite base dice for a 1st-level
    # slot is 2d8, doubled to 4d8 (goblin isn't undead/fiend): dice
    # [2, 2, 2, 2] -> 8. Recorded damage = 12 + 8 = 20.
    thorin = _paladin()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        params={"smite_slot_level": 1},
        raw_text="I strike true, channeling everything into the blow",
    )
    resolve_action(state, action, _FixedRandom([20, 5, 4, 2, 2, 2, 2]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["critical"] is True
    assert attack_event.payload["divine_smite_damage"] == 8
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 20


def test_divine_smite_rejects_a_non_paladin() -> None:
    thorin = _fighter()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        params={"smite_slot_level": 1},
        raw_text="I try to smite",
    )
    with pytest.raises(TurnEngineError, match="not a Paladin"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_divine_smite_rejects_when_no_slot_of_that_level_remains() -> None:
    thorin = _paladin()
    thorin.spell_slots = {1: 0}
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        params={"smite_slot_level": 1},
        raw_text="I try to smite",
    )
    with pytest.raises(TurnEngineError, match="no level 1 spell slots remaining"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_divine_smite_rejects_a_ranged_weapon() -> None:
    thorin = _paladin()
    thorin.inventory.append("shortbow")
    thorin.equipped_weapons = ["shortbow"]
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        params={"smite_slot_level": 1},
        raw_text="I loose an arrow and try to smite",
    )
    with pytest.raises(TurnEngineError, match="requires a melee weapon attack"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_divine_smite_can_trigger_on_each_swing_of_an_extra_attack() -> None:
    # Level 5 (Extra Attack-eligible) Paladin, two 1st-level slots. Both
    # swings hit (goblin AC15, natural 10 both times -> total 15) and both
    # declare a smite - proving the slot check re-reads actor.spell_slots
    # live on each swing rather than snapshotting availability once for the
    # whole action.
    thorin = _paladin()
    thorin.level = 5
    thorin.spell_slots = {1: 2}
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        params={"smite_slot_level": 1},
        raw_text="I strike twice, channeling divine power into both blows",
    )
    resolve_action(state, action, _FixedRandom([10, 3, 1, 1, 10, 2, 1, 2]))  # type: ignore[arg-type]

    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert len(attack_events) == 2
    assert attack_events[0].payload["divine_smite_damage"] == 2
    assert attack_events[1].payload["divine_smite_damage"] == 3
    assert thorin.spell_slots[1] == 0


# --- Cunning Action (issue #21) -------------------------------------------------


def test_cunning_action_dash_moves_double_speed_without_ending_the_turn() -> None:
    # Fenwick (Halfling Rogue) bumped to level 2 for Cunning Action. A
    # single-step path costs far less than even his base speed, so this
    # isn't testing the distance math (that's _resolve_move's own job) -
    # it's proving the verb dispatches through _resolve_move with dash's
    # speed-doubling semantics, consumes the bonus action, and does NOT end
    # the turn.
    fenwick = _rogue()
    fenwick.level = 2
    state = _make_state(fenwick, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="fenwick",
        verb="cunning_action",
        params={"action": "dash", "path": [{"x": 1, "y": 0}]},
        raw_text="I dash to the side",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert fenwick.position == Position(x=1, y=0)
    assert fenwick.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "fenwick"  # still his turn
    assert not any(e.type == "action_invalid" for e in state.events)


def test_cunning_action_disengage_sets_the_flag_without_ending_the_turn() -> None:
    fenwick = _rogue()
    fenwick.level = 2
    state = _make_state(fenwick, _goblin("goblin_1", Position(x=0, y=1)))
    action = ParsedAction(
        actor="fenwick",
        verb="cunning_action",
        params={"action": "disengage"},
        raw_text="I slip away, disengaging in one smooth motion",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert fenwick.disengaged_this_turn is True
    assert fenwick.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "fenwick"


def test_cunning_action_rejects_a_non_rogue() -> None:
    thorin = _fighter()
    state = _make_state(thorin, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="thorin",
        verb="cunning_action",
        params={"action": "disengage"},
        raw_text="I try to be nimble",
    )
    with pytest.raises(TurnEngineError, match="doesn't have Cunning Action"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_cunning_action_rejects_a_level_1_rogue() -> None:
    fenwick = _rogue()  # level defaults to 1 at creation - too early for this feature
    state = _make_state(fenwick, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="fenwick",
        verb="cunning_action",
        params={"action": "disengage"},
        raw_text="I try to slip away",
    )
    with pytest.raises(TurnEngineError, match="doesn't have Cunning Action"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_cunning_action_rejected_if_bonus_action_already_used() -> None:
    fenwick = _rogue()
    fenwick.level = 2
    fenwick.bonus_action_used = True
    state = _make_state(fenwick, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="fenwick",
        verb="cunning_action",
        params={"action": "disengage"},
        raw_text="I try to slip away",
    )
    with pytest.raises(TurnEngineError, match="already used their bonus action"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_cunning_action_rejects_an_unknown_sub_action() -> None:
    # Hide isn't offered (see _resolve_cunning_action's docstring - this
    # engine has no stealth/hidden-state mechanic at all yet), so it's
    # rejected the same as any other unrecognized params["action"].
    fenwick = _rogue()
    fenwick.level = 2
    state = _make_state(fenwick, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="fenwick",
        verb="cunning_action",
        params={"action": "hide"},
        raw_text="I try to hide",
    )
    with pytest.raises(TurnEngineError, match="'dash' or 'disengage'"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# --- Monk: Martial Arts + Flurry of Blows (issue #24) --------------------------


def _monk(position: Position | None = None) -> Character:
    position = position or Position(x=0, y=0)
    # Human's own +1-to-every-ability racial bonus applies: STR8->9(mod-1),
    # DEX15->16(mod+3), CON13->14(mod+2), INT10->11(mod0), WIS12->13(mod+1),
    # CHA14->15(mod+2) - DEX clearly beats STR, proving Martial Arts' DEX
    # option actually matters for this fixture, not just legal to use.
    return create_character(
        character_id="kai",
        name="Kai",
        race_index="human",
        class_index="monk",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 15, "CON": 13, "INT": 10, "WIS": 12, "CHA": 14},
        chosen_skills=["skill-acrobatics", "skill-stealth"],
        position=position,
    )


def test_monk_unarmed_strike_uses_martial_arts_die_and_dex_option() -> None:
    # Kai (level 1): DEX mod+3 beats STR mod-1 -> attack_bonus 3+2(prof)=5.
    # Natural 12 -> total 17 >= goblin AC 15 -> hit, not a crit. Martial
    # Arts die at level 1 is 1d4: die 3 + DEX mod 3 = 6 (not the plain
    # unarmed strike's flat "1 + STR mod", which would be a negative-mod 0).
    kai = _monk()
    kai.equipped_weapons = []  # Monk's starting kit auto-equips a dart - force truly unarmed
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai", verb="attack", target="goblin_1", raw_text="I strike with a swift punch"
    )
    resolve_action(state, action, _FixedRandom([12, 3]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["roll_total"] == 17
    assert attack_event.payload["hit"] is True
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 6


def test_monk_unarmed_strike_die_scales_to_1d6_at_level_5() -> None:
    kai = _monk()
    kai.equipped_weapons = []
    kai.level = 5
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai", verb="attack", target="goblin_1", raw_text="I strike with a swift punch"
    )
    resolve_action(state, action, _FixedRandom([12, 5]))  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 8  # die 5 (now 1d6-legal) + DEX mod 3


def test_monk_weapon_die_is_bumped_to_the_martial_arts_die_when_bigger() -> None:
    # A dagger's own die is 1d4; at level 5 Martial Arts' die is 1d6, larger
    # - Kai's dagger attack rolls 1d6, not 1d4. Dagger is also finesse, but
    # this specifically proves the monk-weapon path (not finesse) drives
    # the die swap - finesse alone would never touch damage_dice_sides.
    kai = _monk()
    kai.level = 5
    kai.inventory.append("dagger")
    kai.equipped_weapons = ["dagger"]
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai",
        verb="attack",
        target="goblin_1",
        item_or_spell="dagger",
        raw_text="I stab with my dagger",
    )
    resolve_action(state, action, _FixedRandom([12, 6]))  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 9  # die 6 (only legal on a 1d6) + DEX mod 3


def test_monk_weapon_die_is_not_reduced_when_already_bigger_than_martial_arts() -> None:
    # A quarterstaff's own die is 1d6; at level 1 Martial Arts' die is only
    # 1d4, smaller - stays the quarterstaff's own 1d6, never shrunk.
    kai = _monk()
    kai.inventory.append("quarterstaff")
    kai.equipped_weapons = ["quarterstaff"]
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai",
        verb="attack",
        target="goblin_1",
        item_or_spell="quarterstaff",
        raw_text="I strike with my quarterstaff",
    )
    resolve_action(state, action, _FixedRandom([12, 6]))  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 9  # die 6 (a legal 1d6 roll) + DEX mod 3


def test_flurry_of_blows_spends_ki_and_lands_two_full_damage_unarmed_strikes() -> None:
    # Bypasses level_up (same established pattern as this session's Divine
    # Smite tests) - directly pokes level/ki to isolate Flurry's own logic.
    kai = _monk()
    kai.level = 2
    kai.class_resources["ki"] = 2
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai",
        verb="flurry_of_blows",
        target="goblin_1",
        raw_text="I unleash a flurry of blows",
    )
    resolve_action(state, action, _FixedRandom([12, 3, 10, 2]))  # type: ignore[arg-type]

    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert len(attack_events) == 2
    assert all(e.payload["hit"] for e in attack_events)
    damage_events = [e for e in state.events if e.type == "damage_dealt"]
    # Both strikes get the full DEX mod (unlike Two-Weapon Fighting's
    # off-hand attack) - die 3 + 3 = 6, die 2 + 3 = 5.
    assert [e.payload["amount"] for e in damage_events] == [6, 5]
    assert kai.class_resources["ki"] == 1
    assert kai.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "kai"  # still his turn


def test_flurry_of_blows_rejected_below_level_2() -> None:
    kai = _monk()  # level defaults to 1 - Monks have no Ki at all yet
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai", verb="flurry_of_blows", target="goblin_1", raw_text="I try a flurry"
    )
    with pytest.raises(TurnEngineError, match="doesn't have Flurry of Blows"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_flurry_of_blows_rejected_with_no_ki_remaining() -> None:
    kai = _monk()
    kai.level = 2
    kai.class_resources["ki"] = 0
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai", verb="flurry_of_blows", target="goblin_1", raw_text="I try a flurry"
    )
    with pytest.raises(TurnEngineError, match="no ki uses remaining"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_flurry_of_blows_rejected_if_bonus_action_already_used() -> None:
    kai = _monk()
    kai.level = 2
    kai.class_resources["ki"] = 2
    kai.bonus_action_used = True
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai", verb="flurry_of_blows", target="goblin_1", raw_text="I try a flurry"
    )
    with pytest.raises(TurnEngineError, match="already used their bonus action"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# --- Monk: Martial Arts Strike (issue #36) --------------------------------------
# The third piece of level-1 Martial Arts (alongside the DEX-option/scaling-die
# tests above) - a free bonus-action unarmed strike, no ki, unlike Flurry of
# Blows which costs 1 ki and needs level 2+.


def test_martial_arts_strike_lands_one_full_damage_unarmed_strike_at_level_1() -> None:
    kai = _monk()  # level 1 - no ki at all, proving this doesn't need any
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai",
        verb="martial_arts_strike",
        target="goblin_1",
        raw_text="I throw in a quick punch",
    )
    resolve_action(state, action, _FixedRandom([12, 3]))  # type: ignore[arg-type]

    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert len(attack_events) == 1
    assert attack_events[0].payload["hit"] is True
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 6  # die 3 + DEX mod 3, same as the plain-attack case
    assert "ki" not in kai.class_resources  # no Ki resource exists yet at level 1
    assert kai.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "kai"  # still his turn


def test_martial_arts_strike_rejected_for_a_non_monk() -> None:
    fighter = _fighter()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(fighter, goblin)
    action = ParsedAction(
        actor=fighter.id,
        verb="martial_arts_strike",
        target="goblin_1",
        raw_text="I throw in a quick punch",
    )
    with pytest.raises(TurnEngineError, match="doesn't have Martial Arts"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_martial_arts_strike_rejected_if_bonus_action_already_used() -> None:
    kai = _monk()
    kai.bonus_action_used = True
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(kai, goblin)
    action = ParsedAction(
        actor="kai",
        verb="martial_arts_strike",
        target="goblin_1",
        raw_text="I throw in a quick punch",
    )
    with pytest.raises(TurnEngineError, match="already used their bonus action"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# --- Bardic Inspiration (issue #25) ---------------------------------------------


def _bard(position: Position | None = None) -> Character:
    # Human's +1-to-every-ability bonus: CHA15->16 (mod+3) -> 3 uses.
    return create_character(
        character_id="pip",
        name="Pip",
        race_index="human",
        class_index="bard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 10, "WIS": 13, "CHA": 15},
        chosen_skills=[
            "skill-performance",
            "skill-persuasion",
            "skill-deception",
            "skill-acrobatics",
            "skill-history",
            "skill-insight",
        ],
        chosen_spells=["healing-word", "thunderwave", "sleep", "charm-person"],
        position=position,
    )


def test_bardic_inspiration_die_boosts_an_allys_next_attack_roll_once() -> None:
    # Thorin (STR16->mod3, proficient longsword -> attack_bonus 5) vs a
    # goblin AC15. Natural 8 -> total 13 < 15 -> would miss - issue #53:
    # this pauses the attack (BardicChoicePending) instead of auto-boosting,
    # since a single non-Extra-Attack PC swing now lets the holder decide.
    # Opting in: Pip's banked 1d6 rolling 4 pushes it to 17 -> hit. Damage
    # die 5 + STR mod 3 = 8.
    pip = _bard(Position(x=0, y=0))
    thorin = _fighter(Position(x=1, y=0))
    goblin = _goblin("goblin_1", Position(x=1, y=0))
    state = _make_state(pip, thorin, goblin)
    inspire_action = ParsedAction(
        actor="pip",
        verb="bardic_inspiration",
        target="thorin",
        raw_text="I offer Thorin a rousing verse",
    )
    resolve_action(state, inspire_action, _FixedRandom([]))  # type: ignore[arg-type]

    assert thorin.bardic_inspiration_die == 6
    assert pip.class_resources["bardic_inspiration"] == 2
    assert pip.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "pip"  # bonus action, still pip's turn

    state.current_turn = state.turn_order.index("thorin")
    attack_action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack",
    )
    with pytest.raises(BardicChoicePending) as exc_info:
        resolve_action(state, attack_action, _FixedRandom([8]))  # type: ignore[arg-type]
    choice = exc_info.value.choice
    assert choice.holder_id == "thorin"
    assert choice.natural == 8
    assert choice.total_without_die == 13
    assert choice.defender_ac == 15
    # The die is untouched while the decision is pending - not yet spent.
    assert thorin.bardic_inspiration_die == 6

    resolve_pending_bardic_choice(state, choice, True, _FixedRandom([4, 5]), load_srd())  # type: ignore[arg-type]

    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert attack_events[-1].payload["roll_total"] == 17
    assert attack_events[-1].payload["hit"] is True
    assert attack_events[-1].payload["bardic_inspiration_die_sides"] == 6
    assert thorin.bardic_inspiration_die is None  # now spent
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 8
    assert thorin.bardic_inspiration_die is None  # consumed
    # resolve_pending_bardic_choice replicates resolve_action's own
    # victory-check/turn-advance tail (the original attempt never reached
    # it, aborted early by BardicChoicePending) - a real bug caught live
    # in this same pass: without it, the turn pointer would still show
    # "thorin" even though his attack (which always ends the turn) had
    # genuinely finished.
    assert state.turn_order[state.current_turn] != "thorin"

    # A second attack, no fresh inspiration - no more bonus applied.
    state.current_turn = state.turn_order.index("thorin")
    resolve_action(state, attack_action, _FixedRandom([8, 5]))
    second_attack_event = [e for e in state.events if e.type == "attack_roll"][-1]
    assert second_attack_event.payload["roll_total"] == 13  # 8 + 5, no bardic bonus this time
    assert "bardic_inspiration_die_sides" not in second_attack_event.payload


def test_declining_the_bardic_offer_finalizes_the_miss_and_keeps_the_die_banked() -> None:
    # Issue #53: real SRD only spends the die the moment it's actually
    # added to a roll - declining costs nothing, the holder can still use
    # it on a later roll this same encounter.
    pip = _bard(Position(x=0, y=0))
    thorin = _fighter(Position(x=1, y=0))
    goblin = _goblin("goblin_1", Position(x=1, y=0))
    state = _make_state(pip, thorin, goblin)
    resolve_action(
        state,
        ParsedAction(actor="pip", verb="bardic_inspiration", target="thorin", raw_text="x"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("thorin")
    attack_action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", item_or_spell="longsword", raw_text="x"
    )
    with pytest.raises(BardicChoicePending) as exc_info:
        resolve_action(state, attack_action, _FixedRandom([8]))  # type: ignore[arg-type]
    choice = exc_info.value.choice

    resolve_pending_bardic_choice(state, choice, False, _FixedRandom([]), load_srd())  # type: ignore[arg-type]

    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert attack_events[-1].payload["roll_total"] == 13
    assert attack_events[-1].payload["hit"] is False
    assert "bardic_inspiration_die_sides" not in attack_events[-1].payload
    assert thorin.bardic_inspiration_die == 6  # still banked, not spent
    # The miss still ends the turn, same as any other attack - declining
    # doesn't leave the turn pointer stuck.
    assert state.turn_order[state.current_turn] != "thorin"


def test_bardic_offer_not_made_on_a_natural_1_no_bonus_could_fix_it() -> None:
    pip = _bard(Position(x=0, y=0))
    thorin = _fighter(Position(x=1, y=0))
    goblin = _goblin("goblin_1", Position(x=1, y=0))
    state = _make_state(pip, thorin, goblin)
    resolve_action(
        state,
        ParsedAction(actor="pip", verb="bardic_inspiration", target="thorin", raw_text="x"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("thorin")
    attack_action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", item_or_spell="longsword", raw_text="x"
    )
    # No BardicChoicePending raised - a natural 1 always misses regardless
    # of any bonus, so there's nothing worth offering.
    resolve_action(state, attack_action, _FixedRandom([1]))  # type: ignore[arg-type]
    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert attack_events[-1].payload["natural"] == 1
    assert attack_events[-1].payload["hit"] is False
    assert thorin.bardic_inspiration_die == 6  # untouched, still banked


def test_bardic_offer_not_made_when_the_roll_already_hits() -> None:
    pip = _bard(Position(x=0, y=0))
    thorin = _fighter(Position(x=1, y=0))
    goblin = _goblin("goblin_1", Position(x=1, y=0))
    state = _make_state(pip, thorin, goblin)
    resolve_action(
        state,
        ParsedAction(actor="pip", verb="bardic_inspiration", target="thorin", raw_text="x"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("thorin")
    attack_action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", item_or_spell="longsword", raw_text="x"
    )
    # Natural 14 -> total 19 >= AC 15, already a hit - no need to offer,
    # the die is never touched.
    resolve_action(state, attack_action, _FixedRandom([14, 5]))  # type: ignore[arg-type]
    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert attack_events[-1].payload["hit"] is True
    assert "bardic_inspiration_die_sides" not in attack_events[-1].payload
    assert thorin.bardic_inspiration_die == 6  # never spent


def test_extra_attack_eligible_pc_still_auto_applies_the_die_immediately() -> None:
    # Issue #53's own documented scope narrowing: the pause-and-ask is only
    # offered for a plain single swing. A level-5+ Fighter/Barbarian/
    # Paladin/Ranger's Extra Attack still auto-applies the die on the first
    # roll that needs it, exactly like every attack roll behaved before
    # this issue - no BardicChoicePending here.
    pip = _bard(Position(x=0, y=0))
    thorin = _fighter(Position(x=1, y=0))
    thorin.level = 5  # Extra Attack eligible
    goblin = _goblin("goblin_1", Position(x=1, y=0))
    state = _make_state(pip, thorin, goblin)
    resolve_action(
        state,
        ParsedAction(actor="pip", verb="bardic_inspiration", target="thorin", raw_text="x"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("thorin")
    attack_action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", item_or_spell="longsword", raw_text="x"
    )
    # First swing: natural 8 -> 13, misses without the die - auto-boosted
    # by the banked 1d6 (rolling 4) to 17, a hit, exactly like the
    # pre-issue-#53 behavior (damage die 5). Second swing (Extra Attack):
    # natural 10 -> 15, hits (AC 15 exactly), no die left to apply (damage
    # die 3).
    resolve_action(state, attack_action, _FixedRandom([8, 4, 5, 10, 3]))  # type: ignore[arg-type]
    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert len(attack_events) == 2
    assert attack_events[0].payload["roll_total"] == 17
    assert attack_events[0].payload["bardic_inspiration_die_sides"] == 6
    assert thorin.bardic_inspiration_die is None


def test_bardic_inspiration_rejects_targeting_self() -> None:
    pip = _bard(Position(x=0, y=0))
    state = _make_state(pip, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="pip", verb="bardic_inspiration", target="pip", raw_text="I inspire myself"
    )
    with pytest.raises(TurnEngineError, match="other than yourself"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_bardic_inspiration_rejects_a_non_bard() -> None:
    thorin = _fighter(Position(x=0, y=0))
    ally = _fighter(Position(x=1, y=0))
    ally.id = "ally"
    state = _make_state(thorin, ally)
    action = ParsedAction(
        actor="thorin", verb="bardic_inspiration", target="ally", raw_text="I try to inspire"
    )
    with pytest.raises(TurnEngineError, match="doesn't have Bardic Inspiration"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_bardic_inspiration_rejects_with_no_uses_remaining() -> None:
    pip = _bard(Position(x=0, y=0))
    pip.class_resources["bardic_inspiration"] = 0
    thorin = _fighter(Position(x=1, y=0))
    state = _make_state(pip, thorin)
    action = ParsedAction(
        actor="pip", verb="bardic_inspiration", target="thorin", raw_text="I try to inspire"
    )
    with pytest.raises(TurnEngineError, match="no bardic inspiration uses remaining"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_bardic_inspiration_rejected_if_bonus_action_already_used() -> None:
    pip = _bard(Position(x=0, y=0))
    pip.bonus_action_used = True
    thorin = _fighter(Position(x=1, y=0))
    state = _make_state(pip, thorin)
    action = ParsedAction(
        actor="pip", verb="bardic_inspiration", target="thorin", raw_text="I try to inspire"
    )
    with pytest.raises(TurnEngineError, match="already used their bonus action"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


# --- Known-spell restriction (issue #30) ----------------------------------


def test_bard_can_cast_a_known_spell() -> None:
    # Pip's chosen_spells (see _bard's own create_character call) includes
    # healing-word - a real, chosen spell should resolve normally, not be
    # rejected by the new known-spells gate.
    pip = _bard(Position(x=0, y=0))
    thorin = _fighter(Position(x=1, y=0))
    thorin.hp = 1
    state = _make_state(pip, thorin)
    action = ParsedAction(
        actor="pip",
        verb="cast_spell",
        target="thorin",
        item_or_spell="healing word",
        raw_text="I sing a word of healing over Thorin",
    )
    resolve_action(state, action, _FixedRandom([3]))  # type: ignore[arg-type]

    assert pip.spell_slots[1] == 1  # started at 2
    assert thorin.hp > 1  # actually healed


def test_bard_cannot_cast_an_unknown_spell() -> None:
    # cure-wounds is a real, valid bard spell - just not one of Pip's own
    # chosen_spells - rejected the same way an outright-unknown spell name
    # already is, not silently allowed the way every class used to be.
    pip = _bard(Position(x=0, y=0))
    thorin = _fighter(Position(x=1, y=0))
    state = _make_state(pip, thorin)
    action = ParsedAction(
        actor="pip",
        verb="cast_spell",
        target="thorin",
        item_or_spell="cure wounds",
        raw_text="I try to cast cure wounds",
    )
    with pytest.raises(TurnEngineError, match="doesn't know"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
    assert pip.spell_slots[1] == 2  # never consumed - rejected before spending a slot


def test_bard_cantrip_unaffected_by_known_spell_restriction() -> None:
    # Cantrips (level 0) stay unrestricted - out of this issue's scope, same
    # as ClassDetail.cantrips already being unconditional. Vicious Mockery
    # is a real bard cantrip Pip never "chose" (cantrips aren't tracked in
    # known_spells at all), and still resolves.
    pip = _bard(Position(x=0, y=0))
    goblin = _goblin("goblin_1", Position(x=1, y=0))
    state = _make_state(pip, goblin)
    action = ParsedAction(
        actor="pip",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="vicious mockery",
        raw_text="I hurl an insult",
    )
    # DC = 8 + prof(2) + CHA mod(3) = 13. Natural 10 -> total 13 >= 13 -> save
    # succeeds, no damage - outcome doesn't matter for this test, only that
    # it resolves at all rather than raising "doesn't know".
    resolve_action(state, action, _FixedRandom([10, 4]))  # type: ignore[arg-type]
    assert any(e.type == "spell_cast" for e in state.events)
