"""_strip_invalid_smite (graph/nodes/intent_parser.py) is a pure,
deterministic post-processing step - fully testable offline, unlike the
node's own LLM-calling behavior (see tests/llm/test_intent_parser.py's live
golden-set cases for the real round trip issue #49 exists to patch up).
"""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.nodes.intent_parser import _strip_invalid_smite


def _character(**overrides: object) -> Character:
    defaults: dict[str, object] = {
        "id": "thorin",
        "name": "Thorin",
        "race": "Human",
        "class_": "Barbarian",
        "class_index": "barbarian",
        "background": "Acolyte",
        "is_pc": True,
        "hp": 15,
        "max_hp": 15,
        "ac": 13,
        "position": Position(x=0, y=0),
        "stats": {"STR": 16, "DEX": 12, "CON": 15, "INT": 8, "WIS": 10, "CHA": 8},
        "speed": 30,
        "proficiency_bonus": 2,
    }
    defaults.update(overrides)
    return Character.model_validate(defaults)


def _game_state(actor: Character) -> GameState:
    return GameState(
        encounter_id="smite_strip_test",
        characters={actor.id: actor},
        turn_order=[actor.id],
        current_turn=0,
        round=1,
    )


def test_strips_a_hallucinated_smite_from_a_non_paladin() -> None:
    actor = _character()
    game_state = _game_state(actor)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        params={"smite_slot_level": 1},
        raw_text="I smash goblin_1 with my warhammer",
    )

    stripped = _strip_invalid_smite(action, game_state)

    assert "smite_slot_level" not in stripped.params
    # Nothing else about the parsed action should change.
    assert stripped.verb == "attack"
    assert stripped.target == "goblin_1"


def test_leaves_a_real_paladins_smite_untouched() -> None:
    actor = _character(class_index="paladin", class_="Paladin")
    game_state = _game_state(actor)
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        target="goblin_1",
        params={"smite_slot_level": 2},
        raw_text="I channel my divine power into the strike",
    )

    result = _strip_invalid_smite(action, game_state)

    assert result is action


def test_leaves_an_attack_with_no_smite_param_untouched() -> None:
    actor = _character()
    game_state = _game_state(actor)
    action = ParsedAction(
        actor="thorin", verb="attack", target="goblin_1", raw_text="I attack goblin_1"
    )

    result = _strip_invalid_smite(action, game_state)

    assert result is action


def test_ignores_a_non_attack_verb() -> None:
    actor = _character()
    game_state = _game_state(actor)
    action = ParsedAction(
        actor="thorin",
        verb="cast_spell",
        target="goblin_1",
        params={"smite_slot_level": 1},
        raw_text="I cast a spell",
    )

    result = _strip_invalid_smite(action, game_state)

    assert result is action
