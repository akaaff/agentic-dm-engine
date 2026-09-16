"""Issue #23: the cheap, self-contained racial-trait subset - Halfling's
Lucky (reroll a natural 1), Half-Orc's Relentless Endurance (drop to 1 HP
instead of 0, once per long rest). Elf's Keen Senses and Half-Elf's Skill
Versatility are creation-time only - see test_character_creation.py."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import _apply_damage_and_handle_downing, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _halfling_rogue() -> Character:
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
        position=Position(x=0, y=0),
    )


def _make_state(*characters: Character) -> GameState:
    return GameState(
        encounter_id="racial_traits_test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
    )


def test_lucky_rerolls_a_natural_1_on_the_actors_own_attack_roll() -> None:
    # Fenwick (Halfling, DEX15->17 after +2 -> mod+3, shortsword finesse,
    # proficient -> attack_bonus 5). Natural 1 would normally always miss -
    # rerolled (Lucky) to 12 -> total 17 >= goblin's AC 15 -> hit. Damage
    # die 4 + DEX mod 3 = 7.
    srd = load_srd()
    fenwick = _halfling_rogue()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    goblin.hp = goblin.max_hp = 100
    state = _make_state(fenwick, goblin)
    action = ParsedAction(
        actor="fenwick",
        verb="attack",
        target="goblin_1",
        item_or_spell="shortsword",
        raw_text="I stab the goblin",
    )
    resolve_action(state, action, _FixedRandom([1, 12, 4]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 12  # the rerolled value, not the original 1
    assert attack_event.payload["hit"] is True
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 7


def test_a_non_halfling_never_rerolls_a_natural_1() -> None:
    # A single fed value proves no reroll was attempted - a second,
    # unconsumed value would desync _FixedRandom and fail loudly on the
    # next roll, not silently pass.
    srd = load_srd()
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=Position(x=0, y=0),
    )
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    state = _make_state(thorin, goblin)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I swing my longsword",
    )
    resolve_action(state, action, _FixedRandom([1]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 1
    assert attack_event.payload["hit"] is False


def test_relentless_endurance_drops_a_half_orc_to_1_hp_instead_of_0() -> None:
    srd = load_srd()
    grom = create_character(
        character_id="grom",
        name="Grom",
        race_index="half-orc",
        class_index="barbarian",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-intimidation"],
        position=Position(x=0, y=0),
    )
    grom.hp = 5
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=1, y=0))
    state = _make_state(grom, goblin)

    _apply_damage_and_handle_downing(
        state,
        goblin,
        grom,
        10,
        "slashing",
        _FixedRandom([]),  # type: ignore[arg-type]
        srd,
    )

    assert grom.hp == 1
    assert grom.used_relentless_endurance_this_rest is True
    assert not has_condition(grom, "unconscious")
    assert not grom.is_dead
    re_event = next(e for e in state.events if e.type == "relentless_endurance")
    assert re_event.payload["target"] == "grom"


def test_relentless_endurance_does_not_trigger_twice_in_the_same_rest() -> None:
    srd = load_srd()
    grom = create_character(
        character_id="grom",
        name="Grom",
        race_index="half-orc",
        class_index="barbarian",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-intimidation"],
        position=Position(x=0, y=0),
    )
    grom.hp = 5
    grom.used_relentless_endurance_this_rest = True  # already spent this rest
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=1, y=0))
    state = _make_state(grom, goblin)

    _apply_damage_and_handle_downing(
        state,
        goblin,
        grom,
        10,
        "slashing",
        _FixedRandom([]),  # type: ignore[arg-type]
        srd,
    )

    assert grom.hp == 0
    assert has_condition(grom, "unconscious")  # normal 0-HP handling applies instead
    assert not any(e.type == "relentless_endurance" for e in state.events)


def test_relentless_endurance_does_not_apply_to_a_non_half_orc() -> None:
    srd = load_srd()
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        position=Position(x=0, y=0),
    )
    thorin.hp = 5
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=1, y=0))
    state = _make_state(thorin, goblin)

    _apply_damage_and_handle_downing(
        state,
        goblin,
        thorin,
        10,
        "slashing",
        _FixedRandom([]),  # type: ignore[arg-type]
        srd,
    )

    assert thorin.hp == 0
    assert has_condition(thorin, "unconscious")
    assert not any(e.type == "relentless_endurance" for e in state.events)
