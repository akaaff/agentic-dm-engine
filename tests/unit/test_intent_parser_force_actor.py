"""_force_actor (graph/nodes/intent_parser.py) is a pure, deterministic
post-processing step - fully testable offline, unlike the node's own
LLM-calling behavior (see tests/llm/test_intent_parser.py's
test_intent_parser_forces_the_real_actor_id_even_for_a_pathological_name for
the live round trip this exists to patch up).
"""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.graph.nodes.intent_parser import _force_actor


def _action(**overrides: object) -> ParsedAction:
    defaults: dict[str, object] = {
        "actor": "asssssass",
        "verb": "attack",
        "target": "goblin_1",
        "raw_text": "I attack goblin_1",
    }
    defaults.update(overrides)
    return ParsedAction.model_validate(defaults)


def test_overrides_a_mismatched_actor_to_the_expected_one() -> None:
    action = _action(actor="assssssass")  # one extra "s" - the real repro

    forced = _force_actor(action, "asssssass")

    assert forced.actor == "asssssass"
    # Nothing else about the parsed action should change.
    assert forced.verb == "attack"
    assert forced.target == "goblin_1"


def test_leaves_an_already_correct_actor_unchanged() -> None:
    action = _action(actor="asssssass")

    forced = _force_actor(action, "asssssass")

    assert forced is action
