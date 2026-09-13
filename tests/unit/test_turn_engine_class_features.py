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
