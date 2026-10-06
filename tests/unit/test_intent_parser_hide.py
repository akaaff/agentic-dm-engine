"""_hide_as_cunning_action (graph/nodes/intent_parser.py, issue #84): a Rogue
of level 2+ with a free bonus action who says "I hide" gets the bonus-action
Hide, so the action is still there for the attack the hiding is for. Pure and
deterministic, so tested offline; the real model's phrasing is live-verified
separately."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.nodes.intent_parser import _postprocess_action


def _actor(class_index: str = "rogue", level: int = 2, **overrides: object) -> Character:
    defaults: dict[str, object] = {
        "id": "fenwick",
        "name": "Fenwick",
        "race": "Halfling",
        "class_": class_index.title(),
        "class_index": class_index,
        "level": level,
        "background": "Acolyte",
        "is_pc": True,
        "hp": 9,
        "max_hp": 9,
        "ac": 14,
        "position": Position(x=0, y=0),
        "stats": {"STR": 8, "DEX": 17, "CON": 12, "INT": 10, "WIS": 13, "CHA": 14},
        "speed": 25,
        "proficiency_bonus": 2,
    }
    defaults.update(overrides)
    return Character.model_validate(defaults)


def _run(actor: Character) -> ParsedAction:
    state = GameState(
        encounter_id="hide_parse_test",
        characters={actor.id: actor},
        turn_order=[actor.id],
        current_turn=0,
        round=1,
        battle_map=None,
    )
    hide = ParsedAction(actor=actor.id, verb="hide", raw_text="I hide")
    return _postprocess_action(hide, actor.id, state)


def test_a_level_two_rogue_hides_as_a_bonus_action() -> None:
    action = _run(_actor())
    assert action.verb == "cunning_action"
    assert action.params == {"action": "hide"}


def test_a_level_one_rogue_keeps_the_full_action_hide() -> None:
    assert _run(_actor(level=1)).verb == "hide"


def test_a_rogue_whose_bonus_action_is_spent_keeps_the_full_action_hide() -> None:
    assert _run(_actor(bonus_action_used=True)).verb == "hide"


def test_other_classes_keep_the_full_action_hide() -> None:
    assert _run(_actor("ranger")).verb == "hide"
