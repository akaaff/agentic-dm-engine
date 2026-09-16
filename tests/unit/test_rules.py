import pytest

from src.engine.encounter import monster_to_character
from src.engine.position import Position
from src.engine.rules import (
    ability_modifier,
    apply_damage,
    armor_ac,
    class_equipment_options,
    condition_attack_advantage,
    condition_attack_disadvantage,
    condition_check_disadvantage,
    effective_speed,
    has_lucky_trait,
    has_non_proficient_armor,
    has_relentless_endurance,
    is_class_proficient_with,
    is_monk_weapon,
    monk_martial_arts_die_sides,
    monster_action_range_feet,
    monster_damage_multiplier,
    monster_has_pack_tactics,
    monster_innate_spellcasting,
    monster_is_immune_to_condition,
    monster_is_undead_or_fiend,
    multiattack_sub_actions,
    normalize_skill_name,
    normalize_spell_name,
    resolve_attack,
    resolve_saving_throw,
    resolve_skill_check,
    saving_throw_bonus,
    set_exhaustion_level,
    skill_ability,
    spell_mechanic,
    spell_range_feet,
    weapon_range_feet,
)
from src.engine.srd_loader import load_srd
from src.engine.state import AbilityScore, Character, Condition, ConditionName


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _make_character(
    hp: int = 10,
    class_index: str | None = None,
    inventory: list[str] | None = None,
    conditions: list[ConditionName] | None = None,
    saving_throw_proficiencies: list[AbilityScore] | None = None,
    exhaustion_level: int = 0,
    char_id: str = "thorin",
    race_index: str | None = None,
) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=True,
        hp=hp,
        max_hp=hp,
        ac=15,
        position=Position(x=0, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 14, "INT": 10, "WIS": 11, "CHA": 8},
        proficiency_bonus=2,
        speed=30,
        race="Dwarf",
        class_="Fighter",
        background="Acolyte",
        class_index=class_index,
        inventory=inventory or [],
        conditions=[Condition(name=c) for c in (conditions or [])],
        saving_throw_proficiencies=saving_throw_proficiencies or [],
        exhaustion_level=exhaustion_level,
        race_index=race_index,
    )


# Hand-computed expected-value table for resolve_attack:
# natural roll -> (attack_bonus, defender_ac) -> total -> hit/crit
# 1  -> always miss regardless of bonus/AC
# 14 -> +5 -> 19 vs AC 15 -> hit, damage 1d8(=6)+3 = 9
# 10 -> +2 -> 12 vs AC 15 -> miss (not a natural 1, just below AC)
# 20 -> always hit + crit -> damage 2d8(=5,7)+3 = 15


def test_attack_hits_and_deals_expected_damage() -> None:
    rng = _FixedRandom([14, 6])  # attack roll, then damage die
    result = resolve_attack(
        defender_ac=15,
        attack_bonus=5,
        damage_dice_count=1,
        damage_dice_sides=8,
        damage_bonus=3,
        damage_type="slashing",
        rng=rng,  # type: ignore[arg-type]
    )
    assert result.hit is True
    assert result.critical is False
    assert result.attack_roll.total == 19
    assert result.damage == 9


def test_attack_misses_below_ac() -> None:
    rng = _FixedRandom([10])
    result = resolve_attack(
        defender_ac=15,
        attack_bonus=2,
        damage_dice_count=1,
        damage_dice_sides=8,
        damage_bonus=3,
        damage_type="slashing",
        rng=rng,  # type: ignore[arg-type]
    )
    assert result.hit is False
    assert result.damage is None


def test_natural_1_always_misses_even_with_huge_bonus() -> None:
    rng = _FixedRandom([1])
    result = resolve_attack(
        defender_ac=5,
        attack_bonus=20,
        damage_dice_count=1,
        damage_dice_sides=8,
        damage_bonus=3,
        damage_type="slashing",
        rng=rng,  # type: ignore[arg-type]
    )
    assert result.hit is False


def test_natural_20_crits_and_doubles_damage_dice_not_bonus() -> None:
    rng = _FixedRandom([20, 5, 7])  # attack roll, then two damage dice
    result = resolve_attack(
        defender_ac=25,  # would have missed on a normal 20 total, but nat 20 always hits
        attack_bonus=0,
        damage_dice_count=1,
        damage_dice_sides=8,
        damage_bonus=3,
        damage_type="slashing",
        rng=rng,  # type: ignore[arg-type]
    )
    assert result.hit is True
    assert result.critical is True
    assert result.damage == 5 + 7 + 3


def test_apply_damage_clamps_at_zero_and_returns_actual_loss() -> None:
    character = _make_character(hp=10)
    actual = apply_damage(character, 15)
    assert character.hp == 0
    assert actual == 10


def test_apply_damage_normal_case() -> None:
    character = _make_character(hp=10)
    actual = apply_damage(character, 4)
    assert character.hp == 6
    assert actual == 4


def test_saving_throw_success_and_failure() -> None:
    rng_pass = _FixedRandom([15])
    result, success = resolve_saving_throw(save_bonus=2, dc=17, rng=rng_pass)  # type: ignore[arg-type]
    assert result.total == 17
    assert success is True

    rng_fail = _FixedRandom([10])
    result, success = resolve_saving_throw(save_bonus=2, dc=17, rng=rng_fail)  # type: ignore[arg-type]
    assert success is False


def test_skill_check_success_and_failure() -> None:
    rng = _FixedRandom([8])
    result, success = resolve_skill_check(modifier=4, dc=12, rng=rng)  # type: ignore[arg-type]
    assert result.total == 12
    assert success is True


def test_ability_modifier_matches_srd_table() -> None:
    assert ability_modifier(10) == 0
    assert ability_modifier(16) == 3
    assert ability_modifier(8) == -1
    assert ability_modifier(7) == -2
    assert ability_modifier(1) == -5


def test_normalize_skill_name_handles_every_authored_spelling() -> None:
    assert normalize_skill_name("Perception") == "perception"
    assert normalize_skill_name("skill-perception") == "perception"
    assert normalize_skill_name("Sleight of Hand") == "sleight-of-hand"


def test_skill_ability_resolves_the_governing_ability_from_the_srd() -> None:
    srd = load_srd()
    assert skill_ability("athletics", srd) == "STR"
    assert skill_ability("skill-perception", srd) == "WIS"
    assert skill_ability("Persuasion", srd) == "CHA"


def test_skill_ability_rejects_an_unknown_skill() -> None:
    srd = load_srd()
    with pytest.raises(ValueError, match="Unknown skill"):
        skill_ability("juggling", srd)


def test_class_equipment_options_wizard_is_specific_weapons_only() -> None:
    srd = load_srd()
    options = set(class_equipment_options(srd.classes["wizard"], srd))
    assert options == {"dagger", "dart", "sling", "quarterstaff", "crossbow-light"}


def test_class_equipment_options_fighter_gets_everything() -> None:
    # "all-armor" + "martial-weapons" + "simple-weapons" + "shields" -
    # broad categories, not an enumerated list like Wizard's.
    srd = load_srd()
    options = set(class_equipment_options(srd.classes["fighter"], srd))
    assert "plate-armor" in options
    assert "shield" in options
    assert "longsword" in options  # martial
    assert "dagger" in options  # simple


def test_class_equipment_options_rogue_gets_hand_crossbow_despite_naming() -> None:
    # Regression guard for the one irregular alias: the SRD proficiency is
    # named "hand-crossbows" but the equipment index is "crossbow-hand"
    # (word order swapped) - see class_equipment_options' docstring.
    srd = load_srd()
    options = set(class_equipment_options(srd.classes["rogue"], srd))
    assert "crossbow-hand" in options


def test_is_class_proficient_with_checks_the_actual_equipped_item() -> None:
    srd = load_srd()
    wizard = _make_character(class_index="wizard")
    assert is_class_proficient_with(wizard, "dagger", srd) is True
    assert is_class_proficient_with(wizard, "longsword", srd) is False


def test_is_class_proficient_with_treats_no_class_as_proficient_with_anything() -> None:
    # Monsters have no class_index and attack via their own stat-block
    # actions, never through this weapon-lookup path - nothing to restrict.
    srd = load_srd()
    monster = _make_character(class_index=None)
    assert is_class_proficient_with(monster, "plate-armor", srd) is True


def test_has_non_proficient_armor_true_only_for_equipped_armor_outside_the_class_pool() -> None:
    # Issue #13: equipped_armor/equipped_shield are a real worn/carried
    # distinction now (mirroring the equipped-weapons feature's own earlier
    # fix to the Dueling check) - a non-proficient piece merely owned but
    # never equipped must NOT trigger this, only one actually worn.
    srd = load_srd()
    wizard_in_chainmail = _make_character(class_index="wizard", inventory=["chain-mail"])
    wizard_in_chainmail.equipped_armor = "chain-mail"
    assert has_non_proficient_armor(wizard_in_chainmail, srd) is True

    wizard_owns_but_never_equipped_chainmail = _make_character(
        class_index="wizard", inventory=["chain-mail"]
    )
    assert has_non_proficient_armor(wizard_owns_but_never_equipped_chainmail, srd) is False

    fighter_in_chainmail = _make_character(class_index="fighter", inventory=["chain-mail"])
    fighter_in_chainmail.equipped_armor = "chain-mail"
    assert has_non_proficient_armor(fighter_in_chainmail, srd) is False

    wizard_with_no_armor = _make_character(class_index="wizard", inventory=["dagger"])
    assert has_non_proficient_armor(wizard_with_no_armor, srd) is False


def test_monster_damage_multiplier_is_always_1_for_a_non_monster() -> None:
    srd = load_srd()
    pc = _make_character()
    assert monster_damage_multiplier(pc, "poison", srd) == 1.0


def test_monster_damage_multiplier_immune_resistant_vulnerable_normal() -> None:
    # Ghost: resistant to acid/fire/lightning/thunder + a compound
    # "bludgeoning, piercing, and slashing from nonmagical weapons" clause
    # (issue #18 - matched by substring, not exact equality); immune to
    # cold/necrotic/poison; no listed vulnerabilities in the vendored data.
    srd = load_srd()
    ghost = monster_to_character(srd.monsters["ghost"], "ghost_1", Position(x=0, y=0))
    assert monster_damage_multiplier(ghost, "cold", srd) == 0.0  # immune
    assert monster_damage_multiplier(ghost, "fire", srd) == 0.5  # resistant
    assert monster_damage_multiplier(ghost, "bludgeoning", srd) == 0.5  # compound clause
    assert monster_damage_multiplier(ghost, "radiant", srd) == 1.0  # not listed at all

    # Skeleton: vulnerable to bludgeoning (a plain single-type entry).
    skeleton = monster_to_character(srd.monsters["skeleton"], "skeleton_1", Position(x=0, y=0))
    assert monster_damage_multiplier(skeleton, "bludgeoning", srd) == 2.0


def test_monster_is_immune_to_condition_false_for_a_non_monster() -> None:
    srd = load_srd()
    pc = _make_character()
    assert monster_is_immune_to_condition(pc, "prone", srd) is False


def test_monster_is_immune_to_condition_matches_real_srd_data() -> None:
    srd = load_srd()
    ooze = monster_to_character(srd.monsters["gray-ooze"], "ooze_1", Position(x=0, y=0))
    assert monster_is_immune_to_condition(ooze, "prone", srd) is True
    assert monster_is_immune_to_condition(ooze, "grappled", srd) is False  # not in its list

    wolf = monster_to_character(srd.monsters["wolf"], "wolf_1", Position(x=0, y=0))
    assert monster_is_immune_to_condition(wolf, "prone", srd) is False


def test_monster_has_pack_tactics_matches_real_srd_data() -> None:
    srd = load_srd()
    pc = _make_character()
    assert monster_has_pack_tactics(pc, srd) is False  # non-monster always False

    wolf = monster_to_character(srd.monsters["wolf"], "wolf_1", Position(x=0, y=0))
    assert monster_has_pack_tactics(wolf, srd) is True

    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    assert monster_has_pack_tactics(goblin, srd) is False


def test_monster_is_undead_or_fiend_matches_real_srd_data() -> None:
    srd = load_srd()
    pc = _make_character()
    assert monster_is_undead_or_fiend(pc, srd) is False  # non-monster always False

    skeleton = monster_to_character(srd.monsters["skeleton"], "skeleton_1", Position(x=0, y=0))
    assert monster_is_undead_or_fiend(skeleton, srd) is True  # type "undead"

    imp = monster_to_character(srd.monsters["imp"], "imp_1", Position(x=0, y=0))
    assert monster_is_undead_or_fiend(imp, srd) is True  # type "fiend"

    wolf = monster_to_character(srd.monsters["wolf"], "wolf_1", Position(x=0, y=0))
    assert monster_is_undead_or_fiend(wolf, srd) is False  # type "beast"


def test_monster_innate_spellcasting_matches_real_srd_data() -> None:
    srd = load_srd()
    assert monster_innate_spellcasting(srd.monsters["goblin"]) is None  # no such ability at all

    drow_innate = monster_innate_spellcasting(srd.monsters["drow"])
    assert drow_innate is not None
    assert drow_innate["dc"] == 11
    assert [s["name"] for s in drow_innate["spells"]] == [
        "Dancing Lights",
        "Darkness",
        "Faerie Fire",
    ]


def test_spell_mechanic_classifies_real_srd_spells() -> None:
    srd = load_srd()
    assert spell_mechanic(srd.spells["fire-bolt"]) == "attack"
    assert spell_mechanic(srd.spells["vicious-mockery"]) == "save"
    assert spell_mechanic(srd.spells["cure-wounds"]) == "heal"
    assert spell_mechanic(srd.spells["dancing-lights"]) is None  # pure utility, no roll at all


def test_normalize_spell_name() -> None:
    assert normalize_spell_name("Ray of Enfeeblement") == "ray-of-enfeeblement"
    assert normalize_spell_name("fire-bolt") == "fire-bolt"


def test_has_lucky_trait_matches_race_index() -> None:
    assert has_lucky_trait(_make_character(race_index="halfling")) is True
    assert has_lucky_trait(_make_character(race_index="human")) is False
    assert has_lucky_trait(_make_character(race_index=None)) is False  # monster/unset


def test_has_relentless_endurance_matches_race_index() -> None:
    assert has_relentless_endurance(_make_character(race_index="half-orc")) is True
    assert has_relentless_endurance(_make_character(race_index="human")) is False
    assert has_relentless_endurance(_make_character(race_index=None)) is False


def test_monk_martial_arts_die_sides_scales_at_level_5() -> None:
    assert monk_martial_arts_die_sides(1) == 4
    assert monk_martial_arts_die_sides(4) == 4
    assert monk_martial_arts_die_sides(5) == 6
    assert monk_martial_arts_die_sides(20) == 6  # real table scales further, out of scope here


def test_is_monk_weapon_matches_real_srd_data() -> None:
    srd = load_srd()
    assert is_monk_weapon(srd.equipment["shortsword"]) is True  # Martial, but "monk"-tagged
    assert is_monk_weapon(srd.equipment["dagger"]) is True  # Simple melee
    assert is_monk_weapon(srd.equipment["quarterstaff"]) is True
    assert is_monk_weapon(srd.equipment["longsword"]) is False  # Martial, no "monk" tag
    assert is_monk_weapon(srd.equipment["longbow"]) is False  # ranged


def test_armor_ac_unarmored_is_10_plus_dex() -> None:
    srd = load_srd()
    assert armor_ac(None, None, dex_mod=3, fighting_style=None, equipment=srd.equipment) == 13


def test_armor_ac_light_armor_gets_full_uncapped_dex_bonus() -> None:
    srd = load_srd()
    # Leather Armor: base 11, dex_bonus True, no max_bonus.
    assert (
        armor_ac("leather-armor", None, dex_mod=4, fighting_style=None, equipment=srd.equipment)
        == 15
    )


def test_armor_ac_medium_armor_caps_the_dex_bonus() -> None:
    srd = load_srd()
    # Scale Mail: base 14, dex_bonus True, max_bonus 2 - a +4 Dex mod is
    # capped down to +2, not applied in full the way light armor's is.
    assert (
        armor_ac("scale-mail", None, dex_mod=4, fighting_style=None, equipment=srd.equipment) == 16
    )


def test_armor_ac_heavy_armor_ignores_dex_entirely() -> None:
    srd = load_srd()
    # Chain Mail: base 16, dex_bonus False - a high Dex mod contributes nothing.
    assert (
        armor_ac("chain-mail", None, dex_mod=4, fighting_style=None, equipment=srd.equipment) == 16
    )


def test_armor_ac_shield_adds_its_flat_bonus() -> None:
    srd = load_srd()
    assert (
        armor_ac("chain-mail", "shield", dex_mod=4, fighting_style=None, equipment=srd.equipment)
        == 18
    )
    # Shield alone (unarmored) still applies.
    assert armor_ac(None, "shield", dex_mod=1, fighting_style=None, equipment=srd.equipment) == 13


def test_armor_ac_defense_fighting_style_needs_actual_armor_not_just_a_shield() -> None:
    srd = load_srd()
    # +1 AC while wearing armor (Phase 9I) - a shield alone doesn't count,
    # per the SRD's literal text, matching the original _compute_ac's rule.
    assert (
        armor_ac(
            "leather-armor", None, dex_mod=2, fighting_style="defense", equipment=srd.equipment
        )
        == 14
    )
    assert (
        armor_ac(None, "shield", dex_mod=2, fighting_style="defense", equipment=srd.equipment) == 14
    )


def test_armor_ac_monk_unarmored_defense_adds_wis_mod_when_no_shield() -> None:
    srd = load_srd()
    # 10 + DEX mod(2) + WIS mod(3), unarmored, no shield (issue #24).
    assert (
        armor_ac(
            None,
            None,
            dex_mod=2,
            fighting_style=None,
            equipment=srd.equipment,
            class_index="monk",
            wis_mod=3,
        )
        == 15
    )


def test_armor_ac_monk_unarmored_defense_disabled_by_a_shield() -> None:
    srd = load_srd()
    # A shield breaks Unarmored Defense (SRD's literal "wielding no
    # shield" gate) - falls back to the ordinary 10 + DEX + shield formula,
    # no WIS mod, but the shield's own flat bonus still applies.
    assert (
        armor_ac(
            None,
            "shield",
            dex_mod=2,
            fighting_style=None,
            equipment=srd.equipment,
            class_index="monk",
            wis_mod=3,
        )
        == 14  # 10 + 2 (dex) + 2 (shield), no WIS
    )


def test_armor_ac_ignores_wis_mod_for_a_non_monk() -> None:
    srd = load_srd()
    assert (
        armor_ac(
            None,
            None,
            dex_mod=2,
            fighting_style=None,
            equipment=srd.equipment,
            class_index="fighter",
            wis_mod=3,
        )
        == 12  # 10 + 2 (dex) only
    )


def test_weapon_range_feet_melee_vs_ranged_vs_reach() -> None:
    srd = load_srd()
    assert weapon_range_feet(srd.equipment["longsword"]) == (5, None)
    assert weapon_range_feet(srd.equipment["longbow"]) == (150, 600)
    # Reach (glaive, whip - the only two SRD-wide) extends melee range by
    # 5ft; the equipment data's own range.normal is 5ft regardless of the
    # "reach" property, so this is the one place that distinction matters.
    assert weapon_range_feet(srd.equipment["glaive"]) == (10, None)


def test_monster_action_range_feet_parses_melee_and_ranged() -> None:
    srd = load_srd()
    kobold_actions = srd.monsters["kobold"]["actions"]
    dagger = next(a for a in kobold_actions if a["name"] == "Dagger")
    sling = next(a for a in kobold_actions if a["name"] == "Sling")
    assert monster_action_range_feet(dagger) == (5, None)
    assert monster_action_range_feet(sling) == (30, 120)


def test_monster_action_range_feet_falls_back_to_melee_for_unparseable_desc() -> None:
    assert monster_action_range_feet({"desc": "does something unusual"}) == (5, None)


def test_multiattack_sub_actions_parses_named_count_each_phrasing() -> None:
    # Real SRD text (giant-badger, CR 0.25 - a curated-roster-suitable low-CR
    # monster with a Multiattack action): "The badger makes two attacks: one
    # with its bite and one with its claws." Confirmed live against the
    # vendored JSON, not assumed - checked via a throwaway script scanning
    # srd.monsters.values() for a "Multiattack" action.
    srd = load_srd()
    actions = srd.monsters["giant-badger"]["actions"]
    multiattack = next(a for a in actions if a["name"] == "Multiattack")
    other_names = [a["name"] for a in actions if a["name"] != "Multiattack"]
    assert multiattack_sub_actions(multiattack["desc"], other_names) == [
        ("Bite", 1),
        ("Claws", 1),
    ]


def test_multiattack_sub_actions_ignores_unmatched_phrases() -> None:
    # A phrase naming something that isn't one of the monster's other real
    # actions (a typo, or a monster whose Multiattack desc references an
    # alternative like "two ranged attacks" rather than a named sub-action)
    # is skipped rather than fabricating a match.
    assert multiattack_sub_actions("makes two attacks: one with its stinger", ["Bite"]) == []


def test_multiattack_sub_actions_matches_case_insensitively_and_orders_by_appearance() -> None:
    desc = "The creature makes three attacks: two with its claws and one with its bite."
    assert multiattack_sub_actions(desc, ["Bite", "Claws"]) == [
        ("Claws", 2),
        ("Bite", 1),
    ]


def test_spell_range_feet_parses_feet_and_falls_back_for_touch() -> None:
    assert spell_range_feet("120 feet") == 120
    assert spell_range_feet("Touch") == 5


# --- Phase 9A: saving throws + condition mechanics ------------------------


def test_saving_throw_bonus_adds_proficiency_only_when_proficient() -> None:
    fighter = _make_character(saving_throw_proficiencies=["STR", "CON"])
    # STR16 -> mod+3, proficient -> +3+2=5; INT10 -> mod+0, not proficient -> 0
    assert saving_throw_bonus(fighter, "STR") == 5
    assert saving_throw_bonus(fighter, "INT") == 0


def test_condition_attack_advantage_from_target_conditions() -> None:
    attacker = _make_character(char_id="attacker")
    for condition in ("blinded", "paralyzed", "petrified", "restrained", "stunned", "unconscious"):
        target = _make_character(char_id="target", conditions=[condition])
        assert condition_attack_advantage(attacker, target, distance_feet=5) is True


def test_condition_attack_advantage_prone_target_only_within_melee_range() -> None:
    attacker = _make_character(char_id="attacker")
    target = _make_character(char_id="target", conditions=["prone"])
    assert condition_attack_advantage(attacker, target, distance_feet=5) is True
    assert condition_attack_advantage(attacker, target, distance_feet=30) is False


def test_condition_attack_advantage_from_invisible_attacker() -> None:
    attacker = _make_character(char_id="attacker", conditions=["invisible"])
    target = _make_character(char_id="target")
    assert condition_attack_advantage(attacker, target, distance_feet=5) is True


def test_condition_attack_disadvantage_from_attacker_conditions() -> None:
    target = _make_character(char_id="target")
    for condition in ("blinded", "poisoned", "restrained", "prone", "frightened"):
        attacker = _make_character(char_id="attacker", conditions=[condition])
        assert condition_attack_disadvantage(attacker, target, distance_feet=5) is True


def test_condition_attack_disadvantage_prone_target_beyond_melee_range() -> None:
    attacker = _make_character(char_id="attacker")
    target = _make_character(char_id="target", conditions=["prone"])
    assert condition_attack_disadvantage(attacker, target, distance_feet=30) is True
    assert condition_attack_disadvantage(attacker, target, distance_feet=5) is False


def test_condition_attack_disadvantage_from_invisible_target() -> None:
    attacker = _make_character(char_id="attacker")
    target = _make_character(char_id="target", conditions=["invisible"])
    assert condition_attack_disadvantage(attacker, target, distance_feet=5) is True


def test_condition_attack_disadvantage_from_exhaustion_level_3() -> None:
    attacker = _make_character(char_id="attacker", exhaustion_level=3)
    target = _make_character(char_id="target")
    assert condition_attack_disadvantage(attacker, target, distance_feet=5) is True


def test_condition_check_disadvantage_from_conditions_and_exhaustion() -> None:
    assert condition_check_disadvantage(_make_character(conditions=["poisoned"])) is True
    assert condition_check_disadvantage(_make_character(conditions=["frightened"])) is True
    assert condition_check_disadvantage(_make_character(exhaustion_level=1)) is True
    assert condition_check_disadvantage(_make_character()) is False


def test_effective_speed_grappled_and_exhaustion() -> None:
    assert effective_speed(_make_character(conditions=["grappled"])) == 0
    assert effective_speed(_make_character(exhaustion_level=2)) == 15  # 30 // 2
    assert effective_speed(_make_character(exhaustion_level=5)) == 0
    assert effective_speed(_make_character()) == 30


def test_set_exhaustion_level_clamps_and_kills_at_six() -> None:
    character = _make_character()
    set_exhaustion_level(character, 3)
    assert character.exhaustion_level == 3
    assert character.is_dead is False

    set_exhaustion_level(character, 9)  # clamps to 6
    assert character.exhaustion_level == 6
    assert character.is_dead is True

    set_exhaustion_level(character, -2)  # clamps to 0
    assert character.exhaustion_level == 0
