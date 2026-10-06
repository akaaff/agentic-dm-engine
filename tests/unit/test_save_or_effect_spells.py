"""Issue #96 (class playtest): Charm Person, Command, Entangle, Faerie Fire,
Hideous Laughter, Grease and Animal Friendship rolled the saving throw and then
did nothing to a target that failed it. They now apply their condition."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import has_condition
from src.engine.encounter import monster_to_character
from src.engine.events import Event
from src.engine.position import BattleMap, Position
from src.engine.rules import condition_attack_advantage
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, _end_concentration, resolve_action
from src.graph.nodes.narrator import _event_line


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _caster() -> Character:
    return create_character(
        character_id="elara",
        name="Elara",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "sleep", "burning-hands"],
        chosen_equipment=["dagger"],
        position=Position(x=0, y=0),
    )


def _state(spell: str, monsters: list[tuple[str, str]]) -> GameState:
    srd = load_srd()
    caster = _caster()
    caster.prepared_spells.append(spell)
    caster.spell_slots[1] = 2
    chars = {"elara": caster}
    for i, (index, cid) in enumerate(monsters):
        chars[cid] = monster_to_character(srd.monsters[index], cid, Position(x=2, y=i))
    return GameState(
        encounter_id="t",
        characters=chars,
        turn_order=list(chars),
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10, height=10, terrain=[["floor"] * 10 for _ in range(10)], spawn_points={}
        ),
    )


def _cast(state: GameState, spell: str, targets: list[str], d20: int) -> None:
    resolve_action(
        state,
        ParsedAction(
            actor="elara",
            verb="cast_spell",
            target=targets[0],
            targets=targets,
            item_or_spell=spell,
            raw_text="x",
        ),
        _FixedRandom([d20] * len(targets)),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("spell", "conditions"),
    [
        ("charm-person", ["charmed"]),
        ("entangle", ["restrained"]),
        ("faerie-fire", ["outlined"]),
        ("hideous-laughter", ["incapacitated", "prone"]),
        ("grease", ["prone"]),
        ("command", ["incapacitated"]),
    ],
)
def test_a_failed_save_applies_the_spells_condition(spell: str, conditions: list[str]) -> None:
    state = _state(spell, [("goblin", "goblin_1")])
    _cast(state, spell, ["goblin_1"], d20=1)  # a natural 1 always fails the save
    goblin = state.characters["goblin_1"]
    for name in conditions:
        assert has_condition(goblin, name), name  # type: ignore[arg-type]
    applied = [e.payload["condition"] for e in state.events if e.type == "condition_applied"]
    assert sorted(applied) == sorted(conditions)


@pytest.mark.parametrize(
    "spell", ["charm-person", "entangle", "faerie-fire", "hideous-laughter", "grease", "command"]
)
def test_a_passed_save_applies_nothing(spell: str) -> None:
    state = _state(spell, [("goblin", "goblin_1")])
    _cast(state, spell, ["goblin_1"], d20=20)
    assert state.characters["goblin_1"].conditions == []


def test_entangle_restrains_every_failing_target_independently() -> None:
    state = _state("entangle", [("goblin", "goblin_1"), ("goblin", "goblin_2")])
    resolve_action(
        state,
        ParsedAction(
            actor="elara",
            verb="cast_spell",
            target="goblin_1",
            targets=["goblin_1", "goblin_2"],
            item_or_spell="entangle",
            raw_text="x",
        ),
        _FixedRandom([1, 20]),  # type: ignore[arg-type]
    )
    assert has_condition(state.characters["goblin_1"], "restrained")
    assert not has_condition(state.characters["goblin_2"], "restrained")


def test_animal_friendship_only_charms_beasts() -> None:
    state = _state("animal-friendship", [("wolf", "wolf_1"), ("goblin", "goblin_1")])
    _cast(state, "animal-friendship", ["wolf_1", "goblin_1"], d20=1)
    assert has_condition(state.characters["wolf_1"], "charmed")
    assert not has_condition(state.characters["goblin_1"], "charmed")


def test_a_charmed_creature_cannot_attack_its_charmer() -> None:
    state = _state("charm-person", [("goblin", "goblin_1")])
    _cast(state, "charm-person", ["goblin_1"], d20=1)
    state.current_turn = state.turn_order.index("goblin_1")
    with pytest.raises(TurnEngineError, match="charmed"):
        resolve_action(
            state,
            ParsedAction(actor="goblin_1", verb="attack", target="elara", raw_text="x"),
            _FixedRandom([15, 3]),  # type: ignore[arg-type]
        )


def test_outlined_targets_are_easier_to_hit() -> None:
    state = _state("faerie-fire", [("goblin", "goblin_1")])
    elara, goblin = state.characters["elara"], state.characters["goblin_1"]
    assert not condition_attack_advantage(elara, goblin, 10)
    _cast(state, "faerie-fire", ["goblin_1"], d20=1)
    assert condition_attack_advantage(elara, goblin, 10)


def test_concentration_ending_strips_the_effect() -> None:
    # Entangle is a concentration spell: its condition is tagged with the
    # caster and spell, so ending concentration removes it from the target.
    state = _state("entangle", [("goblin", "goblin_1")])
    _cast(state, "entangle", ["goblin_1"], d20=1)
    assert has_condition(state.characters["goblin_1"], "restrained")
    _end_concentration(state, state.characters["elara"], load_srd())
    assert not has_condition(state.characters["goblin_1"], "restrained")


def test_the_narrator_is_told_a_failed_save_means_the_spell_took_hold() -> None:
    def line(success: bool) -> str:
        return _event_line(
            Event(
                round=1,
                turn_index=0,
                actor="elara",
                type="saving_throw",
                payload={
                    "kind": "spell_save",
                    "spell": "Entangle",
                    "target": "goblin_1",
                    "ability": "STR",
                    "success": success,
                },
            )
        )

    assert "FAILS the save" in line(False) and "takes full hold" in line(False)
    assert "RESISTS" in line(True) and "no effect" in line(True)
