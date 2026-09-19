import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import (
    TurnEngineError,
    _monster_attack_params,
    _pc_attack_params,
    current_attack_summaries,
    parse_dice_notation,
    resolve_action,
)


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


def test_parse_dice_notation_variants() -> None:
    assert parse_dice_notation("1d6+2") == (1, 6, 2)
    assert parse_dice_notation("2d8") == (2, 8, 0)
    assert parse_dice_notation("1d4-1") == (1, 4, -1)


def test_parse_dice_notation_rejects_garbage() -> None:
    with pytest.raises(TurnEngineError):
        parse_dice_notation("not dice")


def test_pc_attack_params_non_finesse_melee_uses_strength() -> None:
    srd = load_srd()
    fighter = _two_person_party()[0]
    # STR16 -> mod3, proficiency_bonus2 -> attack_bonus 5; damage 1d8, bonus = STR mod 3
    params = _pc_attack_params(fighter, "longsword", srd)
    assert params.attack_bonus == 5
    assert (params.damage_dice_count, params.damage_dice_sides, params.damage_bonus) == (1, 8, 3)
    assert params.damage_type == "slashing"
    # Debug-mode breakdown (issue #38): confirms proficiency was actually
    # applied, not just implied by the total.
    assert params.attack_bonus_breakdown == [("STR mod", 3), ("proficiency", 2)]


def test_pc_attack_params_finesse_uses_better_of_str_or_dex() -> None:
    srd = load_srd()
    wizard = _two_person_party()[1]
    # DEX16 -> mod3 beats STR8 -> mod-1; attack_bonus = 3+2=5, damage bonus = 3
    params = _pc_attack_params(wizard, "dagger", srd)
    assert params.attack_bonus == 5
    assert params.damage_bonus == 3
    assert params.damage_type == "piercing"


def test_pc_attack_params_drops_proficiency_bonus_when_not_proficient() -> None:
    # A Wizard isn't proficient with a longsword (not one of their 5 named
    # weapons, and they have no "martial-weapons"/"simple-weapons" category
    # either - see rules.class_equipment_options). Longsword isn't finesse,
    # so STR applies even though DEX is better here: STR8 -> mod -1, and
    # with no proficiency bonus the attack_bonus is just that mod, not -1+2.
    srd = load_srd()
    wizard = _two_person_party()[1]
    # Phase C: attack resolution only matches equipped_weapons now, not the
    # whole inventory - give the wizard a longsword she owns but wouldn't
    # normally equip, to keep exercising the proficiency-bonus-drop branch
    # this test is actually about (ownership, not proficiency, is Phase C's
    # own new gate).
    wizard.inventory.append("longsword")
    wizard.equipped_weapons = ["longsword"]
    params = _pc_attack_params(wizard, "longsword", srd)
    assert params.attack_bonus == -1
    # Debug-mode breakdown (issue #38): confirms proficiency was correctly
    # *withheld*, not silently missing.
    assert params.attack_bonus_breakdown == [("STR mod", -1), ("proficiency (none)", 0)]


def test_current_attack_summaries_matches_pc_attack_params_for_a_single_weapon() -> None:
    # Proactive UX ask: the character sheet's "current attack/damage"
    # display must never drift from what an actual attack resolves - this
    # reuses _pc_attack_params directly, so confirming it matches that
    # function's own output for the identical inputs is really confirming
    # there's only ever one computation, not two that happen to agree.
    srd = load_srd()
    fighter = _two_person_party()[0]
    expected = _pc_attack_params(fighter, None, srd)

    summaries = current_attack_summaries(fighter, srd)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.source_name == expected.source_name == "Longsword"
    assert summary.attack_bonus == expected.attack_bonus
    assert summary.attack_bonus_breakdown == expected.attack_bonus_breakdown
    assert (summary.damage_dice_count, summary.damage_dice_sides, summary.damage_bonus) == (
        expected.damage_dice_count,
        expected.damage_dice_sides,
        expected.damage_bonus,
    )
    assert summary.damage_type == expected.damage_type


def test_current_attack_summaries_includes_a_reduced_damage_off_hand_entry() -> None:
    # Dual-wielding two light weapons: the off-hand entry must show the
    # same reduced damage_bonus offhand_attack's real resolution applies
    # (include_ability_damage_bonus=False), not the main hand's own numbers
    # copied twice.
    srd = load_srd()
    fighter = _two_person_party()[0]
    fighter.inventory.append("dagger")
    fighter.equipped_weapons = ["dagger", "dagger"]

    summaries = current_attack_summaries(fighter, srd)

    assert len(summaries) == 2
    main, off = summaries
    assert main.source_name == "Dagger"
    assert off.source_name == "Dagger (off-hand)"
    # Main hand includes the STR/DEX mod in damage; off-hand doesn't.
    assert main.damage_bonus > off.damage_bonus
    assert main.attack_bonus == off.attack_bonus  # the attack roll itself is unaffected


def test_current_attack_summaries_falls_back_to_unarmed_strike() -> None:
    srd = load_srd()
    fighter = _two_person_party()[0]
    fighter.inventory = []
    fighter.equipped_weapons = []

    summaries = current_attack_summaries(fighter, srd)

    assert len(summaries) == 1
    assert summaries[0].source_name == "unarmed strike"


def test_current_attack_summaries_is_empty_for_a_monster() -> None:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))

    assert current_attack_summaries(goblin, srd) == []


def test_pc_attack_params_falls_back_to_unarmed_strike() -> None:
    srd = load_srd()
    fighter = _two_person_party()[0]
    fighter.inventory = []  # strip the longsword to force the unarmed path
    fighter.equipped_weapons = []  # Phase C: equipped_weapons, not inventory, is what's checked now
    params = _pc_attack_params(fighter, None, srd)
    assert params.source_name == "unarmed strike"
    assert params.damage_dice_count == 0
    assert params.damage_type == "bludgeoning"


def test_pc_attack_params_matches_a_real_weapon_with_an_invented_adjective() -> None:
    # Regression for a live --autoplay bug: a companion's persona-driven
    # free-text turn declaration described her weapon as a "silvered
    # longbow" - flavor the LLM invented (no such SRD variant exists, and
    # her actual inventory only ever holds a plain "longbow", Ranger's
    # fixed class starting equipment). item_or_spell carries that phrase
    # through to _pc_attack_params verbatim, and an exact-index lookup for
    # "silvered longbow" fails - the same word-order/extra-word mismatch
    # Day 14 already hit and fixed for use_item's "healing potion", now
    # hitting attack's weapon lookup too. This must still resolve to the
    # real Longbow entry rather than rejecting the attack outright.
    srd = load_srd()
    silvana = create_character(
        character_id="companion_silvana",
        name="Silvana Wren",
        race_index="elf",
        class_index="ranger",
        background_index="acolyte",
        base_ability_scores={"STR": 12, "DEX": 15, "CON": 13, "INT": 10, "WIS": 14, "CHA": 8},
        chosen_skills=["skill-perception", "skill-stealth", "skill-survival"],
        chosen_equipment=["leather-armor"],
        is_companion=True,
    )
    # DEX 15 base + elf's +2 racial = 17 -> mod +3; Ranger is proficient
    # with martial weapons (Longbow is Martial/Ranged) -> +2 proficiency
    # bonus at level 1. Ranged weapon uses DEX, not STR, for both rolls.
    params = _pc_attack_params(silvana, "silvered longbow", srd)
    assert params.source_name == "Longbow"
    assert params.attack_bonus == 5
    assert (params.damage_dice_count, params.damage_dice_sides, params.damage_bonus) == (1, 8, 3)
    assert params.damage_type == "piercing"
    assert (params.range_normal_feet, params.range_long_feet) == (150, 600)


def test_pc_attack_params_rejects_a_name_matching_no_real_weapon() -> None:
    srd = load_srd()
    fighter = _two_person_party()[0]
    with pytest.raises(TurnEngineError):
        _pc_attack_params(fighter, "my fireproof toaster", srd)


def test_monster_attack_params_defaults_to_first_action() -> None:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    params = _monster_attack_params(goblin, None, srd)
    assert params.source_name == "Scimitar"
    assert params.attack_bonus == 4
    assert (params.damage_dice_count, params.damage_dice_sides, params.damage_bonus) == (1, 6, 2)
    # Debug-mode breakdown (issue #38): a monster's attack bonus is one
    # precomputed SRD number with no ability-mod/proficiency split available
    # in the data - reported as a single opaque entry, not fabricated.
    assert params.attack_bonus_breakdown == [("attack bonus (stat block)", 4)]


def test_monster_attack_params_can_select_named_action() -> None:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    params = _monster_attack_params(goblin, "Shortbow", srd)
    assert params.source_name == "Shortbow"
    assert params.damage_type == "piercing"


def test_monster_attack_params_rejects_unknown_action_name() -> None:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    with pytest.raises(TurnEngineError):
        _monster_attack_params(goblin, "Fireball", srd)


def _build_demo_state(rng_values: list[int]):  # type: ignore[no-untyped-def]
    encounter = build_demo_encounter()
    party = _two_person_party()
    return build_encounter_state(encounter, party, _FixedRandom(rng_values))  # type: ignore[arg-type]


def test_resolve_action_rejects_action_from_the_wrong_actor() -> None:
    state = _build_demo_state([18, 10, 8, 3])
    off_turn_actor = state.turn_order[1]
    action = ParsedAction(actor=off_turn_actor, verb="end_turn", raw_text="out of turn")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_resolve_action_rejects_a_verb_outside_the_known_set() -> None:
    # Every verb in ParsedAction's Literal is dispatched as of Day 14, so
    # this fallback is only reachable by bypassing pydantic validation
    # (model_construct) - still worth a test as a safety net for whenever a
    # future verb is added to the schema before the engine handles it.
    state = _build_demo_state([18, 10, 8, 3])
    current_actor = state.turn_order[0]
    action = ParsedAction.model_construct(actor=current_actor, verb="teleport", raw_text="?")
    with pytest.raises(NotImplementedError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_resolve_action_invalid_does_not_advance_the_turn() -> None:
    # The DM not understanding an action isn't a system error and doesn't
    # cost the actor their turn - they can just try again.
    state = _build_demo_state([18, 10, 8, 3])
    current_actor = state.turn_order[0]
    action = ParsedAction(actor=current_actor, verb="invalid", raw_text="gibberish")
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert state.turn_order[state.current_turn] == current_actor
    assert state.events[-1].type == "action_invalid"
    assert state.events[-1].payload["raw_text"] == "gibberish"


def test_resolve_action_rejects_a_downed_actor() -> None:
    state = _build_demo_state([18, 10, 8, 3])
    current_actor_id = state.turn_order[0]
    state.characters[current_actor_id].hp = 0
    action = ParsedAction(actor=current_actor_id, verb="end_turn", raw_text="downed")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_resolve_action_rejects_a_pc_attacking_another_pc() -> None:
    # Regression guard: found live on Day 15's first autoplay run, a
    # companion's free-text turn named an ally as its attack target.
    state = _build_demo_state([18, 10, 8, 3])
    assert state.turn_order[0] == "thorin"
    action = ParsedAction(
        actor="thorin", verb="attack", target="elrond", raw_text="I attack Elrond"
    )
    with pytest.raises(TurnEngineError, match="same side"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_resolve_action_rejects_a_monster_attacking_another_monster() -> None:
    state = _build_demo_state([18, 10, 8, 3])
    state.current_turn = state.turn_order.index("goblin_1")
    action = ParsedAction(
        actor="goblin_1", verb="attack", target="goblin_2", raw_text="the goblin attacks its ally"
    )
    with pytest.raises(TurnEngineError, match="same side"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_attack_damage_reduced_by_monster_resistance() -> None:
    # Issue #18, end-to-end through a real attack resolution (not just the
    # pure rules.monster_damage_multiplier unit tests). Swap goblin_1's own
    # monster_index to "ghost" - resistant to "bludgeoning, piercing, and
    # slashing from nonmagical weapons" (the exact compound-clause shape
    # the substring-matching was built for), AC 11, HP 45 (plenty of
    # buffer). Thorin's longsword: STR16(human+1)->mod3, proficient->+2,
    # attack_bonus 5, natural 10 -> total 15 vs AC 11 -> hit, not a crit.
    # Damage: die natural 6 + STR mod 3 = 9 raw: halved (resistant, slashing
    # is nonmagical here) -> 4.
    state = _build_demo_state([18, 10, 8, 3])
    state.characters["goblin_1"].monster_index = "ghost"
    # The demo encounter's own spawn points put them 10ft apart - adjacent
    # for a real melee hit, same "not what this test is about" fix already
    # used elsewhere for range enforcement.
    state.characters["goblin_1"].position = Position(
        x=state.characters["thorin"].position.x + 1, y=state.characters["thorin"].position.y
    )
    action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", raw_text="I swing my longsword"
    )
    resolve_action(state, action, _FixedRandom([10, 6]))  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 4


def test_attack_damage_zeroed_by_monster_immunity() -> None:
    # Ghost is immune to poison - use_item/cast_spell-style poison damage
    # isn't reachable via a plain weapon attack in this project, so this
    # directly exercises _apply_damage_and_handle_downing's own multiplier
    # step by calling it the same way _resolve_attack does, rather than
    # contriving a poison-damage weapon that may not exist in the SRD data.
    from src.engine.turn_engine import _apply_damage_and_handle_downing

    state = _build_demo_state([18, 10, 8, 3])
    ghost = state.characters["goblin_1"]
    ghost.monster_index = "ghost"
    thorin = state.characters["thorin"]
    srd = load_srd()

    _apply_damage_and_handle_downing(state, thorin, ghost, 20, "poison", _FixedRandom([]), srd)  # type: ignore[arg-type]

    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 0
    assert ghost.hp == ghost.max_hp  # untouched


def test_pack_tactics_grants_advantage_when_an_ally_is_adjacent_to_the_target() -> None:
    # Issue #19. Two wolves both within 5ft of Thorin - wolf_1 (the
    # attacker) qualifies for Pack Tactics because wolf_2 (an ally) is also
    # adjacent to the shared target. Thorin's AC is set absurdly high so
    # the attack always misses regardless of the roll, avoiding any damage-
    # die RNG the test doesn't care about. Advantage means 2 d20s are
    # rolled and the higher is kept - feeding [5, 18] and asserting the
    # recorded natural is 18 proves advantage actually applied (without it,
    # only the first value would ever be consumed/kept).
    srd = load_srd()
    thorin = _two_person_party()[0]
    thorin.position = Position(x=0, y=0)
    thorin.ac = 99
    wolf_1 = monster_to_character(srd.monsters["wolf"], "wolf_1", Position(x=1, y=0))
    wolf_2 = monster_to_character(srd.monsters["wolf"], "wolf_2", Position(x=0, y=1))
    state = GameState(
        encounter_id="pack_tactics_test",
        characters={"thorin": thorin, "wolf_1": wolf_1, "wolf_2": wolf_2},
        turn_order=["wolf_1", "wolf_2", "thorin"],
        current_turn=0,
        round=1,
    )
    action = ParsedAction(actor="wolf_1", verb="attack", target="thorin", raw_text="the wolf bites")
    resolve_action(state, action, _FixedRandom([5, 18]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 18


def test_pack_tactics_gives_no_advantage_without_an_adjacent_ally() -> None:
    # Same setup, but wolf_2 is far away - no Pack Tactics advantage, so
    # only a single d20 is ever consumed. Feeding a second, unused value
    # would desync _FixedRandom and fail loudly on the next roll rather
    # than silently passing - so this only passes if exactly one is used.
    srd = load_srd()
    thorin = _two_person_party()[0]
    thorin.position = Position(x=0, y=0)
    thorin.ac = 99
    wolf_1 = monster_to_character(srd.monsters["wolf"], "wolf_1", Position(x=1, y=0))
    wolf_2 = monster_to_character(srd.monsters["wolf"], "wolf_2", Position(x=9, y=9))
    state = GameState(
        encounter_id="pack_tactics_test",
        characters={"thorin": thorin, "wolf_1": wolf_1, "wolf_2": wolf_2},
        turn_order=["wolf_1", "wolf_2", "thorin"],
        current_turn=0,
        round=1,
    )
    action = ParsedAction(actor="wolf_1", verb="attack", target="thorin", raw_text="the wolf bites")
    resolve_action(state, action, _FixedRandom([5]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 5


def test_attack_gets_disadvantage_from_non_proficient_armor() -> None:
    # Elrond (Wizard) has no armor proficiency at all - simulate exactly
    # the authoring mistake this mechanic exists to catch (a companion
    # shipped with armor its class can't use - see CLAUDE.md, Sister
    # Mira's chain-mail) by giving him chain-mail directly. His attack
    # should roll with disadvantage even though the target isn't dodging
    # and he isn't being helped - two d20s [15, 3], keep the lower (3).
    state = _build_demo_state([18, 10, 8, 3])
    elrond = state.characters["elrond"]
    elrond.inventory.append("chain-mail")
    elrond.equipped_armor = "chain-mail"  # issue #13: only equipped armor counts
    state.current_turn = state.turn_order.index("elrond")
    action = ParsedAction(
        actor="elrond", verb="attack", target="goblin_1", item_or_spell="dagger", raw_text="stab"
    )
    resolve_action(state, action, _FixedRandom([15, 3]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 3


def test_attack_rejected_when_target_out_of_melee_range() -> None:
    # thorin(0,1) and goblin_1(2,1) are 10ft apart in the demo encounter -
    # out of range for a longsword's 5ft reach. Caught live: a combat grid
    # showed exactly this geometry (attacker and target several squares
    # apart) with a melee hit narrated anyway - this engine never checked
    # range at all until now.
    state = _build_demo_state([18, 10, 8, 3])
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I attack the goblin",
    )
    with pytest.raises(TurnEngineError, match="out of range"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_attack_rejected_beyond_a_ranged_weapons_long_range() -> None:
    state = _build_demo_state([18, 10, 8, 3])
    # Phase C: thorin's default loadout is his starting longsword - give him
    # a longbow (two-handed, so it must be his only equipped weapon) to
    # exercise this test's actual subject, ranged-weapon range enforcement.
    state.characters["thorin"].inventory.append("longbow")
    state.characters["thorin"].equipped_weapons = ["longbow"]
    state.characters["thorin"].position = Position(x=0, y=0)
    state.characters["goblin_1"].position = Position(x=200, y=0)  # 1000ft > longbow's 600ft long
    action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", item_or_spell="longbow", raw_text="shoot"
    )
    with pytest.raises(TurnEngineError, match="out of range"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_move_rejected_onto_a_square_already_occupied_by_another_character() -> None:
    # Caught live right after the range-enforcement fix landed: a
    # companion's free-text-declared move landed exactly on the human
    # player's own square (0,1)->(1,2 is elrond's square in the demo
    # encounter), and the two characters were visually stacked on one
    # combat-grid token, with the second one completely hidden.
    state = _build_demo_state([18, 10, 8, 3])
    action = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I move next to Elrond",
        params={"path": [{"x": 1, "y": 2}]},
    )
    with pytest.raises(TurnEngineError, match="already occupied"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_attack_gets_disadvantage_beyond_a_ranged_weapons_normal_range() -> None:
    # 155ft: beyond a longbow's 150ft normal range but within its 600ft
    # long range - SRD imposes disadvantage rather than rejecting the shot
    # outright (unlike melee, which has no such tier). Two d20s [15, 3],
    # keep the lower (3).
    state = _build_demo_state([18, 10, 8, 3])
    state.characters["thorin"].inventory.append("longbow")
    state.characters["thorin"].equipped_weapons = ["longbow"]
    state.characters["thorin"].position = Position(x=0, y=0)
    state.characters["goblin_1"].position = Position(x=31, y=0)
    action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", item_or_spell="longbow", raw_text="shoot"
    )
    resolve_action(state, action, _FixedRandom([15, 3]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 3
