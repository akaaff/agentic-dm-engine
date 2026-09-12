import pytest

from src.engine.character_creation import (
    CharacterCreationError,
    class_equipment_options,
    create_character,
    validate_standard_array,
)
from src.engine.srd_loader import load_srd


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


def test_validate_standard_array_accepts_a_permutation() -> None:
    validate_standard_array({"STR": 8, "DEX": 15, "CON": 10, "INT": 14, "WIS": 13, "CHA": 12})


def test_validate_standard_array_rejects_invalid_scores() -> None:
    with pytest.raises(CharacterCreationError):
        validate_standard_array({"STR": 20, "DEX": 15, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8})


def test_create_human_fighter_end_to_end() -> None:
    # Hand-computed expected values:
    #   base STR15 DEX14 CON13 INT12 WIS10 CHA8 + Human's +1 to every score
    #   -> STR16 DEX15 CON14 INT13 WIS11 CHA9
    #   CON mod = (14-10)//2 = 2 -> HP = hit_die(10) + 2 = 12
    #   AC with chain-mail (base 16, no dex bonus) + shield (+2) = 18
    #   proficiency_bonus = 2 (flat at level 1)
    #   inventory = Acolyte's fixed kit (clothes-common, pouch) + chosen (chain-mail, shield)
    character = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["chain-mail", "shield"],
    )

    assert character.stats == {"STR": 16, "DEX": 15, "CON": 14, "INT": 13, "WIS": 11, "CHA": 9}
    assert character.hp == 12
    assert character.max_hp == 12
    assert character.ac == 18
    assert character.proficiency_bonus == 2
    assert character.speed == 30
    assert character.race == "Human"
    assert character.class_ == "Fighter"
    assert character.background == "Acolyte"
    assert character.spell_slots == {}
    assert sorted(character.inventory) == sorted(
        ["clothes-common", "pouch", "chain-mail", "shield"]
    )
    # chosen class skills + Acolyte's fixed background proficiencies
    assert sorted(character.skill_proficiencies) == sorted(
        ["skill-athletics", "skill-perception", "skill-insight", "skill-religion"]
    )


def test_create_elf_wizard_end_to_end() -> None:
    # Hand-computed expected values:
    #   base STR8 DEX14 CON12 INT15 WIS13 CHA10 + Elf's +2 DEX -> DEX16
    #   CON mod = (12-10)//2 = 1 -> HP = hit_die(6) + 1 = 7
    #   DEX mod = (16-10)//2 = 3 -> unarmored AC = 10 + 3 = 13
    #   spell_slots: wizard has 2 first-level slots at level 1
    #   inventory = Wizard's fixed kit (spellbook) + Acolyte's fixed kit (clothes-common, pouch)
    character = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
    )

    assert character.stats == {"STR": 8, "DEX": 16, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10}
    assert character.hp == 7
    assert character.max_hp == 7
    assert character.ac == 13
    assert character.speed == 30
    assert character.spell_slots == {1: 2}
    assert sorted(character.inventory) == sorted(["spellbook", "clothes-common", "pouch"])
    assert sorted(character.skill_proficiencies) == sorted(
        ["skill-arcana", "skill-history", "skill-insight", "skill-religion"]
    )


def test_create_monk_only_requires_its_two_skill_choices() -> None:
    # Regression guard: Monk's SRD proficiency_choices has a 2nd entry
    # ("one type of artisan's tools or one musical instrument") shaped as a
    # nested choice-within-a-choice rather than a flat reference list - the
    # only entry SRD-wide shaped that way. Iterating it the same way as
    # every other class's flat skill/instrument pools raised a raw
    # `KeyError: 'item'`, caught live creating a Monk in the actual app (see
    # CLAUDE.md). Tool/instrument proficiencies aren't modeled by this
    # project at all, so that entry is skipped - a Monk should only ever
    # need to supply their 2 real skill choices.
    character = create_character(
        character_id="kai",
        name="Kai",
        race_index="human",
        class_index="monk",
        background_index="acolyte",
        base_ability_scores={"STR": 10, "DEX": 15, "CON": 13, "INT": 8, "WIS": 14, "CHA": 12},
        chosen_skills=["skill-acrobatics", "skill-stealth"],
    )
    assert "skill-acrobatics" in character.skill_proficiencies
    assert "skill-stealth" in character.skill_proficiencies


def test_wizard_cannot_choose_gear_they_have_no_proficiency_with() -> None:
    # A Wizard is SRD-proficient with exactly 5 specific weapons (dagger,
    # dart, sling, quarterstaff, light crossbow) and no armor at all - a
    # martial weapon or any armor should be rejected, not silently allowed.
    with pytest.raises(CharacterCreationError, match="isn't proficient"):
        create_character(
            character_id="gandalf",
            name="Gandalf",
            race_index="human",
            class_index="wizard",
            background_index="acolyte",
            base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
            chosen_skills=["skill-arcana", "skill-history"],
            chosen_equipment=["longsword"],
        )


def test_wizard_can_choose_their_specific_proficient_weapons() -> None:
    # Wizards are proficient with these 5 by name, not via a broad
    # "simple-weapons" category (a real SRD quirk - see
    # class_equipment_options' docstring) - should be accepted.
    character = create_character(
        character_id="gandalf",
        name="Gandalf",
        race_index="human",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_equipment=["dagger", "crossbow-light"],
    )
    assert "dagger" in character.inventory
    assert "crossbow-light" in character.inventory


def test_wrong_number_of_skill_choices_rejected() -> None:
    with pytest.raises(CharacterCreationError):
        create_character(
            character_id="thorin",
            name="Thorin",
            race_index="human",
            class_index="fighter",
            background_index="acolyte",
            base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
            chosen_skills=["skill-athletics"],  # fighter requires exactly 2
        )


def test_invalid_skill_choice_rejected() -> None:
    with pytest.raises(CharacterCreationError):
        create_character(
            character_id="thorin",
            name="Thorin",
            race_index="human",
            class_index="fighter",
            background_index="acolyte",
            base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
            chosen_skills=["skill-arcana", "skill-athletics"],  # arcana isn't a fighter option
        )


def test_unknown_race_rejected() -> None:
    with pytest.raises(CharacterCreationError):
        create_character(
            character_id="x",
            name="X",
            race_index="not-a-real-race",
            class_index="fighter",
            background_index="acolyte",
            base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
            chosen_skills=["skill-athletics", "skill-perception"],
        )
