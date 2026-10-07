"""Issue #55: no-combat utility spells. The engine logs the cast; a model rules on it, using only
that spell's guideline (looked up per cast from data/spells/utility_guidelines.yaml)."""

from __future__ import annotations

from typing import Any

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state
from src.engine.events import Event
from src.engine.rules import spell_mechanic
from src.engine.srd_loader import load_srd
from src.engine.state import GameState
from src.engine.turn_engine import _SPECIAL_CAST_SPELLS, TurnEngineError, resolve_action
from src.engine.utility_spells import guideline_for, is_utility_spell, utility_spell_indices
from src.graph.nodes import spell_adjudicator as adjudicator
from src.graph.nodes.narrator import _event_line
from src.graph.nodes.spell_adjudicator import (
    SpellRuling,
    _build_prompt,
    adjudicate_utility_spell,
    spell_adjudicator_node,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _state(*extra_spells: str) -> GameState:
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
    wizard.prepared_spells += list(extra_spells)
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
    return build_encounter_state(
        build_demo_encounter(),
        [wizard, fighter],
        _FixedRandom([20, 10, 5, 3]),  # type: ignore[arg-type]
    )


def _cast(state: GameState, spell: str, text: str) -> None:
    action = ParsedAction(actor="elrond", verb="cast_spell", item_or_spell=spell, raw_text=text)
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_every_guideline_belongs_to_a_real_unsupported_low_level_spell() -> None:
    srd = load_srd()
    indices = utility_spell_indices()
    assert len(indices) == 40
    for index in indices:
        spell = srd.spells[index]
        assert spell["level"] <= 2, index
        assert spell_mechanic(spell) is None, f"{index} already has a real mechanic"
        assert index not in _SPECIAL_CAST_SPELLS, index
        assert (guideline_for(index) or "").strip(), index


def test_a_guideline_is_looked_up_for_one_spell_at_a_time() -> None:
    assert is_utility_spell("mage-hand") and not is_utility_spell("fireball")
    assert guideline_for("fireball") is None
    assert "10 lb" in (guideline_for("mage-hand") or "")


def test_a_cantrip_is_logged_with_what_the_player_attempted_and_costs_nothing() -> None:
    state = _state("mage-hand")
    before = dict(state.characters["elrond"].spell_slots)
    _cast(state, "Mage Hand", "I use mage hand to lift the key from the table")
    cast = next(e for e in state.events if e.type == "spell_cast")
    assert cast.payload["utility"] is True
    assert cast.payload["spell_index"] == "mage-hand"
    assert cast.payload["attempt"] == "I use mage hand to lift the key from the table"
    assert state.characters["elrond"].spell_slots == before


def test_a_leveled_spell_spends_a_slot_but_a_ritual_does_not() -> None:
    state = _state("disguise-self", "detect-magic")
    elrond = state.characters["elrond"]
    start = elrond.spell_slots[1]
    _cast(state, "Detect Magic", "I cast detect magic")  # a ritual
    assert elrond.spell_slots[1] == start
    state.current_turn = state.turn_order.index("elrond")
    _cast(state, "Disguise Self", "I disguise myself as a guard")
    assert elrond.spell_slots[1] == start - 1


def test_a_concentration_utility_spell_makes_the_caster_concentrate() -> None:
    state = _state("detect-magic")
    _cast(state, "Detect Magic", "I cast detect magic")
    assert state.characters["elrond"].concentrating_on == "Detect Magic"


def test_an_unprepared_or_unaffordable_utility_spell_is_still_refused() -> None:
    state = _state()
    with pytest.raises(TurnEngineError, match="hasn't prepared"):
        _cast(state, "Disguise Self", "I disguise myself")
    state = _state("disguise-self")
    state.characters["elrond"].spell_slots[1] = 0
    with pytest.raises(TurnEngineError, match="no level-1 spell slots"):
        _cast(state, "Disguise Self", "I disguise myself")


def _utility_event(state: GameState, spell: str, index: str, attempt: str) -> Event:
    event = Event(
        round=1,
        turn_index=0,
        actor="elrond",
        type="spell_cast",
        payload={"spell": spell, "spell_index": index, "utility": True, "attempt": attempt},
    )
    state.events.append(event)
    return event


def test_the_prompt_carries_only_the_cast_spells_guideline_and_text() -> None:
    state = _state()
    event = _utility_event(state, "Mage Hand", "mage-hand", "I pick the lock with my hand")
    prompt = _build_prompt(state, event)
    assert (guideline_for("mage-hand") or "?") in prompt
    assert "spectral, floating hand" in prompt or "spectral" in prompt  # the SRD text
    assert "I pick the lock with my hand" in prompt
    assert "Goblin 1 (enemy)" in prompt  # the scene
    assert (guideline_for("knock") or "?") not in prompt
    assert (guideline_for("detect-magic") or "?") not in prompt


def test_the_verdict_is_recorded_as_an_event_the_narrator_can_follow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_chat(messages: list[dict[str, str]], schema: type, **_: Any) -> SpellRuling:
        seen["prompt"] = messages[0]["content"]
        return SpellRuling(outcome="failure", ruling="The hand can't pick a lock.")

    monkeypatch.setattr(adjudicator, "chat_structured", fake_chat)
    state = _state()
    _utility_event(state, "Mage Hand", "mage-hand", "pick the lock")
    spell_adjudicator_node(
        {"game_state": state, "events_before": 0}  # type: ignore[typeddict-item]
    )
    ruling = next(e for e in state.events if e.type == "spell_ruling")
    assert ruling.payload["outcome"] == "failure"
    assert ruling.payload["ruling"] == "The hand can't pick a lock."
    assert ruling.actor == "elrond"
    line = _event_line(ruling, {"elrond": "Elrond the wizard"})
    assert "FAILED" in line and "can't pick a lock" in line and "final" in line


def test_only_utility_casts_are_ruled_on(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> SpellRuling:
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(adjudicator, "chat_structured", boom)
    state = _state()
    state.events.append(
        Event(
            round=1,
            turn_index=0,
            actor="elrond",
            type="spell_cast",
            payload={"spell": "Magic Missile", "darts": 3},
        )
    )
    spell_adjudicator_node({"game_state": state, "events_before": 0})  # type: ignore[typeddict-item]
    assert not any(e.type == "spell_ruling" for e in state.events)


def test_a_model_failure_rules_a_plain_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*_: Any, **__: Any) -> SpellRuling:
        raise ConnectionError("ollama is down")

    monkeypatch.setattr(adjudicator, "chat_structured", down)
    state = _state()
    event = _utility_event(state, "Mage Hand", "mage-hand", "x")
    verdict = adjudicate_utility_spell(state, event)
    assert verdict.outcome == "success" and verdict.ruling == ""


def test_a_ruling_without_a_sentence_still_reads_cleanly_to_the_narrator() -> None:
    event = Event(
        round=1,
        turn_index=0,
        actor="elrond",
        type="spell_ruling",
        payload={"spell": "Mage Hand", "outcome": "success", "ruling": ""},
    )
    assert "WORKED" in _event_line(event, {"elrond": "Elrond"})
