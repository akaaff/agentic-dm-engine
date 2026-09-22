"""Phase 9J: character leveling (scoped to roughly levels 1-5). Hand-computed
fixtures mirroring the style of test_character_creation.py/test_turn_engine.py -
`level_up` is exercised by calling it repeatedly on a level-1 character built
via the ordinary `create_character` path, exactly how a real caller would use
it (a level-1 Fighter/Wizard, then four level_up calls to reach level 5)."""

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import (
    CharacterCreationError,
    create_character,
    is_eligible_for_extra_attack,
    level_up,
)
from src.engine.encounter import build_encounter_state
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _fighter() -> Character:
    # Same fixture as test_character_creation.test_create_human_fighter_end_to_end:
    # final stats STR16 DEX15 CON14 INT13 WIS11 CHA9, CON mod +2, hit_die 10,
    # level-1 HP = 10 + 2 = 12 (already asserted there).
    return create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["chain-mail", "shield"],
    )


def _wizard() -> Character:
    # Same fixture as test_character_creation.test_create_elf_wizard_end_to_end:
    # final stats STR8 DEX16 CON12 INT15 WIS13 CHA10, CON mod +1, hit_die 6,
    # level-1 HP = 6 + 1 = 7, spell_slots {1: 2}.
    return create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "burning-hands", "mage-armor"],
    )


def test_level_up_fighter_to_level_5_hp_and_proficiency() -> None:
    srd = load_srd()
    fighter = _fighter()
    assert fighter.level == 1
    assert fighter.hp == 12
    assert fighter.proficiency_bonus == 2
    assert not is_eligible_for_extra_attack(fighter)

    # Each level's HP gain is the SRD's fixed "average" value for a d10:
    # 10 // 2 + 1 = 6, plus the CON mod (+2) computed above -> +8 HP/level.
    # 4 level-ups: 12 + 4*8 = 44.
    for expected_level in (2, 3, 4, 5):
        level_up(fighter, srd)
        assert fighter.level == expected_level
        assert fighter.hp == fighter.max_hp
        if expected_level < 5:
            assert not is_eligible_for_extra_attack(fighter)

    assert fighter.hp == 44
    assert fighter.max_hp == 44
    assert fighter.proficiency_bonus == 3  # +3 at level 5, per PROFICIENCY_BONUS_BY_LEVEL
    assert is_eligible_for_extra_attack(fighter)


def test_extra_attack_not_eligible_for_non_extra_attack_class() -> None:
    srd = load_srd()
    wizard = _wizard()
    for _ in range(4):
        level_up(wizard, srd)
    assert wizard.level == 5
    # Wizard isn't a fighter/barbarian/paladin/ranger - no Extra Attack ever.
    assert not is_eligible_for_extra_attack(wizard)


def test_level_up_wizard_spell_slots_progression() -> None:
    # Real PHB full-caster slot progression by character level:
    #   1: {1:2}  2: {1:3}  3: {1:4,2:2}  4: {1:4,2:3}  5: {1:4,2:3,3:2}
    srd = load_srd()
    wizard = _wizard()
    assert wizard.spell_slots == {1: 2}

    level_up(wizard, srd)
    assert wizard.level == 2
    assert wizard.spell_slots == {1: 3}

    level_up(wizard, srd)
    assert wizard.level == 3
    assert wizard.spell_slots == {1: 4, 2: 2}

    level_up(wizard, srd)
    assert wizard.level == 4
    assert wizard.spell_slots == {1: 4, 2: 3}

    level_up(wizard, srd)
    assert wizard.level == 5
    assert wizard.spell_slots == {1: 4, 2: 3, 3: 2}

    # HP: hit_die(6)//2 + 1 + CON mod(1) = 5/level; level-1 HP was 7.
    assert wizard.hp == 7 + 4 * 5 == 27
    assert wizard.max_hp == 27


def test_ability_score_improvement_accepts_legal_allocations() -> None:
    srd = load_srd()
    fighter = _fighter()
    for _ in range(2):
        level_up(fighter, srd)
    assert fighter.level == 3

    # The 3rd level_up call is the one that reaches level 4 (an ASI level) -
    # a legal +2-to-one-ability allocation is applied.
    level_up(fighter, srd, ability_score_increase={"STR": 2})
    assert fighter.level == 4
    assert fighter.stats["STR"] == 18  # was 16

    # A second, independent fighter proves the +1/+1-to-two-abilities shape
    # is equally legal.
    fighter2 = _fighter()
    for _ in range(2):
        level_up(fighter2, srd)
    level_up(fighter2, srd, ability_score_increase={"STR": 1, "DEX": 1})
    assert fighter2.stats["STR"] == 17  # was 16
    assert fighter2.stats["DEX"] == 16  # was 15


def test_ability_score_improvement_rejects_illegal_allocation() -> None:
    srd = load_srd()
    fighter = _fighter()
    for _ in range(2):
        level_up(fighter, srd)
    assert fighter.level == 3

    with pytest.raises(CharacterCreationError):
        level_up(fighter, srd, ability_score_increase={"STR": 3})
    # The stat mutation loop runs after validation - an illegal allocation
    # never touches the ability scores.
    assert fighter.stats["STR"] == 16


def test_ability_score_improvement_skipped_when_not_supplied() -> None:
    # Calling level_up with no ability_score_increase at an ASI level is
    # legal - the caller (presenting the choice to a human) simply hasn't
    # made one yet.
    srd = load_srd()
    fighter = _fighter()
    for _ in range(3):
        level_up(fighter, srd)
    assert fighter.level == 4
    assert fighter.stats["STR"] == 16  # unchanged


_INITIATIVE = [18, 10, 8, 3]  # thorin, elrond, goblin_1, goblin_2, per test_turn_engine.py


def _extra_attack_state() -> GameState:
    encounter = build_demo_encounter()
    fighter = _fighter()
    wizard = _wizard()
    return build_encounter_state(encounter, [fighter, wizard], _FixedRandom(_INITIATIVE))  # type: ignore[arg-type]


def test_resolve_action_extra_attack_produces_two_attack_roll_events() -> None:
    srd = load_srd()
    state = _extra_attack_state()
    thorin = state.characters["thorin"]
    for _ in range(4):
        level_up(thorin, srd)
    assert thorin.level == 5
    assert is_eligible_for_extra_attack(thorin)

    # Reuse the same "poke state" pattern test_turn_engine_conditions.py
    # already uses to get attacker and target into range without needing a
    # real move action first: goblin_1 onto thorin's square (distance 0,
    # well within chain-mail-and-shield-wearing Thorin's unarmed/longsword
    # reach - here the fighter's fists, since no weapon was equipped).
    state.characters["goblin_1"].position = thorin.position

    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        raw_text="I attack the goblin twice",
    )
    # Thorin has no weapon equipped (only armor was chosen at creation) -
    # unarmed strike attack_bonus = STR mod(3) + proficiency_bonus(3) = 6;
    # goblin_1's AC is 15. Two natural rolls of 5 and 3 both miss (11 and 9,
    # neither a natural 20) and consume no further randint calls for damage,
    # keeping this fixture simple while still proving two independent
    # attack_roll events were appended for one `attack` action.
    resolve_action(state, action, _FixedRandom([5, 3]))  # type: ignore[arg-type]

    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert len(attack_events) == 2
    assert [e.payload["natural"] for e in attack_events] == [5, 3]
    assert all(not e.payload["hit"] for e in attack_events)


def _monk() -> Character:
    # Same fixture as test_character_creation.test_create_monk_only_requires_
    # its_two_skill_choices: DEX15->mod2, WIS14->mod2 -> unarmored AC 14.
    return create_character(
        character_id="kai",
        name="Kai",
        race_index="human",
        class_index="monk",
        background_index="acolyte",
        base_ability_scores={"STR": 10, "DEX": 15, "CON": 13, "INT": 8, "WIS": 14, "CHA": 12},
        chosen_skills=["skill-acrobatics", "skill-stealth"],
    )


def test_monk_gets_no_ki_at_level_1() -> None:
    # Issue #24: SRD Monks don't get Ki until level 2.
    assert _monk().class_resources.get("ki", 0) == 0


def test_monk_ki_points_scale_with_level_via_level_up() -> None:
    # Issue #24: unlike Second Wind/Rage's level-1-fixed value, Ki scales
    # every level (ki points = monk level) - without level_up updating it,
    # Flurry of Blows would be permanently stuck at 0 for any leveled Monk.
    srd = load_srd()
    kai = _monk()
    level_up(kai, srd)  # -> level 2
    assert kai.class_resources["ki"] == 2
    level_up(kai, srd)  # -> level 3
    assert kai.class_resources["ki"] == 3


def test_monk_ac_recomputes_on_an_asi_that_boosts_wis() -> None:
    # Issue #24: a Monk's AC depends on WIS (Unarmored Defense), unlike
    # every other AC source in this project - an ASI boosting it should
    # actually move the Monk's AC, unlike everyone else's (left as a
    # documented, out-of-scope gap for non-Monks). Human's own +1-to-every-
    # ability racial bonus applies first: DEX15->16 (mod+3), WIS14->15
    # (mod+2) -> AC 10+3+2 = 15.
    srd = load_srd()
    kai = _monk()
    assert kai.ac == 15
    for i in range(3):
        # 3rd call reaches level 4, the ASI level here.
        level_up(kai, srd, ability_score_increase={"WIS": 2} if i == 2 else None)
    assert kai.level == 4
    assert kai.stats["WIS"] == 17  # 15 -> 17
    assert kai.ac == 16  # WIS mod 2 -> 3, AC 15 -> 16


def _barbarian() -> Character:
    # Human's +1-to-every-ability bonus: DEX13->14 (mod+2), CON14->15 (mod+2).
    return create_character(
        character_id="grosh",
        name="Grosh",
        race_index="human",
        class_index="barbarian",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 10, "CHA": 12},
        chosen_skills=["skill-athletics", "skill-intimidation"],
    )


def test_barbarian_ac_recomputes_on_an_asi_that_boosts_con() -> None:
    # Barbarian's own, different Unarmored Defense (10 + DEX + CON, a
    # shield doesn't disable it) - an ASI boosting CON should actually move
    # the Barbarian's AC the same way a Monk's WIS-boosting ASI does above.
    # DEX14 (mod+2) + CON14 (mod+2) -> AC 10+2+2 = 14 at level 1.
    srd = load_srd()
    grosh = _barbarian()
    assert grosh.equipped_armor is None
    assert grosh.ac == 14
    for i in range(3):
        # 3rd call reaches level 4, the ASI level here.
        level_up(grosh, srd, ability_score_increase={"CON": 2} if i == 2 else None)
    assert grosh.level == 4
    assert grosh.stats["CON"] == 17  # 15 -> 17
    assert grosh.ac == 15  # CON mod 2 -> 3, AC 14 -> 15


def _bard() -> Character:
    # Human's +1-to-every-ability bonus: CHA13->14 (mod+2).
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
    )


def test_bardic_inspiration_uses_recompute_on_an_asi_that_boosts_cha() -> None:
    # Issue #25: uses = CHA modifier (minimum 1), the one class_resource in
    # this project keyed off an ability score - an ASI raising CHA should
    # actually grant more uses, unlike Ki/Wild Shape's uses (which only
    # change with level, not with any particular stat). CHA 15 -> 16 after
    # Human's +1-to-every-ability bonus -> mod+3 at creation.
    srd = load_srd()
    pip = _bard()
    assert pip.class_resources["bardic_inspiration"] == 3
    for i in range(3):
        level_up(pip, srd, ability_score_increase={"CHA": 2} if i == 2 else None)
    assert pip.level == 4
    assert pip.stats["CHA"] == 18  # 16 -> 18
    assert pip.class_resources["bardic_inspiration"] == 4  # mod+3 -> mod+4


def test_known_spells_grow_by_the_class_table_when_a_new_spell_is_supplied() -> None:
    # Issue #30: Bard's SPELLS_KNOWN_BY_LEVEL is 4 at level 1, 5 at level 2 -
    # exactly one new spell should be learnable on the level-1 -> 2 call.
    srd = load_srd()
    pip = _bard()
    assert pip.known_spells == ["healing-word", "thunderwave", "sleep", "charm-person"]

    level_up(pip, srd, spells_learned=["cure-wounds"])
    assert pip.level == 2
    assert pip.known_spells == [
        "healing-word",
        "thunderwave",
        "sleep",
        "charm-person",
        "cure-wounds",
    ]


def test_known_spells_unchanged_when_not_supplied_at_a_growth_level() -> None:
    # Mirrors test_ability_score_improvement_skipped_when_not_supplied's
    # exact "the caller hasn't made a choice yet" shape.
    srd = load_srd()
    pip = _bard()
    level_up(pip, srd)
    assert pip.level == 2
    assert pip.known_spells == ["healing-word", "thunderwave", "sleep", "charm-person"]


def test_known_spells_growth_rejects_wrong_count() -> None:
    srd = load_srd()
    pip = _bard()
    with pytest.raises(CharacterCreationError, match="exactly 1"):
        level_up(pip, srd, spells_learned=["cure-wounds", "heroism"])


def test_known_spells_growth_rejects_a_spell_not_in_the_class_list() -> None:
    srd = load_srd()
    pip = _bard()
    with pytest.raises(CharacterCreationError, match="not a valid spell"):
        level_up(pip, srd, spells_learned=["fireball"])  # sorcerer/wizard only


def test_known_spells_growth_rejects_a_spell_already_known() -> None:
    srd = load_srd()
    pip = _bard()
    with pytest.raises(CharacterCreationError, match="already knows"):
        level_up(pip, srd, spells_learned=["healing-word"])
