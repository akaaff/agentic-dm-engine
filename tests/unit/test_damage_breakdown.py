"""A hit's damage is explained on its event: the named parts that add up to it (dice, ability
modifier, Sneak Attack, ...), why Sneak Attack did or didn't apply, and - on the damage event -
what changed the number on its way in. Prompted by a live one-shot ("how did Fenwick kill a wolf
with a d4 dagger?") that the log couldn't answer: Sneak Attack's dice were in the payload but
nowhere in the damage the player saw.

Goblin AC is 10 and HP 200 throughout: a d20 of 12 always hits and nothing dies, so the dice
sequences only have to cover the d20, the weapon die and the rider dice, in the engine's order."""

from __future__ import annotations

from typing import Any

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import _pc_attack_params, _resolve_single_attack, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _rogue() -> Character:
    rogue = create_character(
        character_id="finn",
        name="Finn",
        race_index="human",
        class_index="rogue",
        background_index="acolyte",
        base_ability_scores={"STR": 10, "DEX": 15, "CON": 13, "INT": 12, "WIS": 14, "CHA": 8},
        chosen_skills=["skill-stealth", "skill-acrobatics", "skill-perception", "skill-deception"],
        chosen_equipment=["dagger"],
        chosen_expertise=["skill-stealth", "skill-acrobatics"],
    )
    rogue.equipped_weapons = ["dagger"]
    rogue.position = Position(x=0, y=0)
    return rogue


def _fighter(x: int) -> Character:
    fighter = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    fighter.position = Position(x=x, y=0)
    return fighter


def _goblin() -> Character:
    return Character(
        id="goblin_1",
        name="Goblin 1",
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=200,
        max_hp=200,
        ac=10,
        level=1,
        position=Position(x=1, y=0),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _state(ally_x: int) -> GameState:
    rogue, fighter, goblin = _rogue(), _fighter(ally_x), _goblin()
    return GameState(
        encounter_id="damage_breakdown_test",
        characters={"finn": rogue, "thorin": fighter, "goblin_1": goblin},
        turn_order=["finn", "thorin", "goblin_1"],
        current_turn=0,
        round=1,
    )


def _stab(state: GameState, dice: list[int]) -> dict[str, Any]:
    action = ParsedAction(
        actor="finn", verb="attack", target="goblin_1", item_or_spell="dagger", raw_text="x"
    )
    resolve_action(state, action, _FixedRandom(dice))  # type: ignore[arg-type]
    return next(e for e in state.events if e.type == "attack_roll").payload


def _total(breakdown: list[Any]) -> int:
    return sum(value for _, value in breakdown)


def test_a_sneak_attack_hit_lists_every_part_and_they_add_up() -> None:
    state = _state(ally_x=2)  # Thorin stands beside the goblin
    # d20 12, dagger d4 4, sneak d6 4: the live one-shot (4 + DEX 3 + 4 = 11)
    payload = _stab(state, [12, 4, 4])
    assert [list(e) for e in payload["damage_breakdown"]] == [
        ["Dagger 1d4", 4],
        ["DEX mod", 3],
        ["Sneak Attack 1d6", 4],
    ]
    assert _total(payload["damage_breakdown"]) == 11
    dealt = next(e for e in state.events if e.type == "damage_dealt").payload
    assert dealt["amount"] == 11
    assert "adjustments" not in dealt  # nothing changed the hit on the way in


def test_sneak_attack_says_what_triggered_it() -> None:
    payload = _stab(_state(ally_x=2), [12, 4, 4])
    sneak = payload["sneak_attack"]
    assert sneak["applied"] is True
    assert sneak["dice"] == "1d6" and sneak["damage"] == 4
    assert sneak["triggers"] == ["Thorin is next to the target"]


def test_sneak_attack_says_why_it_did_not_apply() -> None:
    far = _state(ally_x=9)  # the ally is nowhere near the goblin, and the roll has no advantage
    payload = _stab(far, [12, 4])
    assert payload["sneak_attack"] == {
        "applied": False,
        "reason": "no advantage and no ally next to the target",
    }
    assert _total(payload["damage_breakdown"]) == 7  # 4 + DEX 3, no rider
    assert "Sneak Attack" not in " ".join(label for label, _ in payload["damage_breakdown"])


def test_sneak_attack_says_when_it_was_already_used_this_turn() -> None:
    state = _state(ally_x=2)
    rogue, goblin = state.characters["finn"], state.characters["goblin_1"]
    rogue.sneak_attack_used_this_turn = True
    params = _pc_attack_params(rogue, "dagger", load_srd())
    _resolve_single_attack(state, rogue, goblin, params, _FixedRandom([12, 4]), load_srd())  # type: ignore[arg-type]
    payload = next(e for e in state.events if e.type == "attack_roll").payload
    assert payload["sneak_attack"] == {"applied": False, "reason": "already used this turn"}


def test_only_rogues_get_a_sneak_attack_entry() -> None:
    state = _state(ally_x=2)
    action = ParsedAction(
        actor="finn", verb="attack", target="goblin_1", item_or_spell="dagger", raw_text="x"
    )
    state.current_turn = 1  # Thorin's turn
    thorin_hit = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", item_or_spell="longsword", raw_text="x"
    )
    resolve_action(state, thorin_hit, _FixedRandom([12, 5]))  # type: ignore[arg-type]
    payload = next(e for e in state.events if e.type == "attack_roll").payload
    assert "sneak_attack" not in payload
    assert action  # (Finn's own attack is covered above)


def test_a_critical_hit_doubles_the_dice_in_the_breakdown() -> None:
    state = _state(ally_x=9)
    # natural 20, then 2d4 (3, 2), no sneak (no trigger): 5 + DEX 3
    payload = _stab(state, [20, 3, 2])
    assert list(payload["damage_breakdown"][0]) == ["Dagger 2d4 (critical)", 5]
    assert _total(payload["damage_breakdown"]) == 8


def test_overkill_is_explained_on_the_damage_event() -> None:
    state = _state(ally_x=9)
    state.characters["goblin_1"].hp = 5
    _stab(state, [12, 4])  # 4 + 3 = 7 against 5 HP
    dealt = next(e for e in state.events if e.type == "damage_dealt").payload
    assert dealt["amount"] == 5 and dealt["rolled"] == 7
    assert dealt["adjustments"] == ["only 5 HP left to lose"]


def test_resistance_is_explained_on_the_damage_event() -> None:
    srd = load_srd()
    state = _state(ally_x=9)
    ghost = state.characters["goblin_1"]
    ghost.monster_index = "ghost"  # resistant to nonmagical piercing
    assert srd.monsters["ghost"]
    _stab(state, [12, 4])  # 7 piercing, halved to 3
    dealt = next(e for e in state.events if e.type == "damage_dealt").payload
    assert dealt["rolled"] == 7 and dealt["amount"] == 3
    assert any("resistant to piercing" in a for a in dealt["adjustments"])


def _wizard_state() -> GameState:
    wizard = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "mage-armor", "sleep"],
        chosen_equipment=["dagger"],
    )
    wizard.position = Position(x=0, y=0)
    goblin = _goblin()
    goblin.position = Position(x=4, y=0)  # 20ft: in range for a cantrip
    return GameState(
        encounter_id="spell_breakdown_test",
        characters={"elrond": wizard, "goblin_1": goblin},
        turn_order=["elrond", "goblin_1"],
        current_turn=0,
        round=1,
    )


def test_an_attack_spell_shows_its_roll_and_damage_parts() -> None:
    state = _wizard_state()
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        item_or_spell="Fire Bolt",
        target="goblin_1",
        raw_text="x",
    )
    resolve_action(state, action, _FixedRandom([12, 6]))  # type: ignore[arg-type]
    payload = next(e for e in state.events if e.type == "spell_cast").payload
    assert payload["natural"] == 12 and payload["target_ac"] == 10 and payload["hit"] is True
    assert [list(e) for e in payload["damage_breakdown"]] == [["Fire Bolt 1d10", 6]]
    dealt = next(e for e in state.events if e.type == "damage_dealt").payload
    assert dealt["amount"] == 6


def test_a_save_spell_reports_what_the_dice_made_before_a_save_halved_it() -> None:
    state = _wizard_state()
    state.characters["elrond"].known_spells = []
    state.characters["elrond"].prepared_spells.append("burning-hands")
    state.characters["elrond"].spell_slots = {1: 2}
    state.characters["goblin_1"].position = Position(x=1, y=0)
    action = ParsedAction(
        actor="elrond",
        verb="cast_spell",
        item_or_spell="Burning Hands",
        target="goblin_1",
        targets=["goblin_1"],
        raw_text="x",
    )
    # goblin DEX save: d20 18 passes DC 12; 3d6 = 4 + 5 + 3 = 12, halved to 6
    resolve_action(state, action, _FixedRandom([18, 4, 5, 3]))  # type: ignore[arg-type]
    payload = next(
        e for e in state.events if e.type == "spell_cast" and "save_success" in e.payload
    ).payload
    assert payload["save_success"] is True
    assert payload["damage_rolled"] == 12 and payload["damage"] == 6
    assert payload["damage_dice"] == "3d6"
