"""Phase 9F: Multiattack - a monster's Multiattack action rolls each of its
named sub-attacks as its own attack_roll event, within one `attack` verb/turn.
"""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


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
