import pytest

from src.engine.character_creation import (
    CharacterCreationError,
    create_character,
    validate_standard_array,
)


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
