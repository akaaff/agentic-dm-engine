"""Phase 9F: Multiattack (a monster's Multiattack action rolls each of its
named sub-attacks as its own attack_roll event) and ranged-while-engaged
disadvantage (a ranged attack rolls with disadvantage while a hostile is
within 5ft of the attacker).
"""

from __future__ import annotations

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import resolve_action


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


def _build_demo_state(rng_values: list[int]) -> GameState:
    encounter = build_demo_encounter()
    party = _two_person_party()
    return build_encounter_state(encounter, party, _FixedRandom(rng_values))  # type: ignore[arg-type]


# turn_order for [18, 10, 8, 3] is always thorin, elrond, goblin_1, goblin_2
# (established in test_turn_engine.py).
_INITIATIVE = [18, 10, 8, 3]


def _build_multiattack_state(rng_values: list[int]) -> GameState:
    """A minimal 2-character encounter: a real SRD monster with a
    Multiattack action (giant-badger, CR 0.25 - one of the low-CR monsters
    this project's roster would plausibly curate) versus one PC target, 5ft
    apart (in the badger's Bite/Claws melee reach)."""
    srd = load_srd()
    badger = monster_to_character(
        srd.monsters["giant-badger"], "giant-badger_1", Position(x=0, y=0)
    )
    target = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=Position(x=0, y=1),
    )
    target.hp = target.max_hp = 30  # generous HP so both sub-attacks resolve
    battle_map = BattleMap(
        width=2,
        height=2,
        terrain=[["floor", "floor"], ["floor", "floor"]],
        spawn_points={},
    )
    return GameState(
        encounter_id="multiattack_test",
        characters={badger.id: badger, target.id: target},
        turn_order=[badger.id, target.id],
        current_turn=0,
        round=1,
        battle_map=battle_map,
    )


def test_multiattack_resolves_each_named_sub_action_as_its_own_attack_roll() -> None:
    # giant-badger's Multiattack desc: "The badger makes two attacks: one
    # with its bite and one with its claws." - default action selection
    # (no item_or_spell) picks actions[0], which is Multiattack for this
    # monster, exactly as ordered in the vendored SRD JSON.
    #
    # RNG sequence: Bite's attack roll (no adv/dis - 1 d20) -> 15, hits
    # (attack_bonus 3 -> total 18 vs AC 16); Bite's damage (1d6) -> 4 (total
    # 5 with the +1 modifier). Claws' attack roll -> 12 (total 15, still a
    # hit); Claws' damage (2d4) -> 3, 2 (total 6 with the +1 modifier).
    state = _build_multiattack_state([])
    action = ParsedAction(
        actor="giant-badger_1", verb="attack", target="thorin", raw_text="the badger attacks"
    )
    resolve_action(state, action, _FixedRandom([15, 4, 12, 3, 2]))  # type: ignore[arg-type]

    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert len(attack_events) == 2
    assert [e.payload["source"] for e in attack_events] == ["Bite", "Claws"]
    assert [e.payload["natural"] for e in attack_events] == [15, 12]
    assert all(e.payload["hit"] for e in attack_events)

    damage_events = [e for e in state.events if e.type == "damage_dealt"]
    assert len(damage_events) == 2
    assert [e.payload["amount"] for e in damage_events] == [5, 6]
    assert state.characters["thorin"].hp == 30 - 5 - 6


def test_multiattack_rolls_no_sub_attacks_against_an_already_dead_target() -> None:
    # Regression guard for the "stop early if the target dies partway
    # through" behavior in _resolve_multiattack - poking is_dead directly
    # (same "poke state" pattern test_turn_engine_conditions.py already
    # uses) proves the per-sub-attack death check actually runs before the
    # very first roll, not just between later ones. No rng values supplied:
    # if either Bite or Claws rolled anyway, _FixedRandom would raise.
    state = _build_multiattack_state([])
    state.characters["thorin"].is_dead = True
    action = ParsedAction(
        actor="giant-badger_1", verb="attack", target="thorin", raw_text="the badger attacks"
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert not any(e.type == "attack_roll" for e in state.events)


# --- Ranged-while-engaged disadvantage --------------------------------------


def test_ranged_attack_gets_disadvantage_when_attacker_is_engaged() -> None:
    # thorin(0,1) shoots goblin_1(2,1) with a longbow (10ft away - within
    # normal range, no long-range disadvantage) while goblin_2 has closed to
    # melee range of thorin (repositioned onto (1,1), 5ft/1 square away) -
    # per SRD's "Ranged Attacks in Close Combat," that alone imposes
    # disadvantage. Two d20s [15, 3]: disadvantage keeps the lower (3).
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_2"].position = Position(x=1, y=1)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longbow",
        raw_text="I shoot while they close in",
    )
    resolve_action(state, action, _FixedRandom([15, 3]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 3


def test_ranged_attack_has_no_disadvantage_when_attacker_is_not_engaged() -> None:
    # Regression guard: without any hostile within 5ft, the same shot rolls
    # a single, unmodified d20 (only one value queued for the attack roll
    # itself - a disadvantage pair would need two, and _FixedRandom would
    # raise if resolve_attack tried to consume one that wasn't there). A
    # natural 15 hits, so one more value covers the resulting damage die.
    state = _build_demo_state(_INITIATIVE)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longbow",
        raw_text="I take aim and shoot",
    )
    resolve_action(state, action, _FixedRandom([15, 5]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 15


def test_melee_attack_is_unaffected_by_the_engaged_disadvantage_check() -> None:
    # A melee weapon has no "long" range tier at all (weapon_range_feet
    # returns None for it), so the engaged check - gated on is_ranged - must
    # never fire for one, even with a hostile adjacent to both combatants.
    state = _build_demo_state(_INITIATIVE)
    state.characters["goblin_1"].position = state.characters["thorin"].position
    state.characters["goblin_2"].position = state.characters["thorin"].position
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        item_or_spell="longsword",
        raw_text="I swing my sword",
    )
    # A natural 15 hits, so one more value covers the resulting damage die.
    resolve_action(state, action, _FixedRandom([15, 5]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["natural"] == 15
