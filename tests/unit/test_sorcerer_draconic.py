"""Issue #89: Draconic Resilience, the Draconic Bloodline's level-1 feature (the
SRD's only sorcerer origin, so every SRD sorcerer has it): +1 hit point at level 1
and with each level gained, and an unarmored AC of 13 + DEX. A level-1 human
sorcerer with DEX 14 (+1 racial -> 15, +2) and CON 13 (+1 -> 14, +2) went from AC 12
/ 8 HP to AC 15 / 9 HP."""

from __future__ import annotations

from src.engine.character_creation import create_character, draconic_resilience_hp, level_up
from src.engine.rules import armor_ac_breakdown
from src.engine.srd_loader import load_srd
from src.engine.state import Character


def _sorcerer(equipment: list[str] | None = None) -> Character:
    return create_character(
        character_id="ember",
        name="Ember",
        race_index="human",
        class_index="sorcerer",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 13, "INT": 10, "WIS": 12, "CHA": 15},
        chosen_skills=["skill-arcana", "skill-persuasion"],
        chosen_spells=["magic-missile", "burning-hands"],
        chosen_equipment=equipment or [],
    )


def test_an_unarmored_sorcerer_has_ac_13_plus_dex() -> None:
    ember = _sorcerer()
    assert ember.ac == 15  # 13 + DEX 2


def test_a_sorcerer_gets_one_extra_hit_point_at_level_one() -> None:
    ember = _sorcerer()
    assert ember.max_hp == ember.hp == 9  # d6 6 + CON 2 + Draconic Resilience 1


def test_draconic_resilience_is_a_sorcerer_only_feature() -> None:
    assert draconic_resilience_hp("sorcerer") == 1
    assert draconic_resilience_hp("wizard") == 0
    assert draconic_resilience_hp(None) == 0


def test_each_level_gained_adds_the_extra_hit_point() -> None:
    ember = _sorcerer()
    level_up(ember, load_srd(), spells_learned=["sleep"])
    # d6 average 4 + CON 2 + Draconic Resilience 1 = 7
    assert ember.max_hp == 9 + 7


def test_the_breakdown_names_the_source_and_a_shield_still_adds() -> None:
    srd = load_srd()
    plain = armor_ac_breakdown(None, None, 2, None, srd.equipment, class_index="sorcerer")
    assert plain == [("Draconic Resilience base", 13), ("DEX mod", 2)]
    shielded = armor_ac_breakdown(None, "shield", 2, None, srd.equipment, class_index="sorcerer")
    assert sum(v for _, v in shielded) == 17


def test_wearing_armor_replaces_it_and_other_classes_are_unchanged() -> None:
    srd = load_srd()
    armored = armor_ac_breakdown(
        "leather-armor", None, 2, None, srd.equipment, class_index="sorcerer"
    )
    assert sum(v for _, v in armored) == 13  # leather 11 + DEX 2, no Draconic base
    wizard = armor_ac_breakdown(None, None, 2, None, srd.equipment, class_index="wizard")
    assert sum(v for _, v in wizard) == 12


def test_mage_armor_does_not_stack_with_it() -> None:
    srd = load_srd()
    stacked = armor_ac_breakdown(
        None, None, 2, None, srd.equipment, class_index="sorcerer", mage_armor_active=True
    )
    assert stacked == [("Mage Armor base", 13), ("DEX mod", 2)]
