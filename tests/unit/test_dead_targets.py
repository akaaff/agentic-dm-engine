"""Issue #75 (class playtest): attacks and spells at an already-dead creature
used to be accepted - a weapon attack silently produced no events and ended
the turn, a spell burned its slot for `dmg 0` - and the parser prompts still
ranked the corpse, so "the nearest goblin" resolved to it."""

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action
from src.graph.nodes.intent_parser import build_intent_parser_prompt


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _party() -> list[Character]:
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
        chosen_prepared_spells=["magic-missile", "burning-hands", "mage-armor"],
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


def _state_with_dead_goblin(actor: str) -> GameState:
    state = build_encounter_state(
        build_demo_encounter(),
        _party(),
        _FixedRandom([18, 10, 8, 3]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index(actor)
    goblin = state.characters["goblin_1"]
    goblin.hp = 0
    goblin.is_dead = True
    return state


def test_attacking_a_dead_creature_is_rejected_without_ending_the_turn() -> None:
    state = _state_with_dead_goblin("thorin")
    events_before = len(state.events)

    with pytest.raises(TurnEngineError, match="already dead"):
        resolve_action(
            state,
            ParsedAction(actor="thorin", verb="attack", target="goblin_1", raw_text="x"),
            _FixedRandom([15]),  # type: ignore[arg-type]
        )

    assert state.turn_order[state.current_turn] == "thorin"
    assert len(state.events) == events_before


def test_casting_at_a_dead_creature_is_rejected_and_keeps_the_slot() -> None:
    state = _state_with_dead_goblin("elrond")
    elrond = state.characters["elrond"]
    slots_before = dict(elrond.spell_slots)

    with pytest.raises(TurnEngineError, match="already dead"):
        resolve_action(
            state,
            ParsedAction(
                actor="elrond",
                verb="cast_spell",
                target="goblin_1",
                item_or_spell="magic missile",
                raw_text="x",
            ),
            _FixedRandom([5, 5, 5]),  # type: ignore[arg-type]
        )

    assert elrond.spell_slots == slots_before


def test_sleep_naming_only_a_corpse_is_rejected() -> None:
    state = _state_with_dead_goblin("elrond")
    state.characters["elrond"].prepared_spells.append("sleep")
    slots_before = dict(state.characters["elrond"].spell_slots)

    with pytest.raises(TurnEngineError, match="already dead"):
        resolve_action(
            state,
            ParsedAction(
                actor="elrond",
                verb="cast_spell",
                target="goblin_1",
                targets=["goblin_1"],
                item_or_spell="sleep",
                raw_text="x",
            ),
            _FixedRandom([5] * 5),  # type: ignore[arg-type]
        )

    assert state.characters["elrond"].spell_slots == slots_before


def test_a_downed_living_pc_can_still_be_attacked_by_a_monster() -> None:
    # Only the *dead* are protected: an unconscious PC (hp 0, not dead) must
    # stay targetable - the auto-crit/death-save rules depend on it.
    state = _state_with_dead_goblin("goblin_2")
    thorin_pos = state.characters["thorin"].position
    state.characters["goblin_2"].position = Position(x=thorin_pos.x + 1, y=thorin_pos.y)
    thorin = state.characters["thorin"]
    thorin.hp = 0  # downed, not dead
    assert not thorin.is_dead
    resolve_action(
        state,
        ParsedAction(actor="goblin_2", verb="attack", target="thorin", raw_text="x"),
        _FixedRandom([15, 4]),  # type: ignore[arg-type]
    )  # must not raise


def test_parser_prompt_does_not_list_the_dead() -> None:
    state = _state_with_dead_goblin("thorin")
    prompt = build_intent_parser_prompt(
        {
            "game_state": state,
            "raw_text": "I attack the nearest goblin",
            "parsed_action": None,
            "events_before": 0,
            "round_before": 1,
            "narration": None,
            "scene_image_url": None,
        }
    )
    assert "goblin_1" not in prompt
    assert "goblin_2" in prompt
